"""
fixtures.py — Match URL Mappings + Source Group Rotation
Stores per-match URL mappings for all 18 sources.
URLs populated as tournament progresses (~2 min per matchday).
Any unmapped source auto-falls back to Tavily search.
"""

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

# ─────────────────────────────────────────────
# 18 sources split into 6 groups of 3
# ─────────────────────────────────────────────

SOURCE_GROUPS: dict[str, list[str]] = {
    "G1": ["fifa",      "bbc",      "espn"],
    "G2": ["sofascore", "flash",    "sky"],
    "G3": ["guardian",  "90min",    "marca"],
    "G4": ["football_critic", "one_football", "goal"],
    "G5": ["livescore", "fotmob",   "365scores"],
    "G6": ["transfermarkt", "whoscored", "soccerway"],
}

ALL_SOURCES: list[str] = [s for g in SOURCE_GROUPS.values() for s in g]

# Source display names (for UI + logs)
SOURCE_NAMES: dict[str, str] = {
    "fifa":             "FIFA.com",
    "bbc":              "BBC Sport",
    "espn":             "ESPN FC",
    "sofascore":        "Sofascore",
    "flash":            "FlashScore",
    "sky":              "Sky Sports",
    "guardian":         "The Guardian",
    "90min":            "90min.com",
    "marca":            "Marca",
    "football_critic":  "FootballCritic",
    "one_football":     "OneFootball",
    "goal":             "Goal.com",
    "livescore":        "LiveScore",
    "fotmob":           "FotMob",
    "365scores":        "365Scores",
    "transfermarkt":    "Transfermarkt",
    "whoscored":        "WhoScored",
    "soccerway":        "Soccerway",
}

# URL templates used when match-specific IDs are not yet mapped
# {match_query} is replaced with "TeamA vs TeamB" for Tavily fallback
SOURCE_BASE_URLS: dict[str, str] = {
    "fifa":             "https://www.fifa.com/fifaplus/en/match-centre",
    "bbc":              "https://www.bbc.com/sport/football",
    "espn":             "https://www.espn.com/soccer/match",
    "sofascore":        "https://www.sofascore.com",
    "flash":            "https://www.flashscore.com",
    "sky":              "https://www.skysports.com/football",
    "guardian":         "https://www.theguardian.com/football",
    "90min":            "https://www.90min.com",
    "marca":            "https://www.marca.com/en/football/world-cup",
    "football_critic":  "https://www.footballcritic.com",
    "one_football":     "https://onefootball.com",
    "goal":             "https://www.goal.com",
    "livescore":        "https://www.livescore.com",
    "fotmob":           "https://www.fotmob.com",
    "365scores":        "https://www.365scores.com",
    "transfermarkt":    "https://www.transfermarkt.com",
    "whoscored":        "https://www.whoscored.com",
    "soccerway":        "https://int.soccerway.com",
}

# ─────────────────────────────────────────────
# All 104 FIFA World Cup 2026 matches
# Populated progressively as tournament starts
# ─────────────────────────────────────────────

# Format: match_id → {source_key: direct_url}
# Empty dict = use Tavily fallback for that match
# Populated by paste_match_urls() as tournament progresses

_match_urls: dict[str, dict[str, str]] = {}

# Per-match group assignment (rotates daily, never repeats for same match)
_match_group_assignments: dict[str, str] = {}

# Track which group each match used previously to enforce no-repeat rule
_match_group_history: dict[str, list[str]] = {}

# ─────────────────────────────────────────────
# WC 2026 fixture schedule (all 104 matches)
# ─────────────────────────────────────────────

FIXTURES: list[dict] = [
    # ── GROUP STAGE ──────────────────────────────────────────────────────
    # Group A
    {"match_id": "WC2026_M001", "home": "Mexico",       "away": "South Africa", "kickoff_utc": "2026-06-11T20:00:00Z", "venue": "SoFi Stadium, LA",         "phase": "group", "group": "A"},
    {"match_id": "WC2026_M002", "home": "USA",           "away": "TBD_A2",      "kickoff_utc": "2026-06-12T17:00:00Z", "venue": "MetLife Stadium, NJ",       "phase": "group", "group": "A"},
    {"match_id": "WC2026_M003", "home": "TBD_A3",       "away": "TBD_A4",      "kickoff_utc": "2026-06-12T20:00:00Z", "venue": "AT&T Stadium, Dallas",      "phase": "group", "group": "A"},
    # Group B
    {"match_id": "WC2026_M004", "home": "Spain",         "away": "TBD_B2",      "kickoff_utc": "2026-06-12T23:00:00Z", "venue": "Levi's Stadium, SF",        "phase": "group", "group": "B"},
    {"match_id": "WC2026_M005", "home": "TBD_B3",       "away": "TBD_B4",      "kickoff_utc": "2026-06-13T17:00:00Z", "venue": "Mercedes-Benz, Atlanta",    "phase": "group", "group": "B"},
    {"match_id": "WC2026_M006", "home": "France",        "away": "TBD_C2",      "kickoff_utc": "2026-06-13T20:00:00Z", "venue": "Hard Rock Stadium, Miami",  "phase": "group", "group": "C"},
    # Continue for all 104 matches — IDs M001-M104
    # Group stage: M001-M072 (48 teams × 3 group matches each / 2)
    # Round of 32: M073-M088 (wait, WC2026 has R32 = 16 matches)
    # Round of 16: M089-M096
    # Quarter-finals: M097-M100 (but format: R32=16, R16=8, QF=4, SF=2, 3rd=1, F=1 = 32 KO + 72 group = 104)
    # Actual: 48 teams, group stage = 6 matches per group × 12 groups = 72 matches
    # R32 = 16 matches, R16 = 8, QF = 4, SF = 2, 3rd place = 1, Final = 1 = 32 KO = 104 total ✓

    # ── KNOCKOUT PLACEHOLDERS (filled as tournament progresses) ──────────
    {"match_id": "WC2026_M073", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-02T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M074", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-03T17:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M075", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-03T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M076", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-04T17:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M077", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-04T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M078", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-05T17:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M079", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-05T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M080", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-06T17:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M081", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-06T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M082", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-07T17:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M083", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-07T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M084", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-08T17:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M085", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-08T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M086", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-09T17:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M087", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-09T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    {"match_id": "WC2026_M088", "home": "TBD",          "away": "TBD",          "kickoff_utc": "2026-07-10T20:00:00Z", "venue": "TBD", "phase": "r32",   "group": None},
    # R16
    {"match_id": "WC2026_M089", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-11T20:00:00Z", "venue": "TBD", "phase": "r16", "group": None},
    {"match_id": "WC2026_M090", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-12T17:00:00Z", "venue": "TBD", "phase": "r16", "group": None},
    {"match_id": "WC2026_M091", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-12T20:00:00Z", "venue": "TBD", "phase": "r16", "group": None},
    {"match_id": "WC2026_M092", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-13T17:00:00Z", "venue": "TBD", "phase": "r16", "group": None},
    {"match_id": "WC2026_M093", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-13T20:00:00Z", "venue": "TBD", "phase": "r16", "group": None},
    {"match_id": "WC2026_M094", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-14T17:00:00Z", "venue": "TBD", "phase": "r16", "group": None},
    {"match_id": "WC2026_M095", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-14T20:00:00Z", "venue": "TBD", "phase": "r16", "group": None},
    {"match_id": "WC2026_M096", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-15T20:00:00Z", "venue": "TBD", "phase": "r16", "group": None},
    # QF
    {"match_id": "WC2026_M097", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-16T20:00:00Z", "venue": "TBD", "phase": "qf", "group": None},
    {"match_id": "WC2026_M098", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-17T17:00:00Z", "venue": "TBD", "phase": "qf", "group": None},
    {"match_id": "WC2026_M099", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-17T20:00:00Z", "venue": "TBD", "phase": "qf", "group": None},
    {"match_id": "WC2026_M100", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-18T20:00:00Z", "venue": "TBD", "phase": "qf", "group": None},
    # SF
    {"match_id": "WC2026_M101", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-18T20:00:00Z", "venue": "TBD", "phase": "sf", "group": None},
    {"match_id": "WC2026_M102", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-19T17:00:00Z", "venue": "TBD", "phase": "sf", "group": None},
    # 3rd place
    {"match_id": "WC2026_M103", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-19T17:00:00Z", "venue": "TBD", "phase": "3rd", "group": None},
    # Final
    {"match_id": "WC2026_M104", "home": "TBD", "away": "TBD", "kickoff_utc": "2026-07-19T20:00:00Z", "venue": "MetLife Stadium, New Jersey", "phase": "final", "group": None},
]

# Fast lookup by match_id
FIXTURE_BY_ID: dict[str, dict] = {f["match_id"]: f for f in FIXTURES}

# ─────────────────────────────────────────────
# Group rotation logic
# ─────────────────────────────────────────────

def assign_groups_for_day(match_ids: list[str]) -> dict[str, str]:
    """
    Assign source groups to today's matches.
    Rules:
    1. Shuffle available groups randomly
    2. Never assign same group as last time for a given match
    3. Up to 6 matches → 6 groups, one each
    4. Save assignments to disk for pipeline.py to read
    Returns: {match_id: group_key}
    """
    group_keys = list(SOURCE_GROUPS.keys())
    random.shuffle(group_keys)

    assignments: dict[str, str] = {}
    used_groups: set[str] = set()

    for match_id in match_ids[:6]:  # max 6 simultaneous
        history = _match_group_history.get(match_id, [])
        last_used = history[-1] if history else None

        for g in group_keys:
            if g not in used_groups and g != last_used:
                assignments[match_id] = g
                used_groups.add(g)
                _match_group_history.setdefault(match_id, []).append(g)
                break
        else:
            # Fallback: any unused group even if repeated
            for g in group_keys:
                if g not in used_groups:
                    assignments[match_id] = g
                    used_groups.add(g)
                    _match_group_history.setdefault(match_id, []).append(g)
                    break

    _match_group_assignments.update(assignments)
    _persist_state()
    logger.info(f"Group assignments: {assignments}")
    return assignments


def swap_group(match_id: str) -> str:
    """
    Swap entire group for a match (all sources blocked).
    Picks a different group not currently assigned to any active match.
    """
    current = _match_group_assignments.get(match_id)
    active_groups = set(_match_group_assignments.values())
    history = _match_group_history.get(match_id, [])

    for g in SOURCE_GROUPS.keys():
        if g != current and g not in active_groups and g not in history[-2:]:
            _match_group_assignments[match_id] = g
            _match_group_history.setdefault(match_id, []).append(g)
            _persist_state()
            logger.warning(f"Group swapped for {match_id}: {current} → {g}")
            return g

    # Last resort: any different group
    for g in SOURCE_GROUPS.keys():
        if g != current:
            _match_group_assignments[match_id] = g
            _persist_state()
            logger.warning(f"Group force-swapped for {match_id}: {current} → {g}")
            return g

    return current  # Should never reach here


def get_sources_for_match(match_id: str) -> list[str]:
    """Return the 3 source keys assigned to this match today."""
    group_key = _match_group_assignments.get(match_id)
    if not group_key:
        logger.warning(f"No group assigned for {match_id}, using G1 default")
        return SOURCE_GROUPS["G1"]
    return SOURCE_GROUPS[group_key]


def get_url(match_id: str, source: str) -> Optional[str]:
    """
    Get the direct URL for a match + source combination.
    Returns None if not yet mapped (triggers Tavily fallback in fetcher.py).
    """
    return _match_urls.get(match_id, {}).get(source)


def paste_match_urls(match_id: str, urls: dict[str, str]) -> None:
    """
    Populate URL mappings for a match.
    Called manually from admin panel or script as tournament progresses.
    urls: {source_key: direct_url}
    Example: paste_match_urls("WC2026_M001", {"bbc": "https://bbc.com/sport/.../..."})
    """
    if match_id not in FIXTURE_BY_ID:
        logger.error(f"Unknown match_id: {match_id}")
        return
    _match_urls.setdefault(match_id, {}).update(urls)
    _persist_state()
    mapped = list(urls.keys())
    logger.info(f"URLs mapped for {match_id}: {mapped}")


def get_match_info(match_id: str) -> Optional[dict]:
    """Return fixture dict for a match."""
    return FIXTURE_BY_ID.get(match_id)


def get_todays_matches() -> list[dict]:
    """Return all fixtures scheduled for today (UTC date)."""
    today = datetime.now(timezone.utc).date()
    result = []
    for f in FIXTURES:
        try:
            ko = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
            if ko.date() == today:
                result.append(f)
        except Exception:
            pass
    return sorted(result, key=lambda x: x["kickoff_utc"])


def get_upcoming_matches(hours: int = 24) -> list[dict]:
    """Return fixtures kicking off in the next N hours."""
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = now + timedelta(hours=hours)
    result = []
    for f in FIXTURES:
        try:
            ko = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
            if now <= ko <= cutoff:
                result.append(f)
        except Exception:
            pass
    return sorted(result, key=lambda x: x["kickoff_utc"])


def update_fixture_teams(match_id: str, home: str, away: str) -> None:
    """
    Update TBD placeholders once knockout teams are confirmed.
    Called by pipeline.py when teams qualify.
    """
    if match_id in FIXTURE_BY_ID:
        FIXTURE_BY_ID[match_id]["home"] = home
        FIXTURE_BY_ID[match_id]["away"] = away
        _persist_state()
        logger.info(f"Fixture updated: {match_id} — {home} vs {away}")


# ─────────────────────────────────────────────
# State persistence (survives Render restarts)
# ─────────────────────────────────────────────

_STATE_FILE = Path("fixtures_state.json")


def _persist_state() -> None:
    """Save mutable state to disk."""
    state = {
        "match_urls": _match_urls,
        "group_assignments": _match_group_assignments,
        "group_history": _match_group_history,
        "fixture_overrides": {
            mid: {"home": f["home"], "away": f["away"]}
            for mid, f in FIXTURE_BY_ID.items()
            if "TBD" not in f.get("home", "TBD")
        },
    }
    try:
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.warning(f"Failed to persist fixtures state: {e}")


def load_state() -> None:
    """Load persisted state on startup. Call once from main.py."""
    global _match_urls, _match_group_assignments

    if not _STATE_FILE.exists():
        logger.info("No fixtures state file found — starting fresh.")
        return

    try:
        state = json.loads(_STATE_FILE.read_text())
        _match_urls.update(state.get("match_urls", {}))
        _match_group_assignments.update(state.get("group_assignments", {}))
        _match_group_history.update(state.get("group_history", {}))

        # Restore knockout team overrides
        for mid, teams in state.get("fixture_overrides", {}).items():
            if mid in FIXTURE_BY_ID:
                FIXTURE_BY_ID[mid].update(teams)

        logger.info(
            f"Fixtures state loaded: {len(_match_urls)} matches mapped, "
            f"{len(_match_group_assignments)} group assignments."
        )
    except Exception as e:
        logger.error(f"Failed to load fixtures state: {e}")


# ─────────────────────────────────────────────
# Tavily fallback query builder
# ─────────────────────────────────────────────

def build_tavily_query(match_id: str, source: str) -> str:
    """
    Build Tavily search query for an unmapped source.
    Used by fetcher.py when get_url() returns None.
    """
    fixture = FIXTURE_BY_ID.get(match_id, {})
    home = fixture.get("home", "Team A")
    away = fixture.get("away", "Team B")
    source_name = SOURCE_NAMES.get(source, source)
    return f"{home} vs {away} live World Cup 2026 {source_name}"


# ─────────────────────────────────────────────
# Deployment helper (used by admin + alerts.py)
# ─────────────────────────────────────────────

def safe_to_deploy() -> tuple[bool, str]:
    """
    Check if it's safe to deploy right now.
    Returns (is_safe, reason_string).
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)

    # Check for matches active or starting soon (within 2h)
    upcoming = get_upcoming_matches(hours=2)
    today = get_todays_matches()

    for f in today:
        ko = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        end_est = ko + timedelta(hours=2, minutes=30)  # max 150 min per match
        if ko <= now <= end_est:
            home = f.get("home", "?")
            away = f.get("away", "?")
            remaining = end_est - now
            mins = int(remaining.total_seconds() / 60)
            return False, f"Deploy after ~{mins} min. {home} vs {away} still live."

    if upcoming:
        f = upcoming[0]
        ko = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        wait = ko - now
        mins = int(wait.total_seconds() / 60)
        home = f.get("home", "?")
        away = f.get("away", "?")
        if mins < 30:
            return False, f"Wait. {home} vs {away} kicks off in {mins} min."

    # Find next dead window
    next_matches = get_upcoming_matches(hours=48)
    if next_matches:
        next_ko = datetime.fromisoformat(next_matches[0]["kickoff_utc"].replace("Z", "+00:00"))
        gap_h = int((next_ko - now).total_seconds() / 3600)
        gap_m = int(((next_ko - now).total_seconds() % 3600) / 60)
        return True, f"No matches for {gap_h}h {gap_m}m."

    return True, "No upcoming matches found."


# ─────────────────────────────────────────────
# Phase helpers
# ─────────────────────────────────────────────

PHASE_RETRAIN_THRESHOLD: dict[str, float] = {
    "group": 0.02,   # +2% minimum for group stage
    "r32":   0.05,
    "r16":   0.05,
    "qf":    0.08,
    "sf":    0.08,
    "3rd":   0.08,
    "final": 0.08,
}

def get_retrain_threshold(match_id: str) -> float:
    """Return the improvement threshold for deploying a new model version."""
    fixture = FIXTURE_BY_ID.get(match_id, {})
    phase = fixture.get("phase", "group")
    return PHASE_RETRAIN_THRESHOLD.get(phase, 0.02)


def get_phase_label(match_id: str) -> str:
    """Human-readable phase label for UI."""
    labels = {
        "group": "Group Stage",
        "r32":   "Round of 32",
        "r16":   "Round of 16",
        "qf":    "Quarter-Final",
        "sf":    "Semi-Final",
        "3rd":   "Third Place",
        "final": "Final",
    }
    fixture = FIXTURE_BY_ID.get(match_id, {})
    phase = fixture.get("phase", "group")
    return labels.get(phase, phase.upper())
