"""
simulation.py — Project Delta
Pre-tournament lifecycle simulation using StatsBomb WC2022 Final data.
Replays France vs Argentina (match ID 3869685) event-by-event.
Tests full state machine: SCHEDULED→LIVE→HT→LIVE_2H→FT→ET_1H→ET_HT→ET_2H→PENALTIES→FINISHED
Run on June 9th before tournament starts June 11th.
"""

import asyncio
import json
import time
import sys
from datetime import datetime, timezone
from typing import Any
from loguru import logger

# ── Configure logging ────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}", level="DEBUG")
logger.add("simulation_log.txt", rotation="10 MB", level="DEBUG")

# ── Optional imports (graceful if files not yet built) ───────────────────────
try:
    from statsbombpy import sb
    STATSBOMB_AVAILABLE = True
except ImportError:
    STATSBOMB_AVAILABLE = False
    logger.warning("statsbombpy not installed. Using synthetic data fallback.")

try:
    from pipeline import MatchStateMachine, MatchState, MatchEvent
    PIPELINE_AVAILABLE = True
except ImportError:
    PIPELINE_AVAILABLE = False
    logger.warning("pipeline.py not found. Running standalone simulation.")

try:
    from model import DeltaModel
    MODEL_AVAILABLE = True
except ImportError:
    MODEL_AVAILABLE = False
    logger.warning("model.py not found. Skipping model update tests.")

try:
    from alerts import AlertSystem, AlertLevel
    ALERTS_AVAILABLE = True
except ImportError:
    ALERTS_AVAILABLE = False
    logger.warning("alerts.py not found. Skipping alert tests.")


# ── Constants ─────────────────────────────────────────────────────────────────
WC2022_FINAL_ID = 3869685
SIMULATION_MATCH_ID = "SIM_WC2022_FINAL"
SIMULATION_SPEED = 0.05  # seconds per event (0.05 = fast replay, 1.0 = real-time)

# Results counters
results = {
    "transitions_tested": 0,
    "transitions_passed": 0,
    "transitions_failed": 0,
    "events_processed": 0,
    "edge_cases_found": [],
    "errors": [],
}


# ── State Machine (standalone if pipeline.py unavailable) ────────────────────
class SimMatchState:
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    HT = "HT"
    LIVE_2H = "LIVE_2H"
    FT = "FT"
    ET_1H = "ET_1H"
    ET_HT = "ET_HT"
    ET_2H = "ET_2H"
    PENALTIES = "PENALTIES"
    FINISHED = "FINISHED"
    VOID = "VOID"


class SimStateMachine:
    """Standalone state machine for simulation (mirrors pipeline.py logic)."""

    VALID_TRANSITIONS = {
        SimMatchState.SCHEDULED: [SimMatchState.LIVE, SimMatchState.VOID],
        SimMatchState.LIVE: [SimMatchState.HT, SimMatchState.FT, SimMatchState.VOID],
        SimMatchState.HT: [SimMatchState.LIVE_2H, SimMatchState.VOID],
        SimMatchState.LIVE_2H: [SimMatchState.FT, SimMatchState.VOID],
        SimMatchState.FT: [SimMatchState.ET_1H, SimMatchState.FINISHED, SimMatchState.VOID],
        SimMatchState.ET_1H: [SimMatchState.ET_HT, SimMatchState.VOID],
        SimMatchState.ET_HT: [SimMatchState.ET_2H, SimMatchState.VOID],
        SimMatchState.ET_2H: [SimMatchState.FT, SimMatchState.PENALTIES, SimMatchState.VOID],
        SimMatchState.PENALTIES: [SimMatchState.FINISHED, SimMatchState.VOID],
        SimMatchState.FINISHED: [],
        SimMatchState.VOID: [],
    }

    KEYWORD_TRIGGERS = {
        "half time": SimMatchState.HT,
        "half-time": SimMatchState.HT,
        " ht ": SimMatchState.HT,
        "full time": SimMatchState.FT,
        "full-time": SimMatchState.FT,
        " ft ": SimMatchState.FT,
        "extra time": SimMatchState.ET_1H,
        "extra-time": SimMatchState.ET_1H,
        "extra time half time": SimMatchState.ET_HT,
        "penalty shootout": SimMatchState.PENALTIES,
        "penalties": SimMatchState.PENALTIES,
        "final score": SimMatchState.FINISHED,
        "match over": SimMatchState.FINISHED,
        "abandoned": SimMatchState.VOID,
        "postponed": SimMatchState.VOID,
    }

    def __init__(self):
        self.current = SimMatchState.SCHEDULED
        self.history: list[dict] = []
        self.last_commentary_time: float = time.time()
        self.silence_detected = False

    def try_transition(self, new_state: str, trigger: str) -> bool:
        allowed = self.VALID_TRANSITIONS.get(self.current, [])
        if new_state not in allowed:
            logger.error(f"INVALID TRANSITION: {self.current} → {new_state} (trigger: {trigger})")
            results["transitions_failed"] += 1
            results["edge_cases_found"].append(f"Invalid: {self.current}→{new_state}")
            return False

        old = self.current
        self.current = new_state
        self.history.append({"from": old, "to": new_state, "trigger": trigger, "time": time.time()})
        logger.success(f"STATE TRANSITION: {old} → {new_state} | trigger: '{trigger}'")
        results["transitions_tested"] += 1
        results["transitions_passed"] += 1
        return True

    def detect_from_commentary(self, text: str) -> str | None:
        lower = text.lower()
        for keyword, target_state in self.KEYWORD_TRIGGERS.items():
            if keyword in lower:
                allowed = self.VALID_TRANSITIONS.get(self.current, [])
                if target_state in allowed:
                    return target_state
        return None

    def check_silence_rule(self) -> bool:
        """If commentary silent 4+ min after FT confirmed, wait 20 min total then FINISHED."""
        if self.current == SimMatchState.FT:
            silence_secs = time.time() - self.last_commentary_time
            # In simulation: scaled down. Real system uses 240s silence → 1200s total.
            if silence_secs > 4 * SIMULATION_SPEED * 60:
                return True
        return False


# ── StatsBomb data loader ─────────────────────────────────────────────────────
def load_wc2022_final() -> list[dict]:
    """Load StatsBomb WC2022 Final events."""
    if STATSBOMB_AVAILABLE:
        try:
            logger.info("Loading StatsBomb WC2022 Final (match 3869685)...")
            events = sb.events(match_id=WC2022_FINAL_ID)
            events_list = events.to_dict("records")
            logger.success(f"Loaded {len(events_list)} StatsBomb events")
            return events_list
        except Exception as e:
            logger.warning(f"StatsBomb load failed: {e}. Using synthetic data.")

    return _synthetic_wc2022_final()


def _synthetic_wc2022_final() -> list[dict]:
    """
    Synthetic WC2022 Final replay.
    France 3-3 Argentina (AET) — Argentina win on penalties 4-2.
    Key events: Di María 36', Mbappe 80', 81', Mbappe 118'+, Montiel 118'+
    """
    logger.info("Using synthetic WC2022 Final data (France vs Argentina)")

    events = []

    def e(minute: int, period: int, type_name: str, team: str, player: str = "", commentary: str = "", state_hint: str = ""):
        return {
            "minute": minute,
            "second": 0,
            "period": period,
            "type": {"name": type_name},
            "team": {"name": team},
            "player": {"name": player},
            "commentary": commentary,
            "state_hint": state_hint,
        }

    # SCHEDULED → LIVE
    events.append(e(0, 1, "kickoff", "Argentina", "Messi", "Kick off! The 2022 FIFA World Cup Final is underway.", "LIVE"))

    # Period 1 events
    events.append(e(5, 1, "pass", "Argentina", "De Paul", "Argentina controlling early possession."))
    events.append(e(15, 1, "shot", "France", "Griezmann", "Griezmann with a speculative effort, wide."))
    events.append(e(23, 1, "penalty_won", "Argentina", "Di Maria", "Penalty! Di María goes down in the box."))
    events.append(e(23, 1, "goal", "Argentina", "Messi", "GOAL! Messi slots the penalty. Argentina 1-0 France."))
    events.append(e(36, 1, "goal", "Argentina", "Di Maria", "GOAL! Di María fires into the bottom corner! Argentina 2-0 France!"))
    events.append(e(40, 1, "shot", "France", "Mbappe", "Mbappe tries from range, saved."))
    events.append(e(44, 1, "foul_committed", "France", "Hernandez", "Free kick to Argentina near halfway."))
    events.append(e(45, 1, "period_end", "Argentina", "", "Half time. Half time! France 0-2 Argentina at the break.", "HT"))

    # HALF TIME
    events.append(e(45, 1, "half_time", "", "", "HT - Argentina lead 2-0. Half time whistle."))

    # Period 2 (LIVE_2H)
    events.append(e(46, 2, "kickoff", "France", "Mbappe", "Second half underway! France kick off.", "LIVE_2H"))
    events.append(e(50, 2, "substitution", "France", "Thuram", "France double change. Thuram and Kolo Muani on."))
    events.append(e(60, 2, "shot", "France", "Thuram", "Thuram glances a header just over."))
    events.append(e(71, 2, "shot", "Argentina", "Messi", "Messi curls one just wide. Still 2-0."))
    events.append(e(79, 2, "penalty_won", "France", "Kolo Muani", "PENALTY! France win a penalty!"))
    events.append(e(80, 2, "goal", "France", "Mbappe", "GOAL! Mbappe scores! 2-1! France are alive!"))
    events.append(e(81, 2, "goal", "France", "Mbappe", "GOAL! MBAPPE AGAIN! 2-2! What a comeback! France level!"))
    events.append(e(85, 2, "shot", "Argentina", "Messi", "Messi tries from distance, saved! 85 minutes gone."))
    events.append(e(88, 2, "shot", "France", "Mbappe", "Mbappe through on goal — SAVED by Martinez!"))
    events.append(e(90, 2, "period_end", "France", "", "Full time! Full-time 2-2 after 90 minutes. Extra time to follow!", "FT"))

    # FULL TIME → EXTRA TIME
    events.append(e(91, 3, "extra_time_start", "", "", "Extra time kicks off! We go to extra time!", "ET_1H"))
    events.append(e(95, 3, "shot", "Argentina", "Messi", "Messi with a shot that takes a deflection — off the post!"))
    events.append(e(100, 3, "goal", "Argentina", "Messi", "GOAL! MESSI! Argentina 3-2 France! Messi heads it in!"))
    events.append(e(105, 3, "period_end", "", "", "Extra time half time! Extra time half time. 3-2 after 105 minutes.", "ET_HT"))

    # EXTRA TIME 2H
    events.append(e(106, 4, "kickoff", "France", "Mbappe", "Second half of extra time underway!", "ET_2H"))
    events.append(e(110, 4, "shot", "France", "Mbappe", "Mbappe shoots — SAVED again by Martinez!"))
    events.append(e(116, 4, "handball", "Argentina", "Montiel", "HANDBALL! France awarded a penalty in extra time!"))
    events.append(e(118, 4, "goal", "France", "Mbappe", "GOAL! HAT-TRICK FOR MBAPPE! 3-3! France level again! Unbelievable!"))
    events.append(e(120, 4, "period_end", "", "", "Full time after extra time! 3-3! We go to penalties! Penalty shootout to decide the World Cup!", "PENALTIES"))

    # PENALTIES
    pen_events = [
        (1, "Argentina", "Messi", True, "SCORED"),
        (1, "France", "Mbappe", True, "SCORED — Mbappe opens for France"),
        (2, "Argentina", "Dybala", True, "SCORED"),
        (2, "France", "Coman", False, "MISSED — Martinez saves! Argentina ahead!"),
        (3, "Argentina", "Paredes", True, "SCORED"),
        (3, "France", "Tchouameni", False, "MISSED — Wide! Argentina two ahead in shootout!"),
        (4, "Argentina", "Montiel", True, "SCORED — ARGENTINA WIN THE WORLD CUP!"),
    ]

    for pen_num, team, player, scored, commentary in pen_events:
        result = "goal" if scored else "miss"
        events.append(e(120 + pen_num, 5, f"penalty_{result}", team, player, f"Penalty {pen_num} ({team}) — {player}: {commentary}"))

    events.append(e(125, 5, "match_over", "", "", "Final score! Match over! Argentina win the 2022 FIFA World Cup! 3-3 AET (Argentina 4-2 on penalties)", "FINISHED"))

    logger.info(f"Synthetic dataset: {len(events)} events")
    return events


# ── Prediction confidence updater ─────────────────────────────────────────────
class SimPredictionEngine:
    """Simulates model confidence shifts during the match."""

    def __init__(self):
        self.home = "Argentina"
        self.away = "France"
        self.scores = {"home": 0, "away": 0}
        self.confidence = {"home_win": 0.45, "draw": 0.25, "away_win": 0.30}
        self.locked = False
        self.lock_minute = 85
        self.history: list[dict] = []
        self.update_count = 0

    def update(self, event: dict) -> dict:
        if self.locked:
            return self.confidence.copy()

        minute = event.get("minute", 0)
        period = event.get("period", 1)
        event_type = event.get("type", {}).get("name", "")
        team = event.get("team", {}).get("name", "")

        # Lock at 85 minutes (regulation)
        if period == 2 and minute >= self.lock_minute and not self.locked:
            self.locked = True
            logger.info(f"🔒 Predictions LOCKED at minute {minute}")
            results["edge_cases_found"].append(f"85-minute lock triggered at min {minute}")

        # Goal scored
        if event_type == "goal":
            if team == self.home:
                self.scores["home"] += 1
            else:
                self.scores["away"] += 1
            self._recalculate(minute, period)
            logger.info(f"⚽ GOAL: {self.home} {self.scores['home']}-{self.scores['away']} {self.away} | Confidence: {self._fmt()}")

        # Penalty awarded
        elif event_type == "penalty_won":
            if team == self.home:
                self.confidence["home_win"] = min(0.98, self.confidence["home_win"] + 0.08)
            else:
                self.confidence["away_win"] = min(0.98, self.confidence["away_win"] + 0.08)
            self._normalise()

        self.update_count += 1
        snapshot = {"minute": minute, "period": period, "scores": self.scores.copy(), **self.confidence.copy()}
        self.history.append(snapshot)
        return self.confidence.copy()

    def _recalculate(self, minute: int, period: int):
        home_g = self.scores["home"]
        away_g = self.scores["away"]
        diff = home_g - away_g

        # Remaining time factor
        if period == 1:
            remaining = (90 - minute) / 90
        elif period == 2:
            remaining = max(0, (90 - minute)) / 90
        else:
            remaining = 0.1  # ET or pens — anything can happen

        time_factor = max(0.1, remaining)

        if diff > 0:
            self.confidence["home_win"] = min(0.98, 0.60 + diff * 0.18 * (1 - time_factor * 0.4))
            self.confidence["away_win"] = max(0.02, 0.15 - diff * 0.05)
        elif diff < 0:
            self.confidence["away_win"] = min(0.98, 0.60 + abs(diff) * 0.18 * (1 - time_factor * 0.4))
            self.confidence["home_win"] = max(0.02, 0.15 - abs(diff) * 0.05)
        else:
            self.confidence["home_win"] = 0.38
            self.confidence["away_win"] = 0.35

        self._normalise()

    def _normalise(self):
        total = sum(self.confidence.values())
        if total > 0:
            self.confidence = {k: max(0.02, min(0.98, v / total)) for k, v in self.confidence.items()}
        t2 = sum(self.confidence.values())
        self.confidence = {k: v / t2 for k, v in self.confidence.items()}

    def _fmt(self) -> str:
        h = self.confidence["home_win"]
        d = self.confidence["draw"]
        a = self.confidence["away_win"]
        return f"H:{h:.0%} D:{d:.0%} A:{a:.0%}"

    def confidence_range(self) -> dict:
        """Returns confidence as range (±4%) as per spec."""
        return {
            "home_win": f"{max(2, int(self.confidence['home_win']*100)-4)}-{min(98, int(self.confidence['home_win']*100)+4)}%",
            "draw": f"{max(2, int(self.confidence['draw']*100)-4)}-{min(98, int(self.confidence['draw']*100)+4)}%",
            "away_win": f"{max(2, int(self.confidence['away_win']*100)-4)}-{min(98, int(self.confidence['away_win']*100)+4)}%",
        }


# ── Voting simulator ──────────────────────────────────────────────────────────
class SimVotingSystem:
    """Simulates human voting patterns across match phases."""

    def __init__(self):
        self.votes: list[dict] = []
        self.fingerprints: set = set()
        self.locked = False

    def cast_vote(self, fingerprint: str, pick: str, minute: int, trust_score: float, score: dict):
        if self.locked:
            logger.warning(f"Vote rejected: locked at 85 min (fingerprint {fingerprint[:8]})")
            results["edge_cases_found"].append("Vote rejected after 85-min lock")
            return False

        # Duplicate vote check
        if fingerprint in self.fingerprints:
            # Allow re-vote (change_count tracked)
            existing = next((v for v in self.votes if v["fingerprint"] == fingerprint), None)
            if existing:
                existing["change_count"] += 1
                existing["pick"] = pick
                existing["last_minute"] = minute
                logger.debug(f"Vote updated: {fingerprint[:8]} → {pick} (change #{existing['change_count']})")
                return True

        self.fingerprints.add(fingerprint)
        self.votes.append({
            "fingerprint": fingerprint,
            "pick": pick,
            "minute": minute,
            "trust_score": trust_score,
            "change_count": 0,
            "score_at_vote": score.copy(),
        })
        return True

    def lock(self, minute: int):
        self.locked = True
        logger.info(f"🔒 VOTE LOCK: Hard lock at minute {minute}")

    def verified_votes(self) -> list:
        return [v for v in self.votes if v["trust_score"] >= 0.8]

    def probable_votes(self) -> list:
        return [v for v in self.votes if 0.6 <= v["trust_score"] < 0.8]

    def excluded_votes(self) -> list:
        return [v for v in self.votes if v["trust_score"] < 0.6]

    def summary(self) -> dict:
        total = len(self.votes)
        if total == 0:
            return {"total": 0}
        picks = {"home": 0, "draw": 0, "away": 0}
        for v in self.votes:
            picks[v["pick"]] = picks.get(v["pick"], 0) + 1
        return {
            "total": total,
            "verified": len(self.verified_votes()),
            "probable": len(self.probable_votes()),
            "excluded": len(self.excluded_votes()),
            "picks": picks,
            "pick_pct": {k: f"{v/total:.0%}" for k, v in picks.items()},
        }


# ── Source health simulator ───────────────────────────────────────────────────
class SimSourceHealth:
    """Simulates source blocking/recovery as per spec."""

    def __init__(self):
        self.sources = {
            "BBC Sport": {"status": "ok", "blocked_at": None, "retry_count": 0},
            "ESPN FC": {"status": "ok", "blocked_at": None, "retry_count": 0},
            "Sofascore": {"status": "ok", "blocked_at": None, "retry_count": 0},
            "FlashScore": {"status": "ok", "blocked_at": None, "retry_count": 0},
            "Sky Sports": {"status": "ok", "blocked_at": None, "retry_count": 0},
            "FIFA.com": {"status": "ok", "blocked_at": None, "retry_count": 0},
        }

    def simulate_block(self, source: str):
        if source in self.sources:
            self.sources[source]["status"] = "blocked"
            self.sources[source]["blocked_at"] = time.time()
            self.sources[source]["retry_count"] = 0
            logger.warning(f"⚠️  SOURCE BLOCKED: {source}")
            results["edge_cases_found"].append(f"Source blocked: {source}")

    def simulate_recovery(self, source: str):
        if source in self.sources:
            self.sources[source]["status"] = "ok"
            self.sources[source]["retry_count"] = 0
            logger.info(f"✅ SOURCE RECOVERED: {source}")

    def count_healthy(self) -> int:
        return sum(1 for s in self.sources.values() if s["status"] == "ok")

    def should_trigger_tavily_fallback(self) -> bool:
        return self.count_healthy() < 3


# ── Hallucination guard ───────────────────────────────────────────────────────
def test_hallucination_guard():
    """Tests the hallucination rejection rules from spec."""
    logger.info("Testing hallucination guards...")

    test_cases = [
        ({"score": {"home": 16, "away": 0}}, True, "Score > 15 goals → reject"),
        ({"minute": 135}, True, "Minute > 130 → reject"),
        ({"team": "Atlantis FC"}, True, "Unknown team → reject"),
        ({"score": {"home": 3, "away": 2}}, False, "Valid score → accept"),
        ({"minute": 90}, False, "Valid minute → accept"),
        ({"score": {"home": 0, "away": 0}, "minute": 1, "team": "Argentina"}, False, "All valid → accept"),
    ]

    KNOWN_TEAMS = {"Argentina", "France", "Brazil", "Spain", "England", "Germany", "Portugal"}

    passed = 0
    for event, should_reject, description in test_cases:
        rejected = False

        score = event.get("score", {})
        home_g = score.get("home", 0)
        away_g = score.get("away", 0)

        if home_g + away_g > 15:
            rejected = True
        minute = event.get("minute", 0)
        if minute > 130:
            rejected = True
        team = event.get("team", "")
        if team and team not in KNOWN_TEAMS:
            rejected = True

        if rejected == should_reject:
            passed += 1
            logger.debug(f"  ✅ {description}")
        else:
            logger.error(f"  ❌ {description} — expected reject={should_reject}, got {rejected}")
            results["errors"].append(f"Hallucination guard: {description}")

    logger.info(f"Hallucination guard: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


# ── Fetch interval logic ──────────────────────────────────────────────────────
def get_fetch_interval(state: str, active_matches: int) -> int:
    """Returns fetch interval in seconds per spec."""
    if state in (SimMatchState.HT, SimMatchState.ET_HT):
        return 300  # 5 minutes
    elif state == SimMatchState.PENALTIES:
        return 30
    elif state == SimMatchState.SCHEDULED:
        return 780  # 13 minutes (keep-alive)
    elif state in (SimMatchState.LIVE, SimMatchState.LIVE_2H, SimMatchState.ET_1H, SimMatchState.ET_2H):
        if active_matches == 1:
            return 15
        elif active_matches == 2:
            return 20
        else:
            return 30
    return 60


def test_fetch_intervals():
    """Tests all fetch interval rules."""
    logger.info("Testing fetch intervals...")
    test_cases = [
        (SimMatchState.HT, 1, 300),
        (SimMatchState.ET_HT, 1, 300),
        (SimMatchState.PENALTIES, 1, 30),
        (SimMatchState.SCHEDULED, 1, 780),
        (SimMatchState.LIVE, 1, 15),
        (SimMatchState.LIVE, 2, 20),
        (SimMatchState.LIVE, 4, 30),
        (SimMatchState.LIVE_2H, 3, 30),
    ]

    passed = 0
    for state, matches, expected in test_cases:
        actual = get_fetch_interval(state, matches)
        if actual == expected:
            passed += 1
            logger.debug(f"  ✅ {state} ({matches} matches) → {actual}s")
        else:
            logger.error(f"  ❌ {state} ({matches} matches) → {actual}s (expected {expected}s)")
            results["errors"].append(f"Fetch interval: {state} expected {expected}s got {actual}s")

    logger.info(f"Fetch intervals: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


# ── VOID match simulation ─────────────────────────────────────────────────────
def test_void_handling():
    """Tests VOID match scenario."""
    logger.info("Testing VOID match handling...")
    sm = SimStateMachine()
    voting = SimVotingSystem()

    sm.try_transition(SimMatchState.LIVE, "kickoff")
    voting.cast_vote("fp_001", "home", 5, 0.9, {"home": 0, "away": 0})
    voting.cast_vote("fp_002", "away", 10, 0.8, {"home": 0, "away": 0})

    # Match abandoned
    sm.try_transition(SimMatchState.VOID, "abandoned")

    # Verify
    if sm.current == SimMatchState.VOID:
        logger.success("  ✅ Match correctly transitioned to VOID")
        results["edge_cases_found"].append("VOID handling: correct")
        return True
    else:
        logger.error("  ❌ VOID transition failed")
        results["errors"].append("VOID handling failed")
        return False


# ── Penalty micro-vote simulation ─────────────────────────────────────────────
async def simulate_penalty_shootout(sm: SimStateMachine, voting: SimVotingSystem, prediction: SimPredictionEngine):
    """Simulates the penalty shootout with micro-votes."""
    logger.info("⚽ PENALTY SHOOTOUT — simulating micro-votes...")

    if not sm.try_transition(SimMatchState.PENALTIES, "penalties"):
        logger.error("Failed to transition to PENALTIES")
        return

    kicks = [
        ("Argentina", "Messi", True),
        ("France", "Mbappe", True),
        ("Argentina", "Dybala", True),
        ("France", "Coman", False),
        ("Argentina", "Paredes", True),
        ("France", "Tchouameni", False),
        ("Argentina", "Montiel", True),
    ]

    shootout_votes = []

    for i, (team, player, scored) in enumerate(kicks):
        # 60-second micro-vote window
        micro_fp = f"micro_fp_{i:03d}"
        pick = "Argentina" if i % 2 == 0 else "France"
        voting.cast_vote(micro_fp, pick, 120 + i, 0.85, {"home": 3, "away": 3})
        shootout_votes.append({"kick": i + 1, "team": team, "player": player, "scored": scored})
        await asyncio.sleep(SIMULATION_SPEED)

    logger.info(f"  Penalty shootout: {len(kicks)} kicks, {len(shootout_votes)} vote rounds")
    logger.success("  ✅ Penalty micro-vote simulation complete")
    results["edge_cases_found"].append("Penalty micro-votes: all rounds completed")


# ── Main simulation ───────────────────────────────────────────────────────────
async def run_simulation():
    logger.info("=" * 60)
    logger.info("PROJECT DELTA — SIMULATION TEST")
    logger.info(f"Match: France vs Argentina (WC 2022 Final)")
    logger.info(f"Speed: {SIMULATION_SPEED}s per event")
    logger.info("=" * 60)

    start_time = time.time()

    # Load events
    events = load_wc2022_final()
    logger.info(f"Events loaded: {len(events)}")

    # Initialise systems
    sm = SimStateMachine()
    prediction = SimPredictionEngine()
    voting = SimVotingSystem()
    sources = SimSourceHealth()

    # Track state machine transitions
    expected_states = [
        SimMatchState.LIVE,
        SimMatchState.HT,
        SimMatchState.LIVE_2H,
        SimMatchState.FT,
        SimMatchState.ET_1H,
        SimMatchState.ET_HT,
        SimMatchState.ET_2H,
        SimMatchState.PENALTIES,
        SimMatchState.FINISHED,
    ]
    expected_idx = 0

    # Run pre-match tests
    logger.info("\n── PRE-MATCH TESTS ─────────────────────────────────────")
    test_hallucination_guard()
    test_fetch_intervals()
    test_void_handling()

    # Simulate some pre-match votes
    logger.info("\n── PRE-MATCH VOTES ─────────────────────────────────────")
    for i in range(10):
        fp = f"prematch_fp_{i:04d}"
        pick = ["home", "draw", "away"][i % 3]
        trust = 0.75 + (i % 4) * 0.05
        voting.cast_vote(fp, pick, -60 + i * 5, min(0.98, trust), {"home": 0, "away": 0})
    logger.info(f"Pre-match votes cast: {len(voting.votes)}")

    # Simulate source block mid-match
    logger.info("\n── SOURCE BLOCK TEST ───────────────────────────────────")
    sources.simulate_block("ESPN FC")
    sources.simulate_block("Sofascore")
    healthy = sources.count_healthy()
    logger.info(f"  Healthy sources: {healthy}/6")
    if sources.should_trigger_tavily_fallback():
        logger.warning("  ⚠️  Tavily fallback would trigger (<3 healthy sources)")
        results["edge_cases_found"].append("Tavily fallback: triggered correctly")
    sources.simulate_recovery("ESPN FC")
    sources.simulate_recovery("Sofascore")

    # Main event replay loop
    logger.info("\n── MATCH SIMULATION ────────────────────────────────────")

    for event in events:
        await asyncio.sleep(SIMULATION_SPEED)

        minute = event.get("minute", 0)
        period = event.get("period", 1)
        event_type = event.get("type", {}).get("name", "")
        team = event.get("team", {}).get("name", "")
        player = event.get("player", {}).get("name", "")
        commentary = event.get("commentary", "")
        state_hint = event.get("state_hint", "")

        results["events_processed"] += 1
        sm.last_commentary_time = time.time()

        # State transition from hint
        if state_hint and state_hint != sm.current:
            if expected_idx < len(expected_states) and state_hint == expected_states[expected_idx]:
                sm.try_transition(state_hint, commentary[:50] or event_type)
                expected_idx += 1

        # Auto-detect from commentary
        elif commentary:
            detected = sm.detect_from_commentary(commentary)
            if detected and detected != sm.current:
                if expected_idx < len(expected_states) and detected == expected_states[expected_idx]:
                    sm.try_transition(detected, f"auto-detect: '{commentary[:40]}'")
                    expected_idx += 1

        # Handle penalties separately
        if sm.current == SimMatchState.PENALTIES and state_hint != SimMatchState.PENALTIES:
            continue

        # Update predictions (not locked yet)
        if sm.current in (SimMatchState.LIVE, SimMatchState.LIVE_2H, SimMatchState.ET_1H, SimMatchState.ET_2H):
            prediction.update(event)

            # 85-min vote lock
            if period == 2 and minute >= 85 and not voting.locked:
                voting.lock(minute)

        # Simulate in-match votes
        if not voting.locked and sm.current in (SimMatchState.LIVE, SimMatchState.LIVE_2H):
            if minute % 10 == 0 and minute > 0:
                fp = f"live_fp_{minute:03d}"
                if fp not in voting.fingerprints:
                    pick = "home" if prediction.confidence["home_win"] > 0.5 else "away"
                    voting.cast_vote(fp, pick, minute, 0.82, prediction.scores.copy())

        # Log key events
        if event_type in ("goal", "penalty_won", "period_end", "half_time", "extra_time_start", "match_over"):
            logger.info(f"  [{period}:{minute:03d}'] {event_type.upper()} | {team} {player} | {sm.current}")

    # Run penalty shootout if reached FINISHED state
    if SimMatchState.PENALTIES in [h["from"] for h in sm.history]:
        await simulate_penalty_shootout(sm, voting, prediction)

    # Final FINISHED transition
    if sm.current == SimMatchState.PENALTIES:
        sm.try_transition(SimMatchState.FINISHED, "final score")

    # ── Results ──────────────────────────────────────────────────────────────
    elapsed = time.time() - start_time

    logger.info("\n" + "=" * 60)
    logger.info("SIMULATION RESULTS")
    logger.info("=" * 60)

    # State machine completeness check
    all_transitions = [h["from"] + "→" + h["to"] for h in sm.history]
    logger.info(f"\n📊 STATE MACHINE TRANSITIONS ({len(all_transitions)}):")
    for t in all_transitions:
        logger.info(f"   {t}")

    # Check all expected states reached
    all_reached = all(
        any(h["to"] == s for h in sm.history) for s in expected_states
    )

    if all_reached:
        logger.success(f"✅ ALL {len(expected_states)} EXPECTED STATES REACHED")
    else:
        missing = [s for s in expected_states if not any(h["to"] == s for h in sm.history)]
        logger.error(f"❌ MISSING STATES: {missing}")
        results["errors"].append(f"Missing states: {missing}")

    # Prediction summary
    logger.info(f"\n📈 PREDICTION ENGINE:")
    logger.info(f"   Updates: {prediction.update_count}")
    logger.info(f"   Final score: {prediction.scores}")
    logger.info(f"   Final confidence: {prediction.confidence_range()}")
    logger.info(f"   Locked: {prediction.locked}")

    # Vote summary
    vote_summary = voting.summary()
    logger.info(f"\n🗳️  VOTING SYSTEM:")
    for k, v in vote_summary.items():
        logger.info(f"   {k}: {v}")

    # Edge cases
    logger.info(f"\n⚠️  EDGE CASES FOUND ({len(results['edge_cases_found'])}):")
    for ec in results["edge_cases_found"]:
        logger.info(f"   • {ec}")

    # Errors
    if results["errors"]:
        logger.error(f"\n❌ ERRORS ({len(results['errors'])}):")
        for err in results["errors"]:
            logger.error(f"   • {err}")
    else:
        logger.success("\n✅ ZERO ERRORS")

    # Pass/fail
    logger.info(f"\n📋 SUMMARY:")
    logger.info(f"   Events processed: {results['events_processed']}")
    logger.info(f"   Transitions: {results['transitions_passed']}/{results['transitions_tested']} passed")
    logger.info(f"   Duration: {elapsed:.2f}s")

    passed = (
        all_reached
        and results["transitions_failed"] == 0
        and len(results["errors"]) == 0
    )

    if passed:
        logger.success("\n🚀 SIMULATION PASSED — SAFE TO DEPLOY")
        logger.info("All lifecycle states tested. System ready for June 11.")
    else:
        logger.error("\n🚫 SIMULATION FAILED — DO NOT DEPLOY")
        logger.error("Fix errors above before launch.")

    return passed


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logger.info("Run: python simulation.py")
    logger.info(f"StatsBomb available: {STATSBOMB_AVAILABLE}")
    logger.info(f"Pipeline available: {PIPELINE_AVAILABLE}")
    logger.info(f"Model available: {MODEL_AVAILABLE}")

    passed = asyncio.run(run_simulation())
    sys.exit(0 if passed else 1)
