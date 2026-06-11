"""
pipeline.py — Match Pipeline Orchestrator
State machine + fetch intervals + alert triggers + scoring + SSE event emission.
Coordinates fetcher.py, parser.py, model.py, alerts.py, sheets.py, fixtures.py.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from loguru import logger

import alerts
import sheets
from fetcher import fetch_match_data, SourceResult
from fixtures import (
    FIXTURE_BY_ID,
    assign_groups_for_day,
    get_match_info,
    get_sources_for_match,
    get_todays_matches,
    get_upcoming_matches,
    get_retrain_threshold,
    get_phase_label,
    safe_to_deploy,
    swap_group,
    update_fixture_teams,
)
from model import (
    predict,
    retrain,
    get_current_version,
    get_accuracy,
    ModelResult,
)
from parser import parse_commentary, ParsedMatchState

# ─────────────────────────────────────────────
# Match state machine
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

# Keywords that drive state transitions (all lowercase for matching)
TRANSITION_KEYWORDS: dict[str, MatchState] = {
    "half time":             MatchState.HT,
    "half-time":             MatchState.HT,
    " ht ":                  MatchState.HT,
    "half time whistle":     MatchState.HT,
    "second half":           MatchState.LIVE_2H,
    "second half started":   MatchState.LIVE_2H,
    "full time":             MatchState.FT,
    "full-time":             MatchState.FT,
    "final whistle":         MatchState.FT,
    " ft ":                  MatchState.FT,
    "extra time":            MatchState.ET_1H,
    "extra-time":            MatchState.ET_1H,
    "extra time half time":  MatchState.ET_HT,
    "extra time second half":MatchState.ET_2H,
    "penalty shootout":      MatchState.PENALTIES,
    "penalties":             MatchState.PENALTIES,
    "penalty kicks":         MatchState.PENALTIES,
    "final score":           MatchState.FINISHED,
    "match over":            MatchState.FINISHED,
    "match ended":           MatchState.FINISHED,
    "abandoned":             MatchState.VOID,
    "postponed":             MatchState.VOID,
    "suspended":             MatchState.VOID,
}

# Fetch intervals (seconds) per state and active match count
def get_fetch_interval(state: MatchState, active_count: int) -> int:
    if state in (MatchState.HT, MatchState.ET_HT):
        return 300  # 5 minutes
    if state == MatchState.SCHEDULED:
        return 780  # 13 minutes (keep-alive)
    if state == MatchState.PENALTIES:
        return 30
    if state in (MatchState.FINISHED, MatchState.VOID):
        return 9999  # stop fetching
    # LIVE states — scale with active match count
    if active_count == 1:
        return 15
    if active_count == 2:
        return 20
    return 30  # 3+ matches


# ─────────────────────────────────────────────
# Per-match runtime state
# ─────────────────────────────────────────────

class MatchRuntime:
    """
    All mutable state for one match.
    Lives in memory; key facts persisted to Supabase via main.py.
    """
    def __init__(self, match_id: str):
        f = FIXTURE_BY_ID[match_id]
        self.match_id         = match_id
        self.home             = f["home"]
        self.away             = f["away"]
        self.venue            = f["venue"]
        self.kickoff_utc      = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        self.phase            = f["phase"]
        self.group            = f.get("group")

        # State machine
        self.state            = MatchState.SCHEDULED
        self.prev_state       = None

        # Score
        self.score_home       = 0
        self.score_away       = 0
        self.minute           = 0

        # Prediction (set at SCHEDULED by model.py)
        self.prediction: Optional[ModelResult] = None
        self.confidence_locked = False  # True after 85'
        self.confidence_at_kickoff: Optional[dict] = None

        # Events log
        self.events: list[dict] = []
        self.pre_match_brief  = ""
        self.post_match_debrief = ""

        # Source tracking
        self.sources_used:    list[str] = []
        self.sources_failed:  list[str] = []
        self.fetch_cycles     = 0
        self.parse_errors     = 0
        self.hallucinations_caught = 0
        self.fallbacks_triggered = 0

        # Silence tracking
        self.last_commentary_at: Optional[datetime] = None
        self.ft_confirmed_at:    Optional[datetime] = None

        # Down tracking
        self.all_sources_down_since: Optional[datetime] = None

        # Penalty state
        self.penalty_round    = 0
        self.penalty_scores   = {"home": 0, "away": 0}

        # Last known good state (served to users during outages)
        self.last_good_state: Optional[ParsedMatchState] = None

        # Asyncio task for this match
        self.task: Optional[asyncio.Task] = None

    def state_label(self) -> str:
        return get_phase_label(self.match_id)


# ─────────────────────────────────────────────
# Active match registry
# ─────────────────────────────────────────────

_active: dict[str, MatchRuntime] = {}  # match_id → MatchRuntime

# SSE broadcaster: populated by main.py
# pipeline emits events here; main.py's SSE handler broadcasts
_sse_queues: dict[str, list[asyncio.Queue]] = {}  # match_id → [client queues]


def register_sse_queue(match_id: str, q: asyncio.Queue) -> None:
    _sse_queues.setdefault(match_id, []).append(q)


def unregister_sse_queue(match_id: str, q: asyncio.Queue) -> None:
    if match_id in _sse_queues:
        try:
            _sse_queues[match_id].remove(q)
        except ValueError:
            pass


def _emit(match_id: str, event_type: str, data: dict) -> None:
    """Push an SSE event to all connected clients for this match."""
    payload = {"type": event_type, "data": data, "ts": datetime.now(timezone.utc).isoformat()}
    for q in _sse_queues.get(match_id, []):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # slow client, skip


# ─────────────────────────────────────────────
# State transition logic
# ─────────────────────────────────────────────

def _detect_transition(
    rt: MatchRuntime,
    parsed: ParsedMatchState,
    commentary_text: str,
) -> Optional[MatchState]:
    """
    Scan commentary for transition keywords.
    Returns new state if transition found, else None.
    Never auto-advances without commentary confirmation.
    """
    text = commentary_text.lower()

    for keyword, target_state in TRANSITION_KEYWORDS.items():
        if keyword in text:
            # Validate the transition makes sense from current state
            if _valid_transition(rt.state, target_state):
                return target_state

    # Silence rule: if commentary silent 4+ mins after FT confirmed
    if rt.state == MatchState.FT and rt.ft_confirmed_at:
        silence_secs = (datetime.now(timezone.utc) - rt.ft_confirmed_at).total_seconds()
        if silence_secs > 20 * 60:  # 20 min total
            return MatchState.FINISHED

    return None


def _valid_transition(current: MatchState, target: MatchState) -> bool:
    """Enforce valid state progressions."""
    valid_from: dict[MatchState, set[MatchState]] = {
        MatchState.SCHEDULED:   {MatchState.LIVE, MatchState.VOID},
        MatchState.LIVE:        {MatchState.HT, MatchState.FT, MatchState.VOID},
        MatchState.HT:          {MatchState.LIVE_2H, MatchState.VOID},
        MatchState.LIVE_2H:     {MatchState.FT, MatchState.VOID},
        MatchState.FT:          {MatchState.ET_1H, MatchState.FINISHED, MatchState.VOID},
        MatchState.ET_1H:       {MatchState.ET_HT, MatchState.VOID},
        MatchState.ET_HT:       {MatchState.ET_2H, MatchState.VOID},
        MatchState.ET_2H:       {MatchState.FT, MatchState.PENALTIES, MatchState.VOID},
        MatchState.PENALTIES:   {MatchState.FINISHED, MatchState.VOID},
        MatchState.FINISHED:    set(),
        MatchState.VOID:        set(),
    }
    return target in valid_from.get(current, set())


async def _apply_transition(rt: MatchRuntime, new_state: MatchState) -> None:
    """Apply a state transition and fire all side effects."""
    old_state = rt.state
    rt.prev_state = old_state
    rt.state = new_state
    logger.info(f"{rt.match_id}: {old_state} → {new_state}")

    # Emit SSE state change
    _emit(rt.match_id, "state_change", {
        "match_id": rt.match_id,
        "state": new_state,
        "score_home": rt.score_home,
        "score_away": rt.score_away,
        "minute": rt.minute,
    })

    # LIVE — record confidence at kickoff
    if new_state == MatchState.LIVE and old_state == MatchState.SCHEDULED:
        if rt.prediction:
            rt.confidence_at_kickoff = {
                "home_win": rt.prediction.home_win,
                "draw": rt.prediction.draw,
                "away_win": rt.prediction.away_win,
            }

    # FT — record timestamp for silence rule
    if new_state == MatchState.FT:
        rt.ft_confirmed_at = datetime.now(timezone.utc)

    # VOID — lock everything immediately
    if new_state == MatchState.VOID:
        rt.confidence_locked = True
        _emit(rt.match_id, "void", {
            "match_id": rt.match_id,
            "message": "Match void — all predictions cancelled.",
        })
        sheets.save_error({
            "match_id": rt.match_id,
            "layer": "state_machine",
            "error_type": "VOID",
            "message": f"Match void from state {old_state}",
            "resolution": "Awaiting reschedule or cancellation",
            "recovered": False,
        })

    # PENALTIES — open micro-vote
    if new_state == MatchState.PENALTIES:
        _emit(rt.match_id, "penalties_started", {
            "match_id": rt.match_id,
            "score_home": rt.score_home,
            "score_away": rt.score_away,
        })

    # FINISHED — trigger post-match processing
    if new_state == MatchState.FINISHED:
        asyncio.create_task(_post_match(rt))

    # ET states — suspend predictions
    if new_state in (MatchState.ET_1H, MatchState.ET_HT, MatchState.ET_2H):
        _emit(rt.match_id, "extra_time", {
            "match_id": rt.match_id,
            "state": new_state,
        })


# ─────────────────────────────────────────────
# Confidence update logic
# ─────────────────────────────────────────────

def _update_confidence(rt: MatchRuntime, parsed: ParsedMatchState) -> None:
    """
    Update live confidence based on current score + minute.
    Locked at 85 minutes permanently.
    """
    if rt.confidence_locked:
        return
    if rt.state not in (MatchState.LIVE, MatchState.LIVE_2H):
        return
    if parsed.minute >= 85:
        rt.confidence_locked = True
        logger.info(f"{rt.match_id}: Confidence locked at 85'")
        _emit(rt.match_id, "confidence_locked", {"match_id": rt.match_id, "minute": 85})
        return

    if rt.prediction is None:
        return

    # Score differential influences confidence
    goal_diff = (parsed.score_home - parsed.score_away)
    minute = parsed.minute or rt.minute

    # Historical comeback rates (simple heuristic — model handles full version)
    comeback_pct = max(0.02, 0.15 - (minute / 90) * 0.12)

    if goal_diff > 0:
        home_win = min(0.98, rt.prediction.home_win + goal_diff * 0.18 * (minute / 90))
        draw     = max(0.02, rt.prediction.draw - goal_diff * 0.08)
        away_win = max(0.02, 1 - home_win - draw)
    elif goal_diff < 0:
        away_win = min(0.98, rt.prediction.away_win + abs(goal_diff) * 0.18 * (minute / 90))
        draw     = max(0.02, rt.prediction.draw - abs(goal_diff) * 0.08)
        home_win = max(0.02, 1 - away_win - draw)
    else:
        home_win = rt.prediction.home_win
        draw     = rt.prediction.draw
        away_win = rt.prediction.away_win

    # Shift from kickoff
    ko = rt.confidence_at_kickoff or {}
    home_shift = home_win - ko.get("home_win", home_win)

    _emit(rt.match_id, "confidence_update", {
        "match_id": rt.match_id,
        "home_win": round(home_win, 4),
        "draw":     round(draw, 4),
        "away_win": round(away_win, 4),
        "minute":   minute,
        "home_shift_since_kickoff": round(home_shift, 4),
        "locked":   False,
    })


# ─────────────────────────────────────────────
# Source health + alert triggers
# ─────────────────────────────────────────────

async def _check_source_health(
    rt: MatchRuntime,
    results: list[SourceResult],
) -> int:
    """
    Analyse source results.
    Triggers WARNING if 3+ failing, CRITICAL if all failing.
    Returns count of valid sources.
    """
    valid   = [r for r in results if r.ok]
    failed  = [r for r in results if not r.ok]
    blocked = [r for r in results if r.blocked]

    valid_count = len(valid)
    total = len(results)

    # Per-source health logging to Sheets
    for r in results:
        sheets.save_source_health({
            "match_id": rt.match_id,
            "source_name": r.source,
            "status": "ok" if r.ok else ("blocked" if r.blocked else "failed"),
            "http_code": r.http_code,
            "latency_ms": r.latency_ms,
            "timestamp": datetime.now(timezone.utc),
        })

    # Track for research data
    rt.sources_used.extend([r.source for r in valid])
    rt.sources_failed.extend([r.source for r in failed])

    if valid_count == 0:
        # All sources failing → CRITICAL
        if rt.all_sources_down_since is None:
            rt.all_sources_down_since = datetime.now(timezone.utc)
            last_state = (
                f"{rt.score_home}-{rt.score_away} {rt.home.split()[0]} (min {rt.minute})"
                if rt.state in (MatchState.LIVE, MatchState.LIVE_2H) else rt.state
            )
            await alerts.alert_all_sources_down(
                match_info=f"{rt.home} vs {rt.away} ({rt.minute}')",
                last_good_state=last_state,
                down_since=rt.all_sources_down_since,
            )
        # Trigger full group swap
        new_group = swap_group(rt.match_id)
        logger.warning(f"{rt.match_id}: All sources down, swapped to group {new_group}")
        rt.fallbacks_triggered += 1

    elif valid_count < 3:
        # Under-threshold but not zero → WARNING
        failed_names = [r.source for r in failed]
        await alerts.alert_sources_failing(
            failing_count=len(failed),
            total=total,
            match_info=f"{rt.home} vs {rt.away}",
            failed_sources=failed_names,
        )
        rt.fallbacks_triggered += 1
    else:
        # Sources healthy — clear CRITICAL if it was firing
        if rt.all_sources_down_since is not None:
            rt.all_sources_down_since = None
            await alerts.silence_critical("all_sources_down")
            alerts.clear_silence("all_sources_down")

    # Handle blocked sources (swap individual source, not group)
    for r in blocked:
        logger.warning(f"{rt.match_id}: {r.source} blocked (HTTP {r.http_code})")
        sheets.save_error({
            "match_id": rt.match_id,
            "layer": "fetcher",
            "error_type": "BLOCKED",
            "message": f"{r.source} returned {r.http_code}",
            "resolution": "Auto-retry in 6 min, remove for 1h if persistent",
            "recovered": False,
        })

    return valid_count


# ─────────────────────────────────────────────
# Groq quota monitoring
# ─────────────────────────────────────────────

_groq_token_counts: dict[str, int] = {"key1": 0, "key2": 0}
_groq_daily_limit = 500_000  # per key
_groq_warned: set[str] = set()


def record_groq_usage(key_name: str, tokens_used: int) -> None:
    """Called by parser.py after each Groq call."""
    _groq_token_counts[key_name] = _groq_token_counts.get(key_name, 0) + tokens_used


async def check_groq_quotas() -> None:
    """Check Groq usage and fire alerts if needed. Called each fetch cycle."""
    for key_name, used in _groq_token_counts.items():
        pct = (used / _groq_daily_limit) * 100
        if pct >= 100 and key_name not in _groq_warned:
            _groq_warned.add(key_name)
            await alerts.alert_groq_exhausted(
                match_info="All active matches",
                down_since=datetime.now(timezone.utc),
            )
        elif pct >= 80 and f"{key_name}_80" not in _groq_warned:
            _groq_warned.add(f"{key_name}_80")
            await alerts.alert_groq_limit(key_name, pct)


def reset_groq_counts() -> None:
    """Reset at midnight UTC. Called by APScheduler in main.py."""
    _groq_token_counts.clear()
    _groq_warned.clear()
    logger.info("Groq daily token counts reset.")


# ─────────────────────────────────────────────
# Core match loop
# ─────────────────────────────────────────────

async def _match_loop(match_id: str) -> None:
    """
    Main loop for one match. Runs until FINISHED or VOID.
    Fetches → parses → updates state → emits SSE.
    """
    rt = _active[match_id]
    logger.info(f"Match loop started: {match_id} ({rt.home} vs {rt.away})")

    while rt.state not in (MatchState.FINISHED, MatchState.VOID):
        loop_start = time.monotonic()
        active_count = sum(
            1 for r in _active.values()
            if r.state in (MatchState.LIVE, MatchState.LIVE_2H,
                           MatchState.ET_1H, MatchState.ET_2H,
                           MatchState.PENALTIES)
        )
        interval = get_fetch_interval(rt.state, active_count)

        if rt.state == MatchState.SCHEDULED:
            # Just keep-alive — no full fetch needed
            _emit(match_id, "heartbeat", {"match_id": match_id, "state": rt.state})
            await asyncio.sleep(interval)
            continue

        # ── Fetch ─────────────────────────────────────────────────────
        sources = get_sources_for_match(match_id)
        fixture = get_match_info(match_id)

        try:
            results = await fetch_match_data(
                match_id=match_id,
                home=rt.home,
                away=rt.away,
                sources=sources,
            )
        except Exception as e:
            logger.error(f"{match_id}: fetch error — {e}")
            sheets.save_error({
                "match_id": match_id,
                "layer": "fetcher",
                "error_type": type(e).__name__,
                "message": str(e),
                "resolution": "Serving last known good state",
                "recovered": False,
            })
            _emit(match_id, "updating", {"match_id": match_id})
            await asyncio.sleep(interval)
            continue

        rt.fetch_cycles += 1

        # ── Source health check ────────────────────────────────────────
        valid_count = await _check_source_health(rt, results)
        await check_groq_quotas()

        if valid_count == 0 and rt.last_good_state:
            # Serve last known good state
            _emit(match_id, "updating", {"match_id": match_id})
            await asyncio.sleep(interval)
            continue

        # ── Parse ──────────────────────────────────────────────────────
        raw_texts = [r.text for r in results if r.ok]
        if not raw_texts:
            await asyncio.sleep(interval)
            continue

        try:
            parsed: ParsedMatchState = await parse_commentary(
                match_id=match_id,
                home=rt.home,
                away=rt.away,
                raw_texts=raw_texts,
            )
        except Exception as e:
            rt.parse_errors += 1
            logger.error(f"{match_id}: parse error — {e}")
            sheets.save_error({
                "match_id": match_id,
                "layer": "parser",
                "error_type": type(e).__name__,
                "message": str(e),
                "resolution": "Serving last known good state",
                "recovered": False,
            })
            await asyncio.sleep(interval)
            continue

        # ── Hallucination guard ────────────────────────────────────────
        if parsed.score_home > 15 or parsed.score_away > 15:
            rt.hallucinations_caught += 1
            logger.warning(f"{match_id}: Hallucination caught — score {parsed.score_home}-{parsed.score_away}")
            sheets.save_error({
                "match_id": match_id,
                "layer": "parser",
                "error_type": "HALLUCINATION",
                "message": f"Rejected score {parsed.score_home}-{parsed.score_away}",
                "resolution": "Serving last known good state",
                "recovered": True,
            })
            await asyncio.sleep(interval)
            continue

        if parsed.minute and parsed.minute > 130:
            rt.hallucinations_caught += 1
            logger.warning(f"{match_id}: Hallucination caught — minute {parsed.minute}")
            await asyncio.sleep(interval)
            continue

        # ── Update last known good state ───────────────────────────────
        rt.last_good_state = parsed
        rt.last_commentary_at = datetime.now(timezone.utc)

        # ── Update score and minute ────────────────────────────────────
        if parsed.score_home is not None:
            rt.score_home = parsed.score_home
        if parsed.score_away is not None:
            rt.score_away = parsed.score_away
        if parsed.minute:
            rt.minute = parsed.minute

        # ── State transition detection ─────────────────────────────────
        commentary_combined = " ".join(raw_texts).lower()
        new_state = _detect_transition(rt, parsed, commentary_combined)
        if new_state and new_state != rt.state:
            await _apply_transition(rt, new_state)

        # ── Confidence update ──────────────────────────────────────────
        _update_confidence(rt, parsed)

        # ── Save new events ────────────────────────────────────────────
        for event in parsed.events:
            if event not in rt.events:
                rt.events.append(event)
                _emit(match_id, "event", {
                    "match_id": match_id,
                    "event": event,
                    "score_home": rt.score_home,
                    "score_away": rt.score_away,
                })
                # Archive key moments to commentary tab
                if event.get("type") in ("goal", "red_card", "penalty"):
                    sheets.save_commentary({
                        "match_id": match_id,
                        "full_commentary_archive": [],
                        "key_moments": [event],
                        "player_performance_ratings": {},
                    })

        # ── Emit full match update ─────────────────────────────────────
        _emit(match_id, "match_update", {
            "match_id":     match_id,
            "state":        rt.state,
            "score_home":   rt.score_home,
            "score_away":   rt.score_away,
            "minute":       rt.minute,
            "home":         rt.home,
            "away":         rt.away,
            "ai_context":   getattr(parsed, "ai_context", ""),
            "confidence":   {
                "home_win": getattr(rt.prediction, "home_win", None),
                "draw":     getattr(rt.prediction, "draw", None),
                "away_win": getattr(rt.prediction, "away_win", None),
                "locked":   rt.confidence_locked,
            },
            "model_version": get_current_version(),
            "source_used":  parsed.source_used if hasattr(parsed, "source_used") else "",
        })

        # ── Sleep for remaining interval ───────────────────────────────
        elapsed = time.monotonic() - loop_start
        sleep_for = max(0, interval - elapsed)
        await asyncio.sleep(sleep_for)

    logger.info(f"Match loop ended: {match_id} (state={rt.state})")


# ─────────────────────────────────────────────
# Pre-match setup
# ─────────────────────────────────────────────

async def _pre_match(rt: MatchRuntime) -> None:
    """
    Run 3 hours before kickoff.
    Generates AI brief, initial prediction, opens voting.
    """
    logger.info(f"{rt.match_id}: Pre-match preparation starting.")

    # Get initial prediction from model
    try:
        rt.prediction = await predict(
            match_id=rt.match_id,
            home=rt.home,
            away=rt.away,
        )
    except Exception as e:
        logger.error(f"{rt.match_id}: Pre-match prediction failed — {e}")
        sheets.save_error({
            "match_id": rt.match_id,
            "layer": "model",
            "error_type": "PREDICTION_FAILED",
            "message": str(e),
            "resolution": "Will retry at kickoff",
            "recovered": False,
        })

    # Emit pre-match data to frontend
    _emit(rt.match_id, "pre_match", {
        "match_id":    rt.match_id,
        "home":        rt.home,
        "away":        rt.away,
        "kickoff_utc": rt.kickoff_utc.isoformat(),
        "venue":       rt.venue,
        "prediction":  {
            "home_win": getattr(rt.prediction, "home_win", 0.33),
            "draw":     getattr(rt.prediction, "draw", 0.33),
            "away_win": getattr(rt.prediction, "away_win", 0.33),
            "confidence_range": getattr(rt.prediction, "confidence_range", "—"),
        } if rt.prediction else {},
        "pre_match_brief": rt.pre_match_brief,
        "model_version": get_current_version(),
        "voting_open": True,
    })

    logger.info(f"{rt.match_id}: Pre-match setup complete.")


# ─────────────────────────────────────────────
# Post-match processing
# ─────────────────────────────────────────────

async def _post_match(rt: MatchRuntime) -> None:
    """
    Called when match reaches FINISHED state.
    Scores predictions, retrains model, saves full record, generates debrief.
    """
    logger.info(f"{rt.match_id}: Post-match processing starting.")

    # ── Score prediction accuracy ──────────────────────────────────────
    winner = (
        rt.home if rt.score_home > rt.score_away else
        rt.away if rt.score_away > rt.score_home else "draw"
    )
    ai_correct_winner = False
    crowd_correct_winner = False

    if rt.prediction:
        probs = {
            rt.home: rt.prediction.home_win,
            "draw": rt.prediction.draw,
            rt.away: rt.prediction.away_win,
        }
        ai_predicted = max(probs, key=probs.get)
        ai_correct_winner = (ai_predicted == winner) or (
            ai_predicted == "draw" and winner == "draw"
        )

    # ── Confidence calibration error ──────────────────────────────────
    if rt.prediction and rt.confidence_at_kickoff:
        if winner == rt.home:
            actual = 1.0
            predicted = rt.confidence_at_kickoff.get("home_win", 0.5)
        elif winner == rt.away:
            actual = 1.0
            predicted = rt.confidence_at_kickoff.get("away_win", 0.5)
        else:
            actual = 1.0
            predicted = rt.confidence_at_kickoff.get("draw", 0.33)
        calibration_error = abs(actual - predicted)
    else:
        calibration_error = 0.0

    # ── Retrain model ──────────────────────────────────────────────────
    train_start = time.monotonic()
    accuracy_before = get_accuracy()
    deployed = False
    mlflow_run_id = ""
    improvement_pct = 0.0
    feature_importances = {}

    try:
        retrain_result = await retrain(match_id=rt.match_id)
        duration_s = time.monotonic() - train_start
        accuracy_after = retrain_result.accuracy_after
        improvement_pct = accuracy_after - accuracy_before
        threshold = get_retrain_threshold(rt.match_id)
        deployed = improvement_pct >= threshold
        mlflow_run_id = retrain_result.run_id
        feature_importances = retrain_result.feature_importances

        # Alert if too slow
        if duration_s > 600:
            await alerts.alert_retrain_slow(duration_s, rt.match_id)

        # Notify retrain result
        await alerts.alert_retrain_result(
            match_id=rt.match_id,
            version=get_current_version(),
            accuracy_before=accuracy_before,
            accuracy_after=accuracy_after,
            improvement=improvement_pct,
            deployed=deployed,
            threshold=threshold,
            duration_s=duration_s,
        )

        # Save MLflow summary to Sheets
        sheets.save_model_run({
            "run_id":              mlflow_run_id,
            "match_id":            rt.match_id,
            "version":             get_current_version(),
            "accuracy_before":     accuracy_before,
            "accuracy_after":      accuracy_after,
            "improvement_pct":     improvement_pct,
            "deploy_decision":     deployed,
            "training_duration_s": duration_s,
            "feature_importances": feature_importances,
        })

    except Exception as e:
        logger.error(f"{rt.match_id}: Retrain failed — {e}")
        await alerts.send_warning(
            f"Retrain failed for {rt.match_id}",
            context={"error": str(e)},
        )
        sheets.save_error({
            "match_id": rt.match_id,
            "layer": "model",
            "error_type": "RETRAIN_FAILED",
            "message": str(e),
            "resolution": "Keeping current weights",
            "recovered": False,
        })

    # ── Build + save full match record ─────────────────────────────────
    match_record = sheets.build_match_record(
        match_id=rt.match_id,
        model_version=get_current_version(),
        phase=rt.phase,
        group=rt.group,
        venue=rt.venue,
        kickoff_utc=rt.kickoff_utc,
        home_team=rt.home,
        away_team=rt.away,
        home_squad=[],  # populated from squads file in model.py
        away_squad=[],
        home_manager="",
        away_manager="",
        home_fifa_rank=0,
        away_fifa_rank=0,
        home_elo=0.0,
        away_elo=0.0,
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
        scorers=[e["player"] for e in rt.events if e.get("type") == "goal"],
        assisters=[e.get("assister", "") for e in rt.events if e.get("type") == "goal"],
        cards=[e for e in rt.events if "card" in e.get("type", "")],
        substitutions=[e for e in rt.events if e.get("type") == "sub"],
        first_goal_minute=next((e["minute"] for e in rt.events if e.get("type") == "goal"), None),
        final_whistle_minute=rt.minute,
        injury_time_1h=0,
        injury_time_2h=0,
        went_to_et=rt.state in (MatchState.ET_1H, MatchState.ET_HT, MatchState.ET_2H, MatchState.FINISHED),
        went_to_penalties=any(
            rt.prev_state == MatchState.PENALTIES or rt.state == MatchState.PENALTIES
            for _ in [1]
        ),
        total_duration_min=rt.minute,
        regulation_duration_min=min(rt.minute, 90),
        ai_correct_winner=ai_correct_winner,
        ai_correct_scorer=False,  # scorer accuracy tracked separately
        ai_confidence_at_lock=getattr(rt.prediction, "home_win", 0.0),
        confidence_calibration_error=calibration_error,
        pre_match_brief=rt.pre_match_brief,
        post_match_debrief=rt.post_match_debrief,
        what_model_got_wrong="",
        model_updates_after={},
        total_votes=0,  # populated from DB by main.py
        verified_votes=0,
        vote_distribution={},
        crowd_correct_winner=crowd_correct_winner,
        vote_timeline=[],
        votes_by_phase={},
        sources_used=list(set(rt.sources_used)),
        sources_failed=list(set(rt.sources_failed)),
        fetch_cycles=rt.fetch_cycles,
        parse_errors=rt.parse_errors,
        hallucinations_caught=rt.hallucinations_caught,
        fallbacks_triggered=rt.fallbacks_triggered,
        mlflow_run_id=mlflow_run_id,
        accuracy_before=accuracy_before,
        accuracy_after=accuracy_before + improvement_pct,
        improvement_pct=improvement_pct,
        deploy_decision=deployed,
        training_duration_s=time.monotonic() - train_start,
        feature_importances=feature_importances,
        full_commentary_archive=[],
        key_moments=[e for e in rt.events if e.get("type") in ("goal", "red_card", "penalty")],
        player_performance_ratings={},
    )
    sheets.save_match(match_record)

    # ── Emit final result ──────────────────────────────────────────────
    _emit(rt.match_id, "finished", {
        "match_id":         rt.match_id,
        "score_home":       rt.score_home,
        "score_away":       rt.score_away,
        "winner":           winner,
        "ai_correct":       ai_correct_winner,
        "model_version":    get_current_version(),
        "post_match_debrief": rt.post_match_debrief,
    })

    # ── Deploy window suggestion ───────────────────────────────────────
    safe, reason = safe_to_deploy()
    await alerts.alert_deploy_window(safe, reason)

    # ── Generate + send daily post draft ──────────────────────────────
    linkedin, x_post = _generate_daily_post(rt, winner, ai_correct_winner)
    await alerts.alert_daily_post(linkedin, x_post)

    logger.info(f"{rt.match_id}: Post-match processing complete.")

    # Remove from active
    _active.pop(rt.match_id, None)


# ─────────────────────────────────────────────
# Daily post generation
# ─────────────────────────────────────────────

def _generate_daily_post(
    rt: MatchRuntime,
    winner: str,
    ai_correct: bool,
) -> tuple[str, str]:
    """Generate LinkedIn + X post drafts for Telegram."""
    pred = rt.prediction
    home_pct  = f"{getattr(pred, 'home_win', 0)*100:.0f}%" if pred else "—"
    draw_pct  = f"{getattr(pred, 'draw', 0)*100:.0f}%"     if pred else "—"
    away_pct  = f"{getattr(pred, 'away_win', 0)*100:.0f}%" if pred else "—"
    result_emoji = "✅" if ai_correct else "❌"
    version = get_current_version()

    linkedin = (
        f"{rt.home} {rt.score_home}-{rt.score_away} {rt.away}\n"
        f"AI predicted: {home_pct} · Draw {draw_pct} · {away_pct}\n"
        f"Result: {result_emoji} {'correct' if ai_correct else 'wrong'}\n"
        f"Why {'right' if ai_correct else 'wrong'}:\n"
        f"→ [reason 1]\n"
        f"→ [reason 2]\n"
        f"→ [reason 3]\n"
        f"What updated:\n"
        f"→ [team] rating [+/-X]\n"
        f"→ [player] scorer probability [+/-X%]\n"
        f"→ [what model now knows]\n"
        f"Model v{version} — trained on {rt.match_id[-3:]} matches.\n"
        f"#WorldCup2026 #AI #buildinpublic"
    )

    x_post = (
        f"🏆 {rt.home} {rt.score_home}-{rt.score_away} {rt.away}\n"
        f"AI: {home_pct}/{draw_pct}/{away_pct} → {result_emoji}\n"
        f"Model v{version} | delta.vercel.app\n"
        f"#WorldCup2026 #AI"
    )

    return linkedin, x_post


# ─────────────────────────────────────────────
# Public API (called by main.py)
# ─────────────────────────────────────────────

async def schedule_match(match_id: str) -> None:
    """
    Register a match for monitoring. Call when voting opens (3h before kickoff).
    Creates MatchRuntime, starts pre-match prep, schedules the loop.
    """
    if match_id in _active:
        logger.warning(f"{match_id}: Already active, skipping.")
        return

    fixture = get_match_info(match_id)
    if not fixture:
        logger.error(f"schedule_match: Unknown match_id {match_id}")
        return

    rt = MatchRuntime(match_id)
    _active[match_id] = rt

    # Run pre-match setup
    await _pre_match(rt)

    # Wait for kickoff, then start loop
    async def _wait_then_start():
        now = datetime.now(timezone.utc)
        wait = (rt.kickoff_utc - now).total_seconds()
        if wait > 0:
            logger.info(f"{match_id}: Waiting {wait:.0f}s for kickoff.")
            await asyncio.sleep(wait)
        await _apply_transition(rt, MatchState.LIVE)
        await _match_loop(match_id)

    rt.task = asyncio.create_task(_wait_then_start())
    logger.info(f"{match_id}: Scheduled. Kickoff in {(rt.kickoff_utc - datetime.now(timezone.utc)).total_seconds()/60:.0f} min.")


async def void_match(match_id: str) -> None:
    """
    Force-void a match (abandoned/postponed). Called from admin or state machine.
    """
    rt = _active.get(match_id)
    if not rt:
        logger.warning(f"void_match: {match_id} not active.")
        return
    await _apply_transition(rt, MatchState.VOID)
    if rt.task:
        rt.task.cancel()


async def reschedule_voided_match(match_id: str, new_kickoff_utc: str) -> None:
    """
    Treat a rescheduled void as a brand new match.
    Clears old votes (handled in main.py), resets state.
    """
    _active.pop(match_id, None)
    if match_id in FIXTURE_BY_ID:
        FIXTURE_BY_ID[match_id]["kickoff_utc"] = new_kickoff_utc
    await schedule_match(match_id)
    logger.info(f"{match_id}: Rescheduled → {new_kickoff_utc}")


async def trigger_retrain(match_id: str) -> dict:
    """
    Admin panel 'Retrain Model' button handler.
    Returns result dict.
    """
    try:
        accuracy_before = get_accuracy()
        retrain_result = await retrain(match_id=match_id)
        improvement = retrain_result.accuracy_after - accuracy_before
        threshold = get_retrain_threshold(match_id)
        deployed = improvement >= threshold

        await alerts.alert_retrain_result(
            match_id=match_id,
            version=get_current_version(),
            accuracy_before=accuracy_before,
            accuracy_after=retrain_result.accuracy_after,
            improvement=improvement,
            deployed=deployed,
            threshold=threshold,
            duration_s=retrain_result.duration_s,
        )
        sheets.save_model_run({
            "run_id":              retrain_result.run_id,
            "match_id":            match_id,
            "version":             get_current_version(),
            "accuracy_before":     accuracy_before,
            "accuracy_after":      retrain_result.accuracy_after,
            "improvement_pct":     improvement,
            "deploy_decision":     deployed,
            "training_duration_s": retrain_result.duration_s,
            "feature_importances": retrain_result.feature_importances,
        })
        return {
            "success":         True,
            "deployed":        deployed,
            "accuracy_before": accuracy_before,
            "accuracy_after":  retrain_result.accuracy_after,
            "improvement":     improvement,
            "threshold":       threshold,
            "version":         get_current_version(),
        }
    except Exception as e:
        logger.error(f"Admin retrain failed: {e}")
        return {"success": False, "error": str(e)}


def get_all_match_states() -> list[dict]:
    """Return current state of all active matches. Used by admin dashboard."""
    return [
        {
            "match_id":     rt.match_id,
            "home":         rt.home,
            "away":         rt.away,
            "state":        rt.state,
            "score_home":   rt.score_home,
            "score_away":   rt.score_away,
            "minute":       rt.minute,
            "fetch_cycles": rt.fetch_cycles,
            "parse_errors": rt.parse_errors,
            "fallbacks":    rt.fallbacks_triggered,
            "confidence_locked": rt.confidence_locked,
        }
        for rt in _active.values()
    ]


def get_match_state(match_id: str) -> Optional[dict]:
    """Return state for a specific match."""
    rt = _active.get(match_id)
    if not rt:
        return None
    return {
        "match_id":     rt.match_id,
        "home":         rt.home,
        "away":         rt.away,
        "state":        rt.state,
        "score_home":   rt.score_home,
        "score_away":   rt.score_away,
        "minute":       rt.minute,
        "events":       rt.events[-20:],  # last 20 events
        "prediction": {
            "home_win": getattr(rt.prediction, "home_win", None),
            "draw":     getattr(rt.prediction, "draw", None),
            "away_win": getattr(rt.prediction, "away_win", None),
            "confidence_range": getattr(rt.prediction, "confidence_range", None),
            "locked": rt.confidence_locked,
        },
        "model_version": get_current_version(),
    }


async def startup() -> None:
    """
    Called by main.py on app startup.
    Loads fixtures state, assigns today's groups, starts Telegram poller.
    """
    from fixtures import load_state
    load_state()
    await sheets.start_queue_worker()
    asyncio.create_task(alerts.poll_stop_commands())

    # Assign source groups for today
    todays = get_todays_matches()
    if todays:
        ids = [f["match_id"] for f in todays]
        assign_groups_for_day(ids)
        logger.info(f"Today's matches: {[f['home']+' vs '+f['away'] for f in todays]}")

    logger.info("Pipeline startup complete.")


async def shutdown() -> None:
    """Graceful shutdown — drain Sheets queue, cancel tasks."""
    for rt in list(_active.values()):
        if rt.task and not rt.task.done():
            rt.task.cancel()
    await sheets.stop_queue_worker()
    logger.info("Pipeline shutdown complete.")
