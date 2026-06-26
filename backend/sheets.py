"""
sheets.py — Google Sheets Research Archive
Append-only. Immutable. Every match, vote, model run, error saved here.
Uses a queue so failed writes are retried without blocking the pipeline.

CREDENTIALS FIX (Session 11):
GOOGLE_SHEETS_CREDENTIALS env var now holds the full JSON content of the
service account key (not a file path). This works on Render where we can't
upload files. Copy the entire contents of your .json file into the Render
env var as a single line (or multi-line — both work).
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

# ─────────────────────────────────────────────
# CONFIG
# Two env vars needed:
#   GOOGLE_SHEETS_CREDENTIALS — full JSON content of service account key
#   GOOGLE_SHEETS_ID          — the spreadsheet ID from the URL
# ─────────────────────────────────────────────

GOOGLE_SHEETS_CREDENTIALS_JSON = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SHEET_TABS = {
    "votes":      "Votes",
    "matches":    "Matches",
    "model":      "Model",
    "sources":    "Sources",
    "errors":     "Errors",
    "commentary": "Commentary",
}

_client: Optional[gspread.Client] = None
_spreadsheet: Optional[gspread.Spreadsheet] = None
_sheets_available: bool = False  # set True once first connection succeeds

_write_queue: asyncio.Queue = asyncio.Queue()
_queue_task: Optional[asyncio.Task] = None


# ─────────────────────────────────────────────
# CONNECTION
# ─────────────────────────────────────────────

def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        if not GOOGLE_SHEETS_CREDENTIALS_JSON:
            raise RuntimeError(
                "GOOGLE_SHEETS_CREDENTIALS env var is not set. "
                "Paste the full JSON content of your service account key."
            )
        if not GOOGLE_SHEETS_ID:
            raise RuntimeError(
                "GOOGLE_SHEETS_ID env var is not set. "
                "Copy the spreadsheet ID from your Google Sheets URL."
            )

        # Parse JSON content from env var (handles both compact and pretty-printed)
        try:
            creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"GOOGLE_SHEETS_CREDENTIALS is not valid JSON: {e}. "
                "Make sure you pasted the entire file contents, not a file path."
            )

        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        _client = gspread.authorize(creds)
        logger.info("Google Sheets client authenticated successfully")
    return _client


def _get_spreadsheet() -> gspread.Spreadsheet:
    global _spreadsheet
    if _spreadsheet is None:
        client = _get_client()
        _spreadsheet = client.open_by_key(GOOGLE_SHEETS_ID)
        logger.info(f"Connected to spreadsheet: {_spreadsheet.title}")
    return _spreadsheet


def _get_worksheet(tab: str) -> gspread.Worksheet:
    ss = _get_spreadsheet()
    name = SHEET_TABS[tab]
    try:
        return ss.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=10000, cols=60)
        logger.info(f"Created new worksheet: {name}")
        return ws


# ─────────────────────────────────────────────
# HEADER MANAGEMENT
# ─────────────────────────────────────────────

_headers_cache: dict[str, list[str]] = {}


def _ensure_headers(ws: gspread.Worksheet, row: dict) -> None:
    tab_name = ws.title
    existing = _headers_cache.get(tab_name)
    if existing is None:
        first_row = ws.row_values(1)
        existing = first_row if first_row else []
        _headers_cache[tab_name] = existing

    new_keys = [k for k in row.keys() if k not in existing]
    if new_keys:
        updated = existing + new_keys
        ws.update("1:1", [updated])
        _headers_cache[tab_name] = updated
        logger.debug(f"Added columns to {tab_name}: {new_keys}")


def _row_to_ordered_list(ws: gspread.Worksheet, row: dict) -> list:
    headers = _headers_cache.get(ws.title, [])
    return [_safe_cell(row.get(h, "")) for h in headers]


def _safe_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


# ─────────────────────────────────────────────
# WRITE (sync, runs in executor)
# ─────────────────────────────────────────────

def _write_row_sync(tab: str, row: dict) -> None:
    ws = _get_worksheet(tab)
    _ensure_headers(ws, row)
    ordered = _row_to_ordered_list(ws, row)
    ws.append_row(ordered, value_input_option="USER_ENTERED")


# ─────────────────────────────────────────────
# LOCAL FALLBACK (when Sheets unavailable)
# ─────────────────────────────────────────────

def _save_local_fallback(tab: str, row: dict) -> None:
    path = Path(f"fallback_{tab}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps({
            "tab": tab,
            "row": {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for k, v in row.items()},
            "_saved": datetime.now(timezone.utc).isoformat()
        }) + "\n")
    logger.warning(f"Row saved to local fallback: {path}")


# ─────────────────────────────────────────────
# QUEUE WORKER
# ─────────────────────────────────────────────

async def _queue_worker() -> None:
    global _sheets_available
    import alerts  # imported here to avoid circular imports

    logger.info("Google Sheets queue worker started.")
    loop = asyncio.get_event_loop()

    while True:
        tab, row, attempt = await _write_queue.get()
        try:
            await loop.run_in_executor(None, _write_row_sync, tab, row)
            _sheets_available = True
            logger.debug(f"Sheets write OK: tab={tab} attempt={attempt}")
        except RuntimeError as e:
            # Config error (missing env var) — log once, save locally, don't retry
            logger.warning(f"Sheets not configured: {e}")
            _save_local_fallback(tab, row)
            _write_queue.task_done()
            continue
        except Exception as e:
            logger.warning(f"Sheets write failed (attempt {attempt}): {e}")
            if attempt < 3:
                await asyncio.sleep(5 * attempt)
                await _write_queue.put((tab, row, attempt + 1))
            else:
                logger.error(f"Sheets write ABANDONED after 3 attempts: tab={tab} | {e}")
                try:
                    await alerts.alert_sheets_failed(str(e))
                except Exception:
                    pass
                _save_local_fallback(tab, row)
        finally:
            _write_queue.task_done()


# ─────────────────────────────────────────────
# START / STOP
# ─────────────────────────────────────────────

async def start_queue_worker() -> None:
    global _queue_task
    if _queue_task is None or _queue_task.done():
        _queue_task = asyncio.create_task(_queue_worker())
        logger.info("Sheets queue worker task created.")


async def stop_queue_worker() -> None:
    if _write_queue.qsize() > 0:
        logger.info(f"Draining {_write_queue.qsize()} pending Sheets writes...")
        await _write_queue.join()
    if _queue_task:
        _queue_task.cancel()


def queue_write(tab: str, row: dict) -> None:
    if tab not in SHEET_TABS:
        logger.error(f"Unknown sheet tab: {tab}")
        return
    _write_queue.put_nowait((tab, row, 1))


# Alias used by main.py
async def flush_queue() -> dict:
    return await manual_sync()


# ─────────────────────────────────────────────
# PUBLIC WRITE HELPERS
# ─────────────────────────────────────────────

def save_vote(vote: dict) -> None:
    row = {"saved_at_utc": datetime.now(timezone.utc), **vote}
    queue_write("votes", row)


def save_match(match: dict) -> None:
    row = {"saved_at_utc": datetime.now(timezone.utc), **match}
    queue_write("matches", row)


def save_model_run(run: dict) -> None:
    row = {"saved_at_utc": datetime.now(timezone.utc), **run}
    queue_write("model", row)


def save_source_health(health: dict) -> None:
    row = {"saved_at_utc": datetime.now(timezone.utc), **health}
    queue_write("sources", row)


def save_error(error: dict) -> None:
    row = {"saved_at_utc": datetime.now(timezone.utc), **error}
    queue_write("errors", row)


def save_commentary(commentary: dict) -> None:
    row = {"saved_at_utc": datetime.now(timezone.utc), **commentary}
    queue_write("commentary", row)


# ─────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────

async def health_check() -> dict:
    if not GOOGLE_SHEETS_CREDENTIALS_JSON or not GOOGLE_SHEETS_ID:
        return {
            "connected": False,
            "queue_depth": _write_queue.qsize(),
            "last_write_utc": None,
            "error": "GOOGLE_SHEETS_CREDENTIALS or GOOGLE_SHEETS_ID env var not set",
        }
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _get_spreadsheet)
        return {
            "connected": True,
            "queue_depth": _write_queue.qsize(),
            "last_write_utc": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }
    except Exception as e:
        return {
            "connected": False,
            "queue_depth": _write_queue.qsize(),
            "last_write_utc": None,
            "error": str(e),
        }


async def manual_sync() -> dict:
    pending = _write_queue.qsize()
    if pending > 0:
        logger.info(f"Manual Sheets sync triggered: {pending} rows pending.")
        await _write_queue.join()
    return {"flushed": pending}


# ─────────────────────────────────────────────
# MATCH RECORD BUILDER
# ─────────────────────────────────────────────

def build_match_record(
    match_id: str, model_version: int, phase: str, group: Optional[str],
    venue: str, kickoff_utc: datetime, home_team: str, away_team: str,
    home_squad: list, away_squad: list, home_manager: str, away_manager: str,
    home_fifa_rank: int, away_fifa_rank: int, home_elo: float, away_elo: float,
    home_win_pct: float, draw_pct: float, away_win_pct: float,
    predicted_scorer: str, predicted_score: str, confidence_range: str,
    training_matches_seen: int, final_score_home: int, final_score_away: int,
    winner: str, scorers: list, assisters: list, cards: list, substitutions: list,
    first_goal_minute: Optional[int], final_whistle_minute: int,
    injury_time_1h: int, injury_time_2h: int, went_to_et: bool,
    went_to_penalties: bool, total_duration_min: int, regulation_duration_min: int,
    ai_correct_winner: bool, ai_correct_scorer: bool, ai_confidence_at_lock: float,
    confidence_calibration_error: float, pre_match_brief: str,
    post_match_debrief: str, what_model_got_wrong: str, model_updates_after: dict,
    total_votes: int, verified_votes: int, vote_distribution: dict,
    crowd_correct_winner: bool, vote_timeline: list, votes_by_phase: dict,
    sources_used: list, sources_failed: list, fetch_cycles: int,
    parse_errors: int, hallucinations_caught: int, fallbacks_triggered: int,
    mlflow_run_id: str, accuracy_before: float, accuracy_after: float,
    improvement_pct: float, deploy_decision: bool, training_duration_s: float,
    feature_importances: dict, full_commentary_archive: list,
    key_moments: list, player_performance_ratings: dict,
) -> dict:
    return {
        "match_id": match_id, "model_version": model_version, "phase": phase,
        "group": group or "", "venue": venue, "kickoff_utc": kickoff_utc,
        "home_team": home_team, "away_team": away_team,
        "home_squad": home_squad, "away_squad": away_squad,
        "home_manager": home_manager, "away_manager": away_manager,
        "home_fifa_rank": home_fifa_rank, "away_fifa_rank": away_fifa_rank,
        "home_elo": home_elo, "away_elo": away_elo,
        "home_win_pct": home_win_pct, "draw_pct": draw_pct, "away_win_pct": away_win_pct,
        "predicted_scorer": predicted_scorer, "predicted_score": predicted_score,
        "confidence_range": confidence_range,
        "training_matches_seen": training_matches_seen,
        "final_score_home": final_score_home, "final_score_away": final_score_away,
        "winner": winner, "scorers": scorers, "assisters": assisters,
        "cards": cards, "substitutions": substitutions,
        "first_goal_minute": first_goal_minute,
        "final_whistle_minute": final_whistle_minute,
        "injury_time_1h": injury_time_1h, "injury_time_2h": injury_time_2h,
        "went_to_et": went_to_et, "went_to_penalties": went_to_penalties,
        "total_duration_min": total_duration_min,
        "regulation_duration_min": regulation_duration_min,
        "ai_correct_winner": ai_correct_winner, "ai_correct_scorer": ai_correct_scorer,
        "ai_confidence_at_lock": ai_confidence_at_lock,
        "confidence_calibration_error": confidence_calibration_error,
        "pre_match_brief": pre_match_brief, "post_match_debrief": post_match_debrief,
        "what_model_got_wrong": what_model_got_wrong,
        "model_updates_after": model_updates_after,
        "total_votes": total_votes, "verified_votes": verified_votes,
        "vote_distribution": vote_distribution,
        "crowd_correct_winner": crowd_correct_winner,
        "vote_timeline": vote_timeline, "votes_by_phase": votes_by_phase,
        "sources_used": sources_used, "sources_failed": sources_failed,
        "fetch_cycles": fetch_cycles, "parse_errors": parse_errors,
        "hallucinations_caught": hallucinations_caught,
        "fallbacks_triggered": fallbacks_triggered,
        "mlflow_run_id": mlflow_run_id,
        "accuracy_before": accuracy_before, "accuracy_after": accuracy_after,
        "improvement_pct": improvement_pct, "deploy_decision": deploy_decision,
        "training_duration_s": training_duration_s,
        "feature_importances": feature_importances,
        "full_commentary_archive": full_commentary_archive,
        "key_moments": key_moments,
        "player_performance_ratings": player_performance_ratings,
    }