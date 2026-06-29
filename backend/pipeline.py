"""
pipeline.py — Match Pipeline Orchestrator (Session 11)
=======================================================
State machine + fetch intervals + alert triggers + SSE event emission.
Coordinates fetcher.py, parser.py, model.py, alerts.py, sheets.py, fixtures.py.

Session 11 additions:
  HOOK 1 — Auto score update: fetches every 30s, updates DB on change
  HOOK 2 — Auto retrain: triggers after every FINISHED match
  HOOK 3 — Auto pre-match brief: generates via Groq 3h before kickoff
  HOOK 4 — Auto post-match debrief: generates via Groq 5min after FINISHED

All hooks are self-contained background tasks — pipeline never blocks.
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from loguru import logger

try:
    import alerts
except ImportError:
    alerts = None

try:
    import sheets
except ImportError:
    sheets = None

try:
    from fetcher import fetch_match_data, fetch_all_active_matches, SourceResult, get_fetch_interval
except ImportError:
    fetch_match_data = None
    fetch_all_active_matches = None
    SourceResult = None
    get_fetch_interval = lambda count, state: 30

try:
    from fixtures import (
        FIXTURE_BY_ID, assign_groups_for_day, get_match_info,
        get_sources_for_match, get_todays_matches, get_upcoming_matches,
        get_retrain_threshold, get_phase_label, safe_to_deploy,
        swap_group, update_fixture_teams, load_state,
    )
except ImportError:
    FIXTURE_BY_ID = {}
    def assign_groups_for_day(x): return {}
    def get_match_info(x): return None
    def get_sources_for_match(x): return []
    def get_todays_matches(): return []
    def get_upcoming_matches(hours_ahead=24): return []
    def get_retrain_threshold(x): return 0.02
    def get_phase_label(x): return "Group Stage"
    def safe_to_deploy(): return True, "No matches"
    def swap_group(x): return "G1"
    def update_fixture_teams(m, h, a): pass
    def load_state(): pass

try:
    from model import predict, retrain, get_current_version, get_accuracy, ModelResult
except ImportError:
    predict = None
    retrain = None
    def get_current_version(): return 0
    def get_accuracy(): return None
    ModelResult = None

try:
    from parser import parse_match_state as parse_commentary
    ParsedMatchState = None
except ImportError:
    try:
        from parser import parse_commentary
        ParsedMatchState = None
    except ImportError:
        parse_commentary = None
        ParsedMatchState = None


# ─────────────────────────────────────────────
# STATE MACHINE
# ─────────────────────────────────────────────

class MatchState(str, Enum):
    SCHEDULED   = "SCHEDULED"
    LIVE        = "LIVE"
    HT          = "HT"
    LIVE_2H     = "LIVE_2H"
    FT          = "FT"
    ET_1H       = "ET_1H"
    ET_HT       = "ET_HT"
    ET_2H       = "ET_2H"
    PENALTIES   = "PENALTIES"
    FINISHED    = "FINISHED"
    VOID        = "VOID"

TRANSITION_KEYWORDS: dict[str, str] = {
    "half time":              "HT",
    "half-time":              "HT",
    " ht ":                   "HT",
    "half time whistle":      "HT",
    "second half":            "LIVE_2H",
    "second half started":    "LIVE_2H",
    "full time":              "FT",
    "full-time":              "FT",
    "final whistle":          "FT",
    " ft ":                   "FT",
    "extra time half time":   "ET_HT",
    "extra time second half": "ET_2H",
    "extra time":             "ET_1H",
    "extra-time":             "ET_1H",
    "penalty shootout":       "PENALTIES",
    "penalties":              "PENALTIES",
    "penalty kicks":          "PENALTIES",
    "final score":            "FINISHED",
    "match over":             "FINISHED",
    "match ended":            "FINISHED",
    "abandoned":              "VOID",
    "postponed":              "VOID",
    "suspended":              "VOID",
}

VALID_TRANSITIONS: dict[str, set] = {
    "SCHEDULED":  {"LIVE", "VOID"},
    "LIVE":       {"HT", "FT", "VOID"},
    "HT":         {"LIVE_2H", "VOID"},
    "LIVE_2H":    {"FT", "VOID"},
    "FT":         {"ET_1H", "FINISHED", "VOID"},
    "ET_1H":      {"ET_HT", "VOID"},
    "ET_HT":      {"ET_2H", "VOID"},
    "ET_2H":      {"FT", "PENALTIES", "VOID"},
    "PENALTIES":  {"FINISHED", "VOID"},
    "FINISHED":   set(),
    "VOID":       set(),
}


def _valid_transition(current: str, target: str) -> bool:
    return target in VALID_TRANSITIONS.get(current, set())


def _detect_state_from_text(text: str, current_state: str) -> Optional[str]:
    text_lower = text.lower()
    for keyword, target in TRANSITION_KEYWORDS.items():
        if keyword in text_lower:
            if _valid_transition(current_state, target):
                return target
    return None


# ─────────────────────────────────────────────
# PER-MATCH RUNTIME
# ─────────────────────────────────────────────

class MatchRuntime:
    def __init__(self, match_id: str, fixture: dict):
        self.match_id = match_id
        self.home = fixture.get("home", "")
        self.away = fixture.get("away", "")
        self.venue = fixture.get("venue", "")
        self.kickoff_utc = datetime.fromisoformat(
            fixture["kickoff_utc"].replace("Z", "+00:00")
        )
        self.phase = fixture.get("phase", "group")
        self.group = fixture.get("group")

        self.state = "SCHEDULED"
        self.prev_state = None

        self.score_home = fixture.get("home_score", 0)
        self.score_away = fixture.get("away_score", 0)
        self.minute = 0

        self.prediction = None
        self.confidence_locked = False
        self.confidence_at_kickoff: Optional[dict] = None

        self.events: list[dict] = []
        self.pre_match_brief = ""
        self.post_match_debrief = ""

        self.sources_used: list[str] = []
        self.sources_failed: list[str] = []
        self.fetch_cycles = 0
        self.parse_errors = 0
        self.hallucinations_caught = 0
        self.fallbacks_triggered = 0

        self.last_score = (self.score_home, self.score_away)
        self.last_state = self.state
        self.ft_confirmed_at: Optional[datetime] = None
        self.all_sources_down_since: Optional[datetime] = None

        self.penalty_scores = {"home": 0, "away": 0}
        self.task: Optional[asyncio.Task] = None


# ─────────────────────────────────────────────
# ACTIVE REGISTRY + SSE
# ─────────────────────────────────────────────

_active: dict[str, MatchRuntime] = {}
_sse_queues: dict[str, list[asyncio.Queue]] = {}
_global_sse_queues: list[asyncio.Queue] = []

# DB session factory — set by main.py via init_db_factory()
_db_factory = None


def init_db_factory(factory):
    """Called by main.py to give pipeline access to DB sessions."""
    global _db_factory
    _db_factory = factory


def _get_db():
    if _db_factory is None:
        return None
    return _db_factory()


def register_sse_queue(match_id: str, q: asyncio.Queue) -> None:
    _sse_queues.setdefault(match_id, []).append(q)


def unregister_sse_queue(match_id: str, q: asyncio.Queue) -> None:
    if match_id in _sse_queues:
        try:
            _sse_queues[match_id].remove(q)
        except ValueError:
            pass


def _emit(match_id: str, event_type: str, data: dict) -> None:
    payload = {
        "type": event_type,
        "data": data,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    for q in _sse_queues.get(match_id, []):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass
    for q in _global_sse_queues:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


# ─────────────────────────────────────────────
# DB HELPERS (write match updates to Postgres)
# ─────────────────────────────────────────────

async def _db_update_match(match_id: str, **kwargs) -> bool:
    """Update match row in Postgres. Returns True on success."""
    db = _get_db()
    if db is None:
        return False
    try:
        from main import MatchDB
        match = db.query(MatchDB).filter(MatchDB.id == match_id).first()
        if not match:
            return False
        for k, v in kwargs.items():
            if hasattr(match, k):
                setattr(match, k, v)
        match.last_updated = datetime.now(timezone.utc)
        db.commit()
        return True
    except Exception as e:
        logger.error(f"[DB] Update failed for {match_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            db.close()
        except Exception:
            pass


async def _db_save_event(match_id: str, event: dict) -> None:
    db = _get_db()
    if db is None:
        return
    try:
        from main import LiveEventDB
        ev = LiveEventDB(
            match_id=match_id,
            event_type=event.get("type"),
            minute=str(event.get("minute", "")),
            player=event.get("player", ""),
            team=event.get("team", ""),
            sentiment=event.get("sentiment", ""),
            context=event.get("context", ""),
            timestamp=datetime.now(timezone.utc),
        )
        db.add(ev)
        db.commit()
    except Exception as e:
        logger.error(f"[DB] Event save failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


# ─────────────────────────────────────────────
# HOOK 1: AUTO SCORE UPDATE (every 30s)
# ─────────────────────────────────────────────

async def _auto_update_score(rt: MatchRuntime, source_results: list) -> bool:
    """
    Process fetch results, update DB if score/state changed.
    Returns True if anything changed (triggers Groq for context).
    """
    if not source_results:
        return False

    # Get consensus score from results that have extracted_score
    scores = [
        r.extracted_score for r in source_results
        if r.ok and r.extracted_score
    ]

    if not scores:
        return False

    # Majority vote on score
    from collections import Counter
    score_keys = [f"{s['home']}-{s['away']}" for s in scores]
    most_common = Counter(score_keys).most_common(1)
    if not most_common:
        return False

    best_score_key, count = most_common[0]
    if count < 1:
        return False

    home_s, away_s = best_score_key.split("-")
    new_home, new_away = int(home_s), int(away_s)

    # Hallucination guard
    if new_home > 15 or new_away > 15:
        rt.hallucinations_caught += 1
        logger.warning(f"[Auto] {rt.match_id}: Hallucination caught — {new_home}-{new_away}")
        return False

    score_changed = (new_home != rt.score_home) or (new_away != rt.score_away)

    # Get consensus state from text content
    state_changed = False
    all_texts = " ".join(r.text or "" for r in source_results if r.ok and r.text)
    if all_texts:
        detected_state = _detect_state_from_text(all_texts, rt.state)
        if detected_state and detected_state != rt.state:
            if _valid_transition(rt.state, detected_state):
                logger.info(f"[Auto] {rt.match_id}: State {rt.state} → {detected_state}")
                rt.prev_state = rt.state
                rt.state = detected_state
                state_changed = True

                # FT confirmation timestamp
                if detected_state == "FT":
                    rt.ft_confirmed_at = datetime.now(timezone.utc)

                # Emit state change
                _emit(rt.match_id, "state_change", {
                    "match_id": rt.match_id,
                    "state": rt.state,
                    "score_home": rt.score_home,
                    "score_away": rt.score_away,
                })

                # Update DB
                await _db_update_match(rt.match_id, state=rt.state)

                # Trigger FINISHED processing
                if detected_state == "FINISHED":
                    asyncio.create_task(_post_match_auto(rt))
                elif detected_state == "VOID":
                    rt.confidence_locked = True
                    _emit(rt.match_id, "void", {
                        "match_id": rt.match_id,
                        "message": "Match void — all predictions cancelled.",
                    })

    # Update score in DB if changed
    if score_changed:
        rt.score_home = new_home
        rt.score_away = new_away
        logger.info(f"[Auto] {rt.match_id}: Score updated → {new_home}-{new_away} (from {count}/{len(source_results)} sources)")

        await _db_update_match(
            rt.match_id,
            home_score=new_home,
            away_score=new_away,
        )

        # Emit score update to SSE
        _emit(rt.match_id, "score_update", {
            "match_id": rt.match_id,
            "score_home": new_home,
            "score_away": new_away,
            "state": rt.state,
            "sources_agreed": count,
        })

    return score_changed or state_changed


# ─────────────────────────────────────────────
# HOOK 2: AUTO RETRAIN (after every FINISHED match)
# ─────────────────────────────────────────────

async def _auto_retrain(rt: MatchRuntime) -> None:
    """Auto-trigger retrain after match finishes. Runs as background task."""
    if retrain is None:
        logger.warning(f"[AutoRetrain] {rt.match_id}: model.retrain not available")
        return

    logger.info(f"[AutoRetrain] {rt.match_id}: Starting retrain...")
    accuracy_before = get_accuracy()

    try:
        retrain_result = await retrain(match_id=rt.match_id)
        accuracy_after = retrain_result.accuracy_after
        improvement = (accuracy_after - accuracy_before) if (accuracy_before and accuracy_after) else None
        threshold = get_retrain_threshold(rt.match_id)
        deployed = (improvement >= threshold) if improvement is not None else False

        logger.info(
            f"[AutoRetrain] {rt.match_id}: "
            f"{'DEPLOYED' if deployed else 'SKIPPED'} "
            f"(accuracy: {accuracy_before} → {accuracy_after})"
        )

        # Notify via Telegram
        if alerts:
            try:
                await alerts.alert_retrain_result(
                    match_id=rt.match_id,
                    version=get_current_version(),
                    accuracy_before=accuracy_before or 0,
                    accuracy_after=accuracy_after or 0,
                    improvement=improvement or 0,
                    deployed=deployed,
                    threshold=threshold,
                    duration_s=getattr(retrain_result, "duration_s", 0),
                )
            except Exception as e:
                logger.error(f"[AutoRetrain] Alert failed: {e}")

        # Save to sheets
        if sheets:
            try:
                sheets.save_model_run({
                    "match_id": rt.match_id,
                    "version": get_current_version(),
                    "accuracy_before": accuracy_before,
                    "accuracy_after": accuracy_after,
                    "improvement_pct": improvement,
                    "deploy_decision": deployed,
                    "training_duration_s": getattr(retrain_result, "duration_s", 0),
                    "feature_importances": getattr(retrain_result, "feature_importances", {}),
                })
            except Exception as e:
                logger.error(f"[AutoRetrain] Sheets save failed: {e}")

        # Broadcast retrain result via SSE
        _emit("global", "retrain_complete", {
            "match_id": rt.match_id,
            "deployed": deployed,
            "accuracy_before": accuracy_before,
            "accuracy_after": accuracy_after,
            "version": get_current_version(),
        })

    except Exception as e:
        logger.error(f"[AutoRetrain] {rt.match_id}: Failed — {e}")
        if alerts:
            try:
                await alerts.send_warning(f"Auto-retrain failed for {rt.match_id}", context={"error": str(e)})
            except Exception:
                pass


# ─────────────────────────────────────────────
# HOOK 3: AUTO PRE-MATCH BRIEF (3h before kickoff)
# ─────────────────────────────────────────────

async def _generate_pre_match_brief(match_id: str, home: str, away: str) -> str:
    """
    Generate pre-match brief using Tavily search + Groq 70b.
    Returns brief text, saves to DB.
    """
    groq_key = os.getenv("GROQ_API_KEY_1") or os.getenv("GROQ_API_KEY_2")
    if not groq_key:
        return ""

    # Search for team news
    team_news = ""
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=tavily_key)
            result = client.search(
                query=f"{home} vs {away} team news World Cup 2026",
                search_depth="basic",
                max_results=3,
            )
            team_news = " | ".join(
                r.get("content", "")[:500]
                for r in result.get("results", [])[:3]
            )
        except Exception as e:
            logger.warning(f"[PreBrief] Tavily search failed: {e}")

    # Generate brief with Groq
    try:
        from groq import Groq
        client = Groq(api_key=groq_key)
        prompt = (
            f"Write a 3-sentence pre-match brief for {home} vs {away} at FIFA World Cup 2026. "
            f"Cover: current form, key players to watch, and one tactical point. "
            f"Be factual and concise. "
            f"Context: {team_news[:1000] if team_news else 'No additional context available.'}"
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3,
        )
        brief = response.choices[0].message.content.strip()
        logger.info(f"[PreBrief] Generated for {match_id}: {brief[:80]}...")
        return brief
    except Exception as e:
        logger.error(f"[PreBrief] Groq generation failed: {e}")
        return f"{home} vs {away} — World Cup 2026 match. AI pre-match analysis unavailable."


# ─────────────────────────────────────────────
# HOOK 4: AUTO POST-MATCH DEBRIEF (5min after FINISHED)
# ─────────────────────────────────────────────

async def _generate_post_match_debrief(rt: MatchRuntime) -> str:
    """
    Generate post-match debrief using Groq 70b.
    Uses match events already in DB — no Tavily needed.
    """
    groq_key = os.getenv("GROQ_API_KEY_1") or os.getenv("GROQ_API_KEY_2")
    if not groq_key:
        return ""

    winner = (
        rt.home if rt.score_home > rt.score_away
        else rt.away if rt.score_away > rt.score_home
        else "Draw"
    )
    goals = [e for e in rt.events if e.get("type") == "goal"]
    scorers = [f"{e.get('player', 'Unknown')} ({e.get('minute', '?')}')" for e in goals]
    prediction_line = ""
    if rt.prediction:
        pred = rt.prediction
        prediction_line = (
            f"AI predicted: {getattr(pred, 'home_win', 0)*100:.0f}% home win, "
            f"{getattr(pred, 'draw', 0)*100:.0f}% draw, "
            f"{getattr(pred, 'away_win', 0)*100:.0f}% away win."
        )

    try:
        from groq import Groq
        client = Groq(api_key=groq_key)
        prompt = (
            f"Write a 3-sentence post-match debrief for {rt.home} {rt.score_home}-{rt.score_away} {rt.away} "
            f"at FIFA World Cup 2026. "
            f"Result: {'Draw' if winner == 'Draw' else winner + ' won'}. "
            f"{'Scorers: ' + ', '.join(scorers) + '.' if scorers else 'No goals noted.'} "
            f"{prediction_line} "
            f"Cover: what happened, key moment, and what this means for the group stage."
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3,
        )
        debrief = response.choices[0].message.content.strip()
        logger.info(f"[PostDebrief] Generated for {rt.match_id}: {debrief[:80]}...")
        return debrief
    except Exception as e:
        logger.error(f"[PostDebrief] Groq generation failed: {e}")
        return f"{rt.home} {rt.score_home}-{rt.score_away} {rt.away}. Post-match analysis unavailable."


# ─────────────────────────────────────────────
# POST-MATCH AUTOMATION
# ─────────────────────────────────────────────

async def _post_match_auto(rt: MatchRuntime) -> None:
    """
    Triggered when match hits FINISHED state.
    Runs retrain + debrief generation concurrently.
    """
    logger.info(f"[PostMatch] {rt.match_id}: Starting post-match automation")

    # Wait 5 minutes for final score to stabilise
    await asyncio.sleep(300)

    # Run retrain and debrief concurrently
    retrain_task = asyncio.create_task(_auto_retrain(rt))
    debrief_task = asyncio.create_task(_generate_post_match_debrief(rt))

    debrief = await debrief_task
    await retrain_task  # wait for retrain too

    if debrief:
        rt.post_match_debrief = debrief
        # Save to DB
        await _db_update_match(rt.match_id, post_match_debrief=debrief)
        # Push via SSE
        _emit(rt.match_id, "debrief_ready", {
            "match_id": rt.match_id,
            "debrief": debrief,
        })

    # Save to sheets
    if sheets:
        try:
            winner = (
                rt.home if rt.score_home > rt.score_away
                else rt.away if rt.score_away > rt.score_home
                else "draw"
            )
            sheets.save_match(sheets.build_match_record(
                match_id=rt.match_id,
                model_version=get_current_version(),
                phase=rt.phase,
                group=rt.group,
                venue=rt.venue,
                kickoff_utc=rt.kickoff_utc,
                home_team=rt.home,
                away_team=rt.away,
                home_squad=[], away_squad=[],
                home_manager="", away_manager="",
                home_fifa_rank=0, away_fifa_rank=0,
                home_elo=0.0, away_elo=0.0,
                home_win_pct=getattr(rt.prediction, "home_win", 0.0),
                draw_pct=getattr(rt.prediction, "draw", 0.0),
                away_win_pct=getattr(rt.prediction, "away_win", 0.0),
                predicted_scorer=getattr(rt.prediction, "predicted_scorer", ""),
                predicted_score=getattr(rt.prediction, "predicted_score", ""),
                confidence_range=getattr(rt.prediction, "confidence_range", ""),
                training_matches_seen=getattr(rt.prediction, "training_matches_seen", 0),
                final_score_home=rt.score_home,
                final_score_away=rt.score_away,
                winner=winner,
                scorers=[e.get("player", "") for e in rt.events if e.get("type") == "goal"],
                assisters=[], cards=[], substitutions=[],
                first_goal_minute=next((e.get("minute") for e in rt.events if e.get("type") == "goal"), None),
                final_whistle_minute=rt.minute,
                injury_time_1h=0, injury_time_2h=0,
                went_to_et=rt.state in ("ET_1H", "ET_HT", "ET_2H"),
                went_to_penalties=rt.prev_state == "PENALTIES" or rt.state == "PENALTIES",
                total_duration_min=rt.minute,
                regulation_duration_min=min(rt.minute, 90),
                ai_correct_winner=False,
                ai_correct_scorer=False,
                ai_confidence_at_lock=getattr(rt.prediction, "home_win", 0.0),
                confidence_calibration_error=0.0,
                pre_match_brief=rt.pre_match_brief,
                post_match_debrief=rt.post_match_debrief,
                what_model_got_wrong="",
                model_updates_after={},
                total_votes=0, verified_votes=0, vote_distribution={},
                crowd_correct_winner=False, vote_timeline=[], votes_by_phase={},
                sources_used=list(set(rt.sources_used)),
                sources_failed=list(set(rt.sources_failed)),
                fetch_cycles=rt.fetch_cycles,
                parse_errors=rt.parse_errors,
                hallucinations_caught=rt.hallucinations_caught,
                fallbacks_triggered=rt.fallbacks_triggered,
                mlflow_run_id="",
                accuracy_before=get_accuracy() or 0.0,
                accuracy_after=get_accuracy() or 0.0,
                improvement_pct=0.0,
                deploy_decision=False,
                training_duration_s=0.0,
                feature_importances={},
                full_commentary_archive=[],
                key_moments=[e for e in rt.events if e.get("type") in ("goal", "red_card")],
                player_performance_ratings={},
            ))
        except Exception as e:
            logger.error(f"[PostMatch] Sheets save failed: {e}")

    # Telegram deploy window suggestion
    if alerts:
        try:
            safe, reason = safe_to_deploy()
            await alerts.alert_deploy_window(safe, reason)
        except Exception as e:
            logger.error(f"[PostMatch] Deploy alert failed: {e}")

    logger.info(f"[PostMatch] {rt.match_id}: Automation complete")


# ─────────────────────────────────────────────
# CORE MATCH LOOP
# ─────────────────────────────────────────────

async def _match_loop(match_id: str) -> None:
    rt = _active[match_id]
    logger.info(f"[Loop] {match_id} started — {rt.home} vs {rt.away}")

    while rt.state not in ("FINISHED", "VOID", "FT"):
        loop_start = time.monotonic()

        # Count how many matches are currently live
        active_count = sum(
            1 for r in _active.values()
            if r.state in ("LIVE", "LIVE_2H", "ET_1H", "ET_2H", "PENALTIES")
        )
        interval = get_fetch_interval(active_count, rt.state)

        # Heartbeat during SCHEDULED state — wait for kickoff
        if rt.state == "SCHEDULED":
            now = datetime.now(timezone.utc)
            remaining = (rt.kickoff_utc - now).total_seconds()
            if remaining <= 0:
                logger.info(f"[Loop] {match_id}: Kickoff time reached — transitioning to LIVE")
                rt.state = "LIVE"
                await _db_update_match(match_id, state="LIVE")
                _emit(match_id, "state_change", {
                    "match_id": match_id,
                    "state": "LIVE",
                    "score_home": rt.score_home,
                    "score_away": rt.score_away,
                })
                continue
            _emit(match_id, "heartbeat", {"match_id": match_id, "state": rt.state})
            await asyncio.sleep(min(interval, remaining + 5))
            continue

        # FT silence rule: 20 min after FT → FINISHED
        if rt.state == "FT" and rt.ft_confirmed_at:
            silence_secs = (datetime.now(timezone.utc) - rt.ft_confirmed_at).total_seconds()
            if silence_secs > 20 * 60:
                logger.info(f"[Loop] {match_id}: 20min silence after FT → FINISHED")
                rt.state = "FINISHED"
                await _db_update_match(match_id, state="FINISHED")
                asyncio.create_task(_post_match_auto(rt))
                break

        # Fetch all sources
        if fetch_match_data is None:
            await asyncio.sleep(interval)
            continue

        try:
            source_results = await fetch_match_data(
                match_id=match_id,
                home=rt.home,
                away=rt.away,
            )
        except Exception as e:
            logger.error(f"[Loop] {match_id}: fetch error — {e}")
            if sheets:
                sheets.save_error({
                    "match_id": match_id,
                    "layer": "fetcher",
                    "error_type": type(e).__name__,
                    "message": str(e),
                    "resolution": "Serving last known state",
                    "recovered": False,
                })
            await asyncio.sleep(interval)
            continue

        rt.fetch_cycles += 1

        valid = [r for r in source_results if r.ok]
        failed = [r for r in source_results if not r.ok]
        blocked = [r for r in source_results if r.blocked]

        rt.sources_used.extend(r.source for r in valid)
        rt.sources_failed.extend(r.source for r in failed)

        # Source health alerts
        if len(valid) == 0 and source_results:
            if rt.all_sources_down_since is None:
                rt.all_sources_down_since = datetime.now(timezone.utc)
                if alerts:
                    try:
                        await alerts.alert_all_sources_down(
                            match_info=f"{rt.home} vs {rt.away} ({rt.minute}')",
                            last_good_state=f"{rt.score_home}-{rt.score_away}",
                            down_since=rt.all_sources_down_since,
                        )
                    except Exception:
                        pass
            rt.fallbacks_triggered += 1
            _emit(match_id, "updating", {"match_id": match_id})
            await asyncio.sleep(interval)
            continue

        if rt.all_sources_down_since is not None:
            rt.all_sources_down_since = None

        if len(blocked) >= 3 and alerts:
            try:
                await alerts.alert_sources_failing(
                    failing_count=len(blocked),
                    total=len(source_results),
                    match_info=f"{rt.home} vs {rt.away}",
                    failed_sources=[r.source for r in blocked],
                )
            except Exception:
                pass

        # HOOK 1: Auto update score/state from extracted HTML
        changed = await _auto_update_score(rt, source_results)

        # Emit full match update
        _emit(match_id, "match_update", {
            "match_id":   match_id,
            "state":      rt.state,
            "score_home": rt.score_home,
            "score_away": rt.score_away,
            "minute":     rt.minute,
            "home":       rt.home,
            "away":       rt.away,
            "confidence_locked": rt.confidence_locked,
            "model_version": get_current_version(),
            "sources_valid": len(valid),
        })

        elapsed = time.monotonic() - loop_start
        await asyncio.sleep(max(0, interval - elapsed))

    logger.info(f"[Loop] {match_id} ended (state={rt.state})")
    _active.pop(match_id, None)


# ─────────────────────────────────────────────
# SCHEDULE A MATCH
# ─────────────────────────────────────────────

async def schedule_match(match_id: str) -> None:
    if match_id in _active:
        logger.warning(f"[Schedule] {match_id}: Already active")
        return

    fixture = get_match_info(match_id)
    if not fixture:
        logger.error(f"[Schedule] {match_id}: Not found in fixtures")
        return

    rt = MatchRuntime(match_id, fixture)
    _active[match_id] = rt

    # HOOK 3: Generate pre-match brief immediately
    asyncio.create_task(_schedule_pre_match_brief(rt))

    # HOOK: Generate and save prediction immediately
    asyncio.create_task(_generate_and_save_prediction(rt))

    # Start match loop
    rt.task = asyncio.create_task(_match_loop(match_id))
    ko_in = (rt.kickoff_utc - datetime.now(timezone.utc)).total_seconds() / 60
    logger.info(f"[Schedule] {match_id}: Scheduled — {rt.home} vs {rt.away} in {ko_in:.0f} min")
    
async def _schedule_pre_match_brief(rt: MatchRuntime) -> None:
    """Generate pre-match brief and save to DB."""
    try:
        brief = await _generate_pre_match_brief(rt.match_id, rt.home, rt.away)
        if brief:
            rt.pre_match_brief = brief
            await _db_update_match(rt.match_id, pre_match_brief=brief)
            _emit(rt.match_id, "pre_match_brief", {
                "match_id": rt.match_id,
                "brief": brief,
            })
    except Exception as e:
        logger.error(f"[PreBrief] {rt.match_id}: {e}")


async def _generate_and_save_prediction(rt: MatchRuntime) -> None:
    """Generate prediction and write to PredictionDB."""
    try:
        from model import predict as model_predict
        result = await model_predict(
            match_id=rt.match_id,
            home=rt.home,
            away=rt.away,
        )
        db = _get_db()
        if db:
            try:
                from main import PredictionDB
                from datetime import datetime, timezone
                existing = db.query(PredictionDB).filter(
                    PredictionDB.match_id == rt.match_id
                ).first()
                if not existing:
                    pred = PredictionDB(
                        match_id=rt.match_id,
                        home_win=result.home_win,
                        draw=result.draw,
                        away_win=result.away_win,
                        predicted_scorer=result.predicted_scorer or "",
                        predicted_score=result.predicted_score or "",
                        confidence_range_low=result.home_win - 0.04,
                        confidence_range_high=result.home_win + 0.04,
                        model_version=result.model_version,
                        training_matches_seen=result.training_matches_seen,
                        created_at=datetime.now(timezone.utc),
                        locked_at_85=False,
                    )
                    db.add(pred)
                    db.commit()
                    logger.info(f"[Prediction] Saved for {rt.match_id}: H={result.home_win:.2f} D={result.draw:.2f} A={result.away_win:.2f}")
                    _emit(rt.match_id, "prediction_update", {
                        "match_id": rt.match_id,
                        "home_win": result.home_win,
                        "draw": result.draw,
                        "away_win": result.away_win,
                        "model_version": result.model_version,
                    })
            except Exception as e:
                logger.error(f"[Prediction] DB write failed for {rt.match_id}: {e}")
                db.rollback()
            finally:
                db.close()
    except Exception as e:
        logger.error(f"[Prediction] Generation failed for {rt.match_id}: {e}")


async def void_match(match_id: str) -> None:
    rt = _active.get(match_id)
    if not rt:
        logger.warning(f"[Void] {match_id}: Not active")
        return
    rt.state = "VOID"
    rt.confidence_locked = True
    await _db_update_match(match_id, state="VOID")
    _emit(match_id, "void", {"match_id": match_id, "message": "Match voided"})
    if rt.task:
        rt.task.cancel()


# ─────────────────────────────────────────────
# PUBLIC TRIGGER (admin panel)
# ─────────────────────────────────────────────

async def trigger_retrain(match_id: str) -> dict:
    """Called from admin panel."""
    if retrain is None:
        return {"success": False, "error": "Model not available"}
    try:
        accuracy_before = get_accuracy()
        result = await retrain(match_id=match_id)
        improvement = (
            (result.accuracy_after - accuracy_before)
            if (accuracy_before is not None and result.accuracy_after is not None)
            else None
        )
        threshold = get_retrain_threshold(match_id)
        deployed = (improvement >= threshold) if improvement is not None else False

        if alerts:
            try:
                await alerts.alert_retrain_result(
                    match_id=match_id,
                    version=get_current_version(),
                    accuracy_before=accuracy_before or 0,
                    accuracy_after=result.accuracy_after or 0,
                    improvement=improvement or 0,
                    deployed=deployed,
                    threshold=threshold,
                    duration_s=getattr(result, "duration_s", 0),
                )
            except Exception:
                pass

        return {
            "success": True,
            "deployed": deployed,
            "accuracy_before": accuracy_before,
            "accuracy_after": result.accuracy_after,
            "improvement": improvement,
            "version": get_current_version(),
        }
    except Exception as e:
        logger.error(f"[Retrain] Admin trigger failed: {e}")
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────
# QUERY HELPERS
# ─────────────────────────────────────────────

def get_all_match_states() -> list[dict]:
    return [
        {
            "match_id":   rt.match_id,
            "home":       rt.home,
            "away":       rt.away,
            "state":      rt.state,
            "score_home": rt.score_home,
            "score_away": rt.score_away,
            "minute":     rt.minute,
            "fetch_cycles": rt.fetch_cycles,
            "parse_errors": rt.parse_errors,
            "fallbacks":  rt.fallbacks_triggered,
        }
        for rt in _active.values()
    ]


def get_match_state(match_id: str) -> Optional[dict]:
    rt = _active.get(match_id)
    if not rt:
        return None
    return {
        "match_id":   rt.match_id,
        "home":       rt.home,
        "away":       rt.away,
        "state":      rt.state,
        "score_home": rt.score_home,
        "score_away": rt.score_away,
        "minute":     rt.minute,
        "events":     rt.events[-20:],
        "model_version": get_current_version(),
    }


# ─────────────────────────────────────────────
# STARTUP / SHUTDOWN
# ─────────────────────────────────────────────

async def startup() -> None:
    try:
        load_state()
    except Exception as e:
        logger.error(f"[Startup] load_state failed: {e}")

    if sheets:
        try:
            await sheets.start_queue_worker()
        except Exception as e:
            logger.error(f"[Startup] Sheets worker failed: {e}")

    if alerts:
        try:
            asyncio.create_task(alerts.poll_stop_commands())
        except Exception as e:
            logger.error(f"[Startup] Telegram poller failed: {e}")

    todays = get_todays_matches()
    if todays:
        ids = [f["match_id"] for f in todays]
        try:
            assign_groups_for_day(ids)
        except Exception:
            pass
        logger.info(f"[Startup] Today: {[f['home'] + ' vs ' + f['away'] for f in todays]}")

    logger.info("[Startup] Pipeline ready")


async def shutdown() -> None:
    for rt in list(_active.values()):
        if rt.task and not rt.task.done():
            rt.task.cancel()
    if sheets:
        try:
            await sheets.stop_queue_worker()
        except Exception:
            pass
    logger.info("[Shutdown] Pipeline stopped")


# ─────────────────────────────────────────────
# PipelineOrchestrator (main.py interface)
# ─────────────────────────────────────────────

class PipelineOrchestrator:
    """
    Object interface for main.py.
    Wraps module-level functions.
    """

    def __init__(self, broker=None):
        self._broker = broker
        self._running = False

    async def start(self) -> None:
        await startup()
        self._running = True
        logger.info("[Orchestrator] Started")

    async def stop(self) -> None:
        await shutdown()
        self._running = False

    async def check_source_health(self) -> None:
        """Called every 30 min by APScheduler."""
        try:
            from fetcher import source_health
            report = source_health.get_health_report()
            blocked = [s for s in report if s["status"] in ("blocked", "removed")]
            if len(blocked) >= 3 and alerts:
                try:
                    await alerts.alert_sources_failing(
                        failing_count=len(blocked),
                        total=6,
                        match_info="Source health check",
                        failed_sources=[s["name"] for s in blocked],
                    )
                except Exception:
                    pass
            ok_count = sum(1 for s in report if s["status"] == "ok")
            logger.info(f"[Health] {ok_count}/6 sources ok, {len(blocked)} blocked/removed")
        except Exception as e:
            logger.error(f"[Health] Check failed: {e}")

    async def generate_pre_match_briefs(self) -> None:
        """
        Called every 15 min by APScheduler.
        Schedules matches kicking off in 2.5-3h if not already active.
        Also generates pre-match briefs for matches without one.
        """
        try:
            upcoming = get_upcoming_matches(hours_ahead=3)
            for fixture in upcoming:
                match_id = fixture.get("match_id")
                if not match_id:
                    continue

                kickoff_str = fixture.get("kickoff_utc", "")
                try:
                    kickoff = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                    hours_until = (kickoff - datetime.now(timezone.utc)).total_seconds() / 3600
                except Exception:
                    continue

                # Schedule match 2.5h before kickoff
                if 2.5 <= hours_until <= 3.0 and match_id not in _active:
                    logger.info(f"[Orchestrator] Auto-scheduling {match_id} ({hours_until:.1f}h to kickoff)")
                    asyncio.create_task(schedule_match(match_id))

                # Generate pre-match brief for matches < 3h away without one
                if hours_until <= 3.0:
                    # Check DB if brief already exists
                    db = _get_db()
                    if db:
                        try:
                            from main import MatchDB
                            m = db.query(MatchDB).filter(MatchDB.id == match_id).first()
                            if m and not m.pre_match_brief:
                                asyncio.create_task(_generate_and_save_brief(
                                    match_id,
                                    fixture.get("home", ""),
                                    fixture.get("away", ""),
                                ))
                        except Exception:
                            pass
                        finally:
                            try:
                                db.close()
                            except Exception:
                                pass

        except Exception as e:
            logger.error(f"[Orchestrator] generate_pre_match_briefs error: {e}")

    async def check_fixtures(self) -> None:
        """Called every 2h by APScheduler."""
        try:
            todays = get_todays_matches()
            logger.info(f"[Orchestrator] Fixture check: {len(todays)} matches today")
        except Exception as e:
            logger.error(f"[Orchestrator] Fixture check failed: {e}")

    def get_active_match_ids(self) -> list[str]:
        return list(_active.keys())

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "active_matches": len(_active),
            "match_states": get_all_match_states(),
        }


async def _generate_and_save_brief(match_id: str, home: str, away: str) -> None:
    """Background task: generate brief and save to DB."""
    brief = await _generate_pre_match_brief(match_id, home, away)
    if brief:
        await _db_update_match(match_id, pre_match_brief=brief)
        _emit(match_id, "pre_match_brief", {"match_id": match_id, "brief": brief})
        logger.info(f"[PreBrief] Auto-generated and saved for {match_id}")