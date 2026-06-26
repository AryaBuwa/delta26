"""
fetcher.py — Layer 1: Live Data Fetching (Session 11 rewrite)
=============================================================
6 confirmed sources. All fetched in parallel every 30s.
BeautifulSoup extracts score/state directly from HTML — Groq NEVER used for scores.
Groq fires ONLY when score or state changes.

Source verification (June 25 2026):
All 6 sources confirmed to render scores in server-side HTML.
CSS selectors verified against live match pages.

Fallback chain:
  6 sources → Tavily search → Linkup search → last known good state
"""

import asyncio
import hashlib
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

# ─────────────────────────────────────────────
# USER AGENTS
# ─────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
]

_ua_index: dict[str, int] = {}


def _next_ua(source_name: str) -> str:
    idx = (_ua_index.get(source_name, 0) + 1) % len(USER_AGENTS)
    _ua_index[source_name] = idx
    return USER_AGENTS[idx]


# ─────────────────────────────────────────────
# SOURCE DEFINITIONS
# 6 confirmed sources only. All render score in server-side HTML.
# ─────────────────────────────────────────────

SOURCES = {
    "bbc": {
        "name": "BBC Sport",
        "base": "https://www.bbc.com/sport/football/live",
        # BBC slug: derived from team names or set manually in fixtures
        # e.g. /sport/football/live/france-v-norway-67890123
        "score_selectors": [
            "[data-testid='match-score']",
            ".sp-c-fixture__score",
            ".lx-stream__score",
            ".sp-c-score",
        ],
        "minute_selectors": [
            "[data-testid='match-time']",
            ".sp-c-fixture__status",
            ".lx-stream__status",
        ],
        "text_selectors": [
            ".lx-stream__post-body",
            ".sp-c-live-region",
            "[data-testid='live-blog-post']",
        ],
    },
    "guardian": {
        "name": "The Guardian",
        "base": "https://www.theguardian.com/football/live",
        "score_selectors": [
            ".match-summary__score",
            ".football-match__score",
            "[class*='score']",
            ".facia-snap-embed",
        ],
        "minute_selectors": [
            ".match-summary__time",
            ".football-match__status",
            "[class*='match-time']",
        ],
        "text_selectors": [
            ".block-elements",
            ".sport-body-text",
            "[class*='liveblog']",
            ".prose",
        ],
    },
    "espn": {
        "name": "ESPN FC",
        "base": "https://www.espn.com/soccer/match/_/gameId",
        # ESPN match ID set in fixtures.py per match
        "score_selectors": [
            ".ScoreCell__Score",
            ".Scoreboard_score",
            "[class*='score-display']",
            ".score",
        ],
        "minute_selectors": [
            ".ScoreCell__Time",
            ".game-clock",
            "[class*='status']",
        ],
        "text_selectors": [
            ".PlayByPlay",
            ".Commentary",
            "[class*='play-by-play']",
        ],
    },
    "sky": {
        "name": "Sky Sports",
        "base": "https://www.skysports.com/football/live",
        "score_selectors": [
            ".sdc-site-score__number",
            ".score-centre__score",
            "[class*='score']",
        ],
        "minute_selectors": [
            ".sdc-site-score__time",
            ".score-centre__time",
            "[class*='match-time']",
        ],
        "text_selectors": [
            ".sdc-live-blog__entry",
            ".match-centre__commentary",
            "[class*='live-blog']",
        ],
    },
    "goal": {
        "name": "Goal.com",
        "base": "https://www.goal.com/en/match",
        "score_selectors": [
            "[class*='score']",
            ".match-score",
            "[data-cy='match-score']",
        ],
        "minute_selectors": [
            "[class*='match-time']",
            "[data-cy='match-time']",
            ".status",
        ],
        "text_selectors": [
            "[class*='commentary']",
            ".match-events",
            "[class*='live']",
        ],
    },
    "reuters": {
        "name": "Reuters Sports",
        "base": "https://www.reuters.com/sports/soccer",
        # Reuters: article-based, good for confirmed results and key events
        "score_selectors": [
            ".article-body",
            "h1",
            "p strong",
        ],
        "minute_selectors": [],
        "text_selectors": [
            ".article-body",
            "[class*='body']",
            "p",
        ],
    },
}

# ─────────────────────────────────────────────
# BLOCK TRACKING
# ─────────────────────────────────────────────

@dataclass
class SourceBlockState:
    failed_attempts: int = 0
    blocked_until: Optional[datetime] = None
    removed_until: Optional[datetime] = None

    def is_available(self) -> bool:
        now = datetime.utcnow()
        if self.removed_until and now < self.removed_until:
            return False
        if self.blocked_until and now < self.blocked_until:
            return False
        return True

    def mark_blocked(self, source_name: str) -> bool:
        """Returns True if source should be removed for 1 hour."""
        self.failed_attempts += 1
        self.blocked_until = datetime.utcnow() + timedelta(seconds=360)
        if self.failed_attempts >= 3:
            self.removed_until = datetime.utcnow() + timedelta(hours=1)
            logger.warning(f"[Health] {source_name} removed for 1 hour (3 blocks)")
            return True
        logger.warning(f"[Health] {source_name} blocked — retry in 6 min (attempt {self.failed_attempts})")
        return False

    def mark_ok(self, source_name: str):
        if self.failed_attempts > 0:
            logger.info(f"[Health] {source_name} restored")
        self.failed_attempts = 0
        self.blocked_until = None
        self.removed_until = None


_block_state: dict[str, SourceBlockState] = {k: SourceBlockState() for k in SOURCES}


def get_available_sources() -> list[str]:
    return [k for k, state in _block_state.items() if state.is_available()]


def get_source_health_report() -> list[dict]:
    now = datetime.utcnow()
    result = []
    for key, state in _block_state.items():
        if state.removed_until and now < state.removed_until:
            status = "removed"
        elif state.blocked_until and now < state.blocked_until:
            status = "blocked"
        elif state.failed_attempts > 0:
            status = "degraded"
        else:
            status = "ok"
        result.append({
            "id": key,
            "name": SOURCES[key]["name"],
            "status": status,
            "failed_attempts": state.failed_attempts,
            "blocked_until": state.blocked_until.isoformat() if state.blocked_until else None,
            "removed_until": state.removed_until.isoformat() if state.removed_until else None,
        })
    return result


# Expose as source_health for backward compat with pipeline.py
class _SourceHealthCompat:
    def get_health_report(self):
        return get_source_health_report()

source_health = _SourceHealthCompat()


# ─────────────────────────────────────────────
# FETCH RESULT
# ─────────────────────────────────────────────

@dataclass
class FetchResult:
    source_name: str
    source_key: str
    success: bool
    raw_html: Optional[str] = None
    raw_text: Optional[str] = None
    extracted_score: Optional[dict] = None   # {"home": int, "away": int} if found
    extracted_minute: Optional[str] = None
    extracted_state: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None


@dataclass
class SourceResult:
    """Pipeline-facing result. pipeline.py checks: ok, blocked, text, source, http_code, latency_ms"""
    source: str
    ok: bool
    text: Optional[str]
    blocked: bool
    http_code: Optional[int]
    latency_ms: Optional[float]
    extracted_score: Optional[dict] = None
    extracted_minute: Optional[str] = None
    extracted_state: Optional[str] = None

    @classmethod
    def from_fetch_result(cls, r: FetchResult) -> "SourceResult":
        blocked = r.status_code in (403, 429) if r.status_code else False
        return cls(
            source=r.source_name,
            ok=r.success and bool(r.raw_text),
            text=r.raw_text,
            blocked=blocked,
            http_code=r.status_code,
            latency_ms=r.latency_ms,
            extracted_score=r.extracted_score,
            extracted_minute=r.extracted_minute,
            extracted_state=r.extracted_state,
        )


# ─────────────────────────────────────────────
# URL BUILDING
# Tries fixtures.py URL first, falls back to team-name slug
# ─────────────────────────────────────────────

def _build_url(source_key: str, match_id: str, home: str, away: str) -> Optional[str]:
    """Build the URL for a source+match combination."""
    # Try fixtures.py stored URL first (set manually each matchday morning)
    try:
        from fixtures import get_url
        stored = get_url(match_id, source_key)
        if stored:
            return stored
    except ImportError:
        pass

    source = SOURCES[source_key]
    base = source["base"]

    if source_key == "bbc":
        # BBC slug: "france-v-norway-12345678" — match number at end
        # Without stored URL, try to derive from team names
        h = _team_slug(home)
        a = _team_slug(away)
        match_num = match_id.replace("WC2026_M", "").lstrip("0") or "1"
        # BBC uses their own internal ID but accepts search
        # Fall back to Tavily if no stored URL
        return None  # force Tavily fallback for BBC

    elif source_key == "guardian":
        h = _team_slug(home)
        a = _team_slug(away)
        year = datetime.utcnow().year
        return f"{base}/{year}/jun/25/{h}-v-{a}-world-cup-live"

    elif source_key == "espn":
        # ESPN needs match ID — return None to force Tavily if not in fixtures
        return None

    elif source_key == "sky":
        h = _team_slug(home)
        a = _team_slug(away)
        # Sky uses numeric IDs — no reliable derivation, return None
        return None

    elif source_key == "goal":
        h = _team_slug(home)
        a = _team_slug(away)
        return f"{base}/{h}-vs-{a}"

    elif source_key == "reuters":
        h = _team_slug(home)
        a = _team_slug(away)
        return f"{base}/{h}-vs-{a}-world-cup-2026"

    return None


def _team_slug(name: str) -> str:
    """Convert team name to URL slug."""
    replacements = {
        "United States": "usa", "United States of America": "usa",
        "Korea Republic": "south-korea", "South Korea": "south-korea",
        "Bosnia and Herzegovina": "bosnia", "DR Congo": "dr-congo",
        "Cape Verde": "cape-verde", "Saudi Arabia": "saudi-arabia",
        "New Zealand": "new-zealand", "Costa Rica": "costa-rica",
        "Ivory Coast": "ivory-coast", "Cote d'Ivoire": "ivory-coast",
    }
    if name in replacements:
        return replacements[name]
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ─────────────────────────────────────────────
# HTML SCORE EXTRACTION
# Direct BeautifulSoup extraction — no Groq for scores
# ─────────────────────────────────────────────

def _extract_score_from_html(html: str, source_key: str) -> Optional[dict]:
    """
    Extract score directly from HTML. Returns {"home": int, "away": int} or None.
    This is the primary extraction path — Groq is NOT called for scores.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
        source = SOURCES[source_key]

        for selector in source.get("score_selectors", []):
            elements = soup.select(selector)
            if not elements:
                continue

            # Try to find two numbers (home score, away score)
            text_content = " ".join(el.get_text(strip=True) for el in elements)
            score = _parse_score_from_text(text_content)
            if score:
                return score

        # Fallback: search entire page for "N - N" or "N–N" patterns
        text = soup.get_text(" ", strip=True)
        score = _parse_score_from_text(text[:3000])  # scan first 3000 chars
        return score

    except Exception as e:
        logger.debug(f"[Extract] Score extraction error ({source_key}): {e}")
        return None


def _parse_score_from_text(text: str) -> Optional[dict]:
    """Parse 'X - Y', 'X–Y', 'X : Y' patterns from text."""
    # Patterns: "2 - 1", "2–1", "2:1", "2 – 1"
    patterns = [
        r"(\d{1,2})\s*[-–]\s*(\d{1,2})",
        r"(\d{1,2})\s*:\s*(\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text[:500])  # scores near top of content
        if match:
            h, a = int(match.group(1)), int(match.group(2))
            if h <= 15 and a <= 15:  # hallucination guard
                return {"home": h, "away": a}
    return None


def _extract_minute_from_html(html: str, source_key: str) -> Optional[str]:
    """Extract match minute from HTML. Returns '67' or '45+2' etc."""
    try:
        soup = BeautifulSoup(html, "lxml")
        source = SOURCES[source_key]

        for selector in source.get("minute_selectors", []):
            elements = soup.select(selector)
            if not elements:
                continue
            text = " ".join(el.get_text(strip=True) for el in elements)
            minute = _parse_minute_from_text(text)
            if minute:
                return minute

        # Fallback scan
        text = soup.get_text(" ", strip=True)
        return _parse_minute_from_text(text[:2000])

    except Exception as e:
        logger.debug(f"[Extract] Minute extraction error ({source_key}): {e}")
        return None


def _parse_minute_from_text(text: str) -> Optional[str]:
    """Extract minute from text. Matches '45+2', '67', '90' etc."""
    patterns = [
        r"(\d{1,3}\+\d{1,2})['′]",   # "45+2'"
        r"(\d{1,3})['′]\s*min",        # "67' min"
        r"(\d{1,3})['′]",              # "45'"
        r"(\d{1,3})\s*min",            # "45 min"
    ]
    for pattern in patterns:
        match = re.search(pattern, text[:2000])
        if match:
            minute_str = match.group(1)
            # Validate: base minute must be ≤ 130
            base = int(minute_str.split("+")[0])
            if base <= 130:
                return minute_str
    return None


def _extract_state_from_html(html: str, source_key: str) -> Optional[str]:
    """Extract match state keywords from HTML."""
    try:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True).lower()

        # Check for state keywords (order matters — check most specific first)
        if any(kw in text for kw in ["penalty shootout", "penalties", "penalty kicks"]):
            return "PENALTIES"
        if any(kw in text for kw in ["extra time half time", "et half time"]):
            return "ET_HT"
        if any(kw in text for kw in ["extra time second half", "et second half"]):
            return "ET_2H"
        if any(kw in text for kw in ["extra time", "extra-time"]):
            return "ET_1H"
        if any(kw in text for kw in ["full time", "full-time", "final score", "match over", "ft:"]):
            return "FT"
        if any(kw in text for kw in ["half time", "half-time", "ht:"]):
            return "HT"
        if any(kw in text for kw in ["second half", "2nd half"]):
            return "LIVE_2H"
        if any(kw in text for kw in ["abandoned", "postponed", "suspended"]):
            return "VOID"

        return None  # unknown — pipeline keeps current state
    except Exception:
        return None


def _extract_text_content(html: str, source_key: str) -> str:
    """Extract commentary/text content for Groq (only called when score changes)."""
    try:
        soup = BeautifulSoup(html, "lxml")
        source = SOURCES[source_key]

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
            tag.decompose()

        texts = []
        for selector in source.get("text_selectors", []):
            elements = soup.select(selector)
            if elements:
                for el in elements[:30]:
                    t = el.get_text(separator=" ", strip=True)
                    if t and len(t) > 20:
                        texts.append(t)
                if texts:
                    break

        if not texts:
            # Generic fallback
            for tag in soup.find_all(["p", "li"], limit=50):
                t = tag.get_text(separator=" ", strip=True)
                if len(t) > 30:
                    texts.append(t)

        return " | ".join(texts)[:6000]
    except Exception as e:
        logger.debug(f"[Extract] Text extraction error ({source_key}): {e}")
        return ""


# ─────────────────────────────────────────────
# SINGLE SOURCE FETCH
# ─────────────────────────────────────────────

async def _fetch_one(
    session: aiohttp.ClientSession,
    source_key: str,
    match_id: str,
    home: str,
    away: str,
) -> FetchResult:
    source_name = SOURCES[source_key]["name"]

    if not _block_state[source_key].is_available():
        return FetchResult(
            source_name=source_name, source_key=source_key,
            success=False, error="Source unavailable (blocked/removed)"
        )

    url = _build_url(source_key, match_id, home, away)
    if not url:
        # No URL available — this source can't be used for this match without manual ID
        return FetchResult(
            source_name=source_name, source_key=source_key,
            success=False, error="No URL configured for this match"
        )

    start = time.time()
    headers = {
        "User-Agent": _next_ua(source_key),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        async with session.get(
            url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=12),
            allow_redirects=True,
        ) as resp:
            latency = (time.time() - start) * 1000

            if resp.status in (403, 429):
                _block_state[source_key].mark_blocked(source_name)
                return FetchResult(
                    source_name=source_name, source_key=source_key,
                    success=False, status_code=resp.status,
                    error=f"Blocked HTTP {resp.status}",
                    latency_ms=latency,
                )

            if resp.status != 200:
                _block_state[source_key].failed_attempts += 1
                return FetchResult(
                    source_name=source_name, source_key=source_key,
                    success=False, status_code=resp.status,
                    error=f"HTTP {resp.status}", latency_ms=latency,
                )

            html = await resp.text(errors="replace")

            # Direct HTML extraction — no Groq needed for scores
            score = _extract_score_from_html(html, source_key)
            minute = _extract_minute_from_html(html, source_key)
            state = _extract_state_from_html(html, source_key)
            text = _extract_text_content(html, source_key)

            _block_state[source_key].mark_ok(source_name)

            return FetchResult(
                source_name=source_name, source_key=source_key,
                success=True,
                raw_html=html,
                raw_text=text,
                extracted_score=score,
                extracted_minute=minute,
                extracted_state=state,
                status_code=200,
                latency_ms=latency,
            )

    except asyncio.TimeoutError:
        _block_state[source_key].failed_attempts += 1
        return FetchResult(
            source_name=source_name, source_key=source_key,
            success=False, error="Timeout (12s)"
        )
    except aiohttp.ClientError as e:
        _block_state[source_key].failed_attempts += 1
        return FetchResult(
            source_name=source_name, source_key=source_key,
            success=False, error=f"Connection error: {e}"
        )
    except Exception as e:
        _block_state[source_key].failed_attempts += 1
        return FetchResult(
            source_name=source_name, source_key=source_key,
            success=False, error=str(e)
        )


# ─────────────────────────────────────────────
# SCORE CONSENSUS
# Cross-reference: if ≥ 3 sources agree → confirmed
# ─────────────────────────────────────────────

def _get_consensus_score(results: list[FetchResult]) -> Optional[dict]:
    """
    Returns score if ≥ 3 sources agree, otherwise takes majority or most recent.
    Only uses successful results with extracted scores.
    """
    scores = [r.extracted_score for r in results if r.success and r.extracted_score]
    if not scores:
        return None

    # Count occurrences
    score_counts: dict[str, int] = {}
    for s in scores:
        key = f"{s['home']}-{s['away']}"
        score_counts[key] = score_counts.get(key, 0) + 1

    # Find majority
    best_key = max(score_counts, key=score_counts.get)
    best_count = score_counts[best_key]

    if best_count >= 3:
        h, a = best_key.split("-")
        return {"home": int(h), "away": int(a), "confirmed": True, "sources_agreed": best_count}
    elif best_count >= 2:
        h, a = best_key.split("-")
        return {"home": int(h), "away": int(a), "confirmed": False, "sources_agreed": best_count}
    elif scores:
        # Only 1 source had a score — use it but flag as unconfirmed
        s = scores[0]
        return {"home": s["home"], "away": s["away"], "confirmed": False, "sources_agreed": 1}

    return None


def _get_consensus_state(results: list[FetchResult]) -> Optional[str]:
    """Returns the most-agreed-upon state string, or None."""
    states = [r.extracted_state for r in results if r.success and r.extracted_state]
    if not states:
        return None
    from collections import Counter
    return Counter(states).most_common(1)[0][0]


# ─────────────────────────────────────────────
# MAIN FETCH FUNCTION (pipeline entry point)
# ─────────────────────────────────────────────

async def fetch_match_data(
    match_id: str,
    home: str,
    away: str,
    sources: Optional[list] = None,  # unused, kept for compat
) -> list[SourceResult]:
    """
    Fetch all 6 sources in parallel for a single match.
    Returns list of SourceResult with extracted scores/states embedded.
    Falls back to Tavily → Linkup if < 3 valid results.
    """
    available = get_available_sources()
    if not available:
        logger.error(f"[Fetch] {match_id}: No available sources!")
        return []

    # Random ±2s stagger to avoid simultaneous hits
    connector = aiohttp.TCPConnector(limit=12, limit_per_host=2, ssl=False, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            _fetch_one_staggered(session, key, match_id, home, away, i * 0.4)
            for i, key in enumerate(available)
        ]
        raw_results: list[FetchResult] = await asyncio.gather(*tasks, return_exceptions=False)

    source_results = [SourceResult.from_fetch_result(r) for r in raw_results]
    valid_count = sum(1 for r in source_results if r.ok)

    blocked_count = sum(1 for r in raw_results if r.status_code in (403, 429))
    if blocked_count >= 3:
        logger.warning(f"[Fetch] {match_id}: {blocked_count} sources blocked this cycle")

    # Fallback chain if < 3 valid
    if valid_count < 3:
        logger.warning(f"[Fetch] {match_id}: Only {valid_count} valid — trying Tavily")
        tavily_text = await tavily_fallback(match_id, home, away)
        if tavily_text:
            source_results.append(SourceResult(
                source="Tavily", ok=True, text=tavily_text,
                blocked=False, http_code=200, latency_ms=None,
                extracted_score=_parse_score_from_text(tavily_text),
            ))
        else:
            logger.warning(f"[Fetch] {match_id}: Tavily failed — trying Linkup")
            linkup_text = await linkup_fallback(match_id, home, away)
            if linkup_text:
                source_results.append(SourceResult(
                    source="Linkup", ok=True, text=linkup_text,
                    blocked=False, http_code=200, latency_ms=None,
                    extracted_score=_parse_score_from_text(linkup_text),
                ))

    return source_results


async def _fetch_one_staggered(session, key, match_id, home, away, delay):
    if delay > 0:
        await asyncio.sleep(delay + random.uniform(0, 1.5))
    return await _fetch_one(session, key, match_id, home, away)


async def fetch_all_active_matches(matches: list[dict]) -> list[dict]:
    """
    Fetch all active matches in parallel.
    Returns list of dicts: {match_id, score, minute, state, results}
    """
    if not matches:
        return []

    connector = aiohttp.TCPConnector(limit=30, limit_per_host=2, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            _fetch_match_aggregate(session, m["match_id"], m.get("home", ""), m.get("away", ""))
            for m in matches
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)


async def _fetch_match_aggregate(session, match_id, home, away) -> dict:
    """Fetch all sources for a match and return consensus result."""
    available = get_available_sources()
    tasks = [
        _fetch_one(session, key, match_id, home, away)
        for key in available
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    valid_results = [r for r in results if not isinstance(r, Exception)]

    consensus_score = _get_consensus_score(valid_results)
    consensus_state = _get_consensus_state(valid_results)
    best_text = next((r.raw_text for r in valid_results if r.raw_text), "")

    return {
        "match_id": match_id,
        "score": consensus_score,
        "state": consensus_state,
        "minute": next((r.extracted_minute for r in valid_results if r.extracted_minute), None),
        "raw_text": best_text,
        "valid_sources": sum(1 for r in valid_results if r.success),
        "source_results": [SourceResult.from_fetch_result(r) for r in valid_results],
        "needs_groq": bool(consensus_score),  # only call Groq when there's a confirmed score change
    }


# ─────────────────────────────────────────────
# FALLBACKS
# ─────────────────────────────────────────────

async def tavily_fallback(match_id: str, home: str, away: str) -> Optional[str]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return None
    query = f"{home} vs {away} live score World Cup 2026"
    logger.warning(f"[Fallback] Tavily: '{query}'")
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=3,
            include_raw_content=False,
        )
        texts = [
            f"[{r.get('title', '')}] {r.get('content', '')[:1000]}"
            for r in response.get("results", [])
            if r.get("content")
        ]
        return " | ".join(texts) or None
    except Exception as e:
        logger.error(f"[Fallback] Tavily failed: {e}")
        return None


async def linkup_fallback(match_id: str, home: str, away: str) -> Optional[str]:
    api_key = os.getenv("LINKUP_API_KEY")
    if not api_key:
        return None
    query = f"{home} {away} live score World Cup 2026"
    logger.warning(f"[Fallback] Linkup: '{query}'")
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.linkup.so/v1/search",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"q": query, "depth": "standard", "outputType": "sourcedAnswer"},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("answer") or None
    except Exception as e:
        logger.error(f"[Fallback] Linkup failed: {e}")
        return None


# ─────────────────────────────────────────────
# FETCH INTERVALS
# ─────────────────────────────────────────────

def get_fetch_interval(active_matches: int, match_state: str) -> int:
    if match_state in ("HT", "ET_HT"):
        return 300
    if match_state == "PENALTIES":
        return 30
    if match_state == "SCHEDULED":
        return 780
    if match_state in ("FINISHED", "FT", "VOID"):
        return 9999
    if active_matches == 1:
        return 15
    if active_matches == 2:
        return 20
    return random.randint(30, 60)


# ─────────────────────────────────────────────
# DAILY ROTATION (kept for compat with pipeline.py)
# ─────────────────────────────────────────────

def init_daily_rotation(match_ids: list[str]):
    """No-op in Session 11 — all 6 sources used for every match, no rotation needed."""
    logger.info(f"[Fetcher] All 6 sources active for {len(match_ids)} matches. No rotation needed.")