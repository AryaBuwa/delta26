"""
fetcher.py — Layer 1: Data Fetching
====================================
18 sources split into 6 groups of 3.
Each match assigned one group, rotating randomly each morning.
Async parallel fetch with bot prevention, rate limiting, fallback chain.
"""

import asyncio
import random
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

# ─────────────────────────────────────────────
# SOURCE DEFINITIONS
# ─────────────────────────────────────────────

class SourceStatus(Enum):
    OK = "ok"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class Source:
    id: int
    name: str
    url_template: str           # e.g. "https://bbc.com/sport/football/live/{match_slug}"
    group: int                  # 1–6
    status: SourceStatus = SourceStatus.OK
    blocked_until: Optional[datetime] = None
    failed_attempts: int = 0
    last_user_agent_idx: int = 0


# 18 sources in 6 groups of 3
ALL_SOURCES: list[Source] = [
    # GROUP 1 — Primary official
    Source(1,  "FIFA",            "https://www.fifa.com/fifaplus/en/match-centre/{match_id}",           group=1),
    Source(2,  "BBC Sport",       "https://www.bbc.com/sport/football/live/{match_slug}",               group=1),
    Source(3,  "ESPN FC",         "https://www.espn.com/soccer/match/_/gameId/{espn_id}",               group=1),

    # GROUP 2 — Live score specialists
    Source(4,  "Sofascore",       "https://www.sofascore.com/football/match/{sofascore_id}",            group=2),
    Source(5,  "FlashScore",      "https://www.flashscore.com/match/{flashscore_id}/",                  group=2),
    Source(6,  "Sky Sports",      "https://www.skysports.com/football/live/{sky_id}",                   group=2),

    # GROUP 3 — Quality live blogs
    Source(7,  "The Guardian",    "https://www.theguardian.com/football/live/{guardian_slug}",          group=3),
    Source(8,  "90min",           "https://www.90min.com/posts/{ninety_slug}",                          group=3),
    Source(9,  "Marca",           "https://www.marca.com/en/football/world-cup/{marca_id}.html",        group=3),

    # GROUP 4 — Match data sites
    Source(10, "FootballCritic",  "https://www.footballcritic.com/match/{fc_id}",                      group=4),
    Source(11, "WhoScored",       "https://www.whoscored.com/Matches/{ws_id}/Live/",                    group=4),
    Source(12, "LiveScore",       "https://www.livescore.com/en/football/{ls_id}/",                     group=4),

    # GROUP 5 — Backup international
    Source(13, "Goal.com",        "https://www.goal.com/en/match/{goal_id}",                            group=5),
    Source(14, "OneFootball",     "https://onefootball.com/en/match/{of_id}/live",                      group=5),
    Source(15, "Transfermarkt",   "https://www.transfermarkt.com/spielbericht/index/spielbericht/{tm_id}", group=5),

    # GROUP 6 — Additional reliable
    Source(16, "AS",              "https://en.as.com/soccer/world-cup/{as_id}/",                        group=6),
    Source(17, "FotMob",          "https://www.fotmob.com/match/{fotmob_id}",                           group=6),
    Source(18, "Soccerway",       "https://int.soccerway.com/matches/{sw_id}/",                         group=6),
]

# ─────────────────────────────────────────────
# USER AGENT ROTATION (10 real browser UAs)
# ─────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]


def get_next_user_agent(source: Source) -> str:
    """Return a different user agent each cycle per source."""
    idx = (source.last_user_agent_idx + 1) % len(USER_AGENTS)
    source.last_user_agent_idx = idx
    return USER_AGENTS[idx]


# ─────────────────────────────────────────────
# GROUP ROTATION STATE
# Assigned fresh each morning, random but never repeating same group on same match
# ─────────────────────────────────────────────

class GroupRotation:
    """
    Manages daily group assignment for each match.
    Rotates groups randomly each morning.
    Guarantees: same match never uses same group two days running.
    """

    def __init__(self):
        self._assignments: dict[str, int] = {}    # match_id → group_id (today)
        self._previous: dict[str, int] = {}       # match_id → group_id (yesterday)
        self._last_rotation_date: Optional[str] = None

    def _groups_for_match(self, match_id: str) -> list[int]:
        """All 6 group ids, excluding yesterday's group for this match."""
        excluded = self._previous.get(match_id)
        available = [g for g in range(1, 7) if g != excluded]
        return available

    def rotate_if_new_day(self, match_ids: list[str]):
        """Call once per morning. Assigns new random groups."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._last_rotation_date == today:
            return  # Already rotated today

        logger.info(f"[GroupRotation] Rotating groups for {len(match_ids)} matches — {today}")
        self._previous = dict(self._assignments)

        for match_id in match_ids:
            available = self._groups_for_match(match_id)
            self._assignments[match_id] = random.choice(available)

        self._last_rotation_date = today

    def get_group(self, match_id: str) -> int:
        return self._assignments.get(match_id, 1)

    def swap_group(self, match_id: str):
        """Called when entire group is blocked. Swap to different group immediately."""
        current = self._assignments.get(match_id, 1)
        previous = self._previous.get(match_id)
        excluded = {current, previous}
        available = [g for g in range(1, 7) if g not in excluded]
        if available:
            new_group = random.choice(available)
            logger.warning(f"[GroupRotation] Match {match_id}: swapping group {current} → {new_group} (blocked)")
            self._assignments[match_id] = new_group
        else:
            logger.error(f"[GroupRotation] Match {match_id}: no available groups to swap to!")


group_rotation = GroupRotation()


# ─────────────────────────────────────────────
# SOURCE HEALTH TRACKING
# ─────────────────────────────────────────────

class SourceHealthTracker:
    """Tracks blocked/failed sources per match cycle."""

    BLOCK_RETRY_SECONDS = 360       # 6 minutes before retry
    REMOVE_AFTER_ATTEMPTS = 3       # remove for 1 hour after 3 fails
    HOURLY_RESTORE_SECONDS = 3600   # auto-restore after 1 hour

    def __init__(self):
        self._sources: dict[int, Source] = {s.id: s for s in ALL_SOURCES}
        self._removed_until: dict[int, datetime] = {}

    def get_sources_for_group(self, group_id: int) -> list[Source]:
        """Return sources for a group, skipping those currently removed."""
        now = datetime.utcnow()
        result = []
        for s in self._sources.values():
            if s.group != group_id:
                continue
            removed_until = self._removed_until.get(s.id)
            if removed_until and now < removed_until:
                logger.debug(f"[Health] Source {s.name} still removed until {removed_until}")
                continue
            elif removed_until and now >= removed_until:
                # Auto-restore after 1 hour
                logger.info(f"[Health] Auto-restoring source {s.name} after 1-hour removal")
                del self._removed_until[s.id]
                s.status = SourceStatus.OK
                s.failed_attempts = 0
            result.append(s)
        return result

    def mark_blocked(self, source_id: int) -> bool:
        """
        Mark source as blocked (403/429).
        Returns True if source should be removed for 1 hour.
        """
        s = self._sources[source_id]
        s.failed_attempts += 1
        s.status = SourceStatus.BLOCKED
        s.blocked_until = datetime.utcnow() + timedelta(seconds=self.BLOCK_RETRY_SECONDS)

        if s.failed_attempts >= self.REMOVE_AFTER_ATTEMPTS:
            self._removed_until[source_id] = datetime.utcnow() + timedelta(seconds=self.HOURLY_RESTORE_SECONDS)
            logger.warning(f"[Health] Source {s.name} removed for 1 hour after {s.failed_attempts} blocked attempts")
            return True

        logger.warning(f"[Health] Source {s.name} blocked — retry in 6 min (attempt {s.failed_attempts})")
        return False

    def mark_ok(self, source_id: int):
        s = self._sources[source_id]
        s.status = SourceStatus.OK
        s.failed_attempts = 0
        s.blocked_until = None

    def mark_failed(self, source_id: int):
        s = self._sources[source_id]
        s.failed_attempts += 1
        s.status = SourceStatus.FAILED

    def get_health_report(self) -> list[dict]:
        """For admin dashboard — status of all 18 sources."""
        now = datetime.utcnow()
        report = []
        for s in self._sources.values():
            removed_until = self._removed_until.get(s.id)
            report.append({
                "id": s.id,
                "name": s.name,
                "group": s.group,
                "status": s.status.value,
                "failed_attempts": s.failed_attempts,
                "blocked_until": s.blocked_until.isoformat() if s.blocked_until else None,
                "removed_until": removed_until.isoformat() if removed_until and now < removed_until else None,
            })
        return report


source_health = SourceHealthTracker()


# ─────────────────────────────────────────────
# RAW FETCH RESULT
# ─────────────────────────────────────────────

@dataclass
class FetchResult:
    source_name: str
    source_id: int
    success: bool
    raw_html: Optional[str] = None
    raw_text: Optional[str] = None     # commentary extracted from HTML
    error: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None


# ─────────────────────────────────────────────
# SINGLE SOURCE FETCH
# ─────────────────────────────────────────────

async def fetch_single_source(
    session: aiohttp.ClientSession,
    source: Source,
    match_context: dict,
) -> FetchResult:
    """
    Fetch one source for one match.
    match_context has slugs/IDs needed to build the URL.
    """
    start = time.time()
    url = _build_url(source, match_context)
    if not url:
        return FetchResult(source.name, source.id, False, error="No URL mapping for this match")

    ua = get_next_user_agent(source)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }

    # Randomise request interval ±5 seconds (handled by caller, but record intent)
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            latency = (time.time() - start) * 1000

            if resp.status in (403, 429):
                removed = source_health.mark_blocked(source.id)
                return FetchResult(
                    source.name, source.id, False,
                    status_code=resp.status,
                    error=f"Blocked ({resp.status})" + (" — removed 1h" if removed else " — retry 6min"),
                    latency_ms=latency,
                )

            if resp.status != 200:
                source_health.mark_failed(source.id)
                return FetchResult(
                    source.name, source.id, False,
                    status_code=resp.status,
                    error=f"HTTP {resp.status}",
                    latency_ms=latency,
                )

            html = await resp.text(errors="replace")
            text = _extract_commentary(html, source)
            source_health.mark_ok(source.id)

            return FetchResult(
                source.name, source.id, True,
                raw_html=html,
                raw_text=text,
                status_code=200,
                latency_ms=latency,
            )

    except asyncio.TimeoutError:
        source_health.mark_failed(source.id)
        return FetchResult(source.name, source.id, False, error="Timeout (15s)")
    except Exception as e:
        source_health.mark_failed(source.id)
        return FetchResult(source.name, source.id, False, error=str(e))


def _build_url(source: Source, match_context: dict) -> Optional[str]:
    """Build URL from template + match context dict. Returns None if missing key."""
    try:
        return source.url_template.format(**match_context)
    except KeyError as e:
        logger.debug(f"[Fetch] Source {source.name} missing URL key: {e}")
        return None


def _extract_commentary(html: str, source: Source) -> str:
    """
    Extract live commentary text from HTML.
    Each source has slightly different structure — we grab the most text-rich elements.
    Parser.py (Groq) will make sense of raw text — we just need enough signal.
    """
    try:
        soup = BeautifulSoup(html, "lxml")

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "ads"]):
            tag.decompose()

        # Source-specific selectors (best effort, falls back to body text)
        selectors_by_source = {
            "BBC Sport":    ["#live-text-commentary", ".lx-stream__post", ".sp-c-live-region"],
            "ESPN FC":      [".LiveScore", ".match-header__events", ".Commentary"],
            "Sofascore":    [".incident", ".event__item", "[data-testid='incident']"],
            "The Guardian": [".block--content", ".sport-body-text", ".liveblog-body"],
            "Sky Sports":   [".sdc-live-blog__entry", ".match-centre__commentary"],
        }

        selectors = selectors_by_source.get(source.name, [])
        texts = []

        for sel in selectors:
            elements = soup.select(sel)
            if elements:
                for el in elements[:50]:   # cap at 50 elements to keep tokens down
                    t = el.get_text(separator=" ", strip=True)
                    if t:
                        texts.append(t)
                break   # found good selector, stop

        if not texts:
            # Generic fallback: grab all paragraph + list item text
            for tag in soup.find_all(["p", "li", "span", "div"], limit=100):
                t = tag.get_text(separator=" ", strip=True)
                if len(t) > 30:    # skip short noise fragments
                    texts.append(t)

        combined = " | ".join(texts)
        return combined[:8000]   # cap to keep Groq tokens reasonable

    except Exception as e:
        logger.warning(f"[Fetch] Commentary extraction failed for {source.name}: {e}")
        return ""


# ─────────────────────────────────────────────
# PARALLEL GROUP FETCH (main entry point)
# ─────────────────────────────────────────────

@dataclass
class GroupFetchResult:
    match_id: str
    valid_results: list[FetchResult]   # ≥3 success = cross-reference mode
    all_results: list[FetchResult]
    group_used: int
    needs_fallback: bool               # True if <3 valid sources returned


async def fetch_match_group(
    match_id: str,
    match_context: dict,
    session: aiohttp.ClientSession,
) -> GroupFetchResult:
    """
    Fetch all 3 sources in assigned group simultaneously (async parallel).
    Returns result with valid_results count. Caller triggers fallback if <3.
    """
    group_id = group_rotation.get_group(match_id)
    sources = source_health.get_sources_for_group(group_id)

    if not sources:
        logger.error(f"[Fetch] Match {match_id}: No available sources in group {group_id}!")
        group_rotation.swap_group(match_id)
        # Retry with new group
        group_id = group_rotation.get_group(match_id)
        sources = source_health.get_sources_for_group(group_id)

    logger.info(f"[Fetch] Match {match_id}: Fetching group {group_id} — {[s.name for s in sources]}")

    # Randomise ±5s between source requests in the group
    tasks = []
    for i, source in enumerate(sources):
        delay = random.uniform(0, 5)
        tasks.append(_fetch_with_delay(session, source, match_context, delay))

    results: list[FetchResult] = await asyncio.gather(*tasks, return_exceptions=False)
    valid = [r for r in results if r.success and r.raw_text]

    # Check if entire group is blocked
    all_blocked = all(
        r.status_code in (403, 429)
        for r in results if r.status_code is not None
    )
    if all_blocked and len(results) > 0:
        logger.warning(f"[Fetch] Match {match_id}: Entire group {group_id} blocked — swapping group")
        group_rotation.swap_group(match_id)

    return GroupFetchResult(
        match_id=match_id,
        valid_results=valid,
        all_results=results,
        group_used=group_id,
        needs_fallback=len(valid) < 3,
    )


async def _fetch_with_delay(
    session: aiohttp.ClientSession,
    source: Source,
    match_context: dict,
    delay: float,
) -> FetchResult:
    if delay > 0:
        await asyncio.sleep(delay)
    return await fetch_single_source(session, source, match_context)


# ─────────────────────────────────────────────
# MULTI-MATCH PARALLEL FETCH (up to 6 matches)
# ─────────────────────────────────────────────

async def fetch_all_active_matches(
    matches: list[dict],
) -> list[GroupFetchResult]:
    """
    Fetch all active matches simultaneously.
    Each match gets its own source group.
    Returns list of GroupFetchResult — caller handles fallback for those with needs_fallback=True.
    """
    if not matches:
        return []

    logger.info(f"[Fetch] Starting parallel fetch for {len(matches)} matches")

    connector = aiohttp.TCPConnector(
        limit=30,                    # max 30 simultaneous connections
        limit_per_host=5,            # max 5 per domain (polite)
        ttl_dns_cache=300,
        ssl=False,                   # skip SSL verify for speed (we validate data via parser)
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            fetch_match_group(
                match_id=m["match_id"],
                match_context=m,
                session=session,
            )
            for m in matches
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    valid_results = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"[Fetch] Match fetch raised exception: {r}")
        else:
            valid_results.append(r)

    logger.info(f"[Fetch] Completed: {len(valid_results)}/{len(matches)} matches fetched")
    return valid_results


# ─────────────────────────────────────────────
# TAVILY SEARCH FALLBACK
# ─────────────────────────────────────────────

async def tavily_fallback(match_id: str, home_team: str, away_team: str) -> Optional[str]:
    """
    Called when <3 valid sources returned.
    Uses Tavily to search for live match updates.
    """
    import os
    from tavily import TavilyClient

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.error("[Fallback] TAVILY_API_KEY not set")
        return None

    query = f"{home_team} vs {away_team} live score World Cup 2026"
    logger.warning(f"[Fallback] Triggering Tavily search: '{query}'")

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=3,
            include_raw_content=True,
        )
        texts = []
        for result in response.get("results", []):
            content = result.get("raw_content") or result.get("content", "")
            if content:
                texts.append(f"[{result.get('title', 'Unknown')}] {content[:1500]}")

        combined = " | ".join(texts)
        logger.info(f"[Fallback] Tavily returned {len(texts)} results for {match_id}")
        return combined if combined else None

    except Exception as e:
        logger.error(f"[Fallback] Tavily failed for {match_id}: {e}")
        return None


# ─────────────────────────────────────────────
# LINKUP SEARCH FALLBACK (secondary)
# ─────────────────────────────────────────────

async def linkup_fallback(match_id: str, home_team: str, away_team: str) -> Optional[str]:
    """
    Called when Tavily also fails.
    Uses Linkup as secondary search fallback.
    """
    import os
    import httpx

    api_key = os.getenv("LINKUP_API_KEY")
    if not api_key:
        logger.error("[Fallback] LINKUP_API_KEY not set")
        return None

    query = f"{home_team} {away_team} live World Cup score"
    logger.warning(f"[Fallback] Triggering Linkup search: '{query}'")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.linkup.so/v1/search",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"q": query, "depth": "standard", "outputType": "sourcedAnswer"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("answer", "")
            logger.info(f"[Fallback] Linkup returned answer for {match_id}")
            return answer if answer else None

    except Exception as e:
        logger.error(f"[Fallback] Linkup failed for {match_id}: {e}")
        return None


# ─────────────────────────────────────────────
# FETCH INTERVALS (adaptive)
# ─────────────────────────────────────────────

def get_fetch_interval(active_matches: int, match_state: str) -> int:
    """
    Returns fetch interval in seconds based on match count and state.
    """
    if match_state in ("HT", "ET_HT"):
        return 300          # 5 minutes during half time
    if match_state == "PENALTIES":
        return 30           # 30 seconds (kicks every 60-90s)
    if match_state == "SCHEDULED":
        return 780          # 13 minutes pre-match keep-alive
    if active_matches == 1:
        return 15
    if active_matches == 2:
        return 20
    return random.randint(30, 60)   # 3+ matches: 30-60s


# ─────────────────────────────────────────────
# DAILY GROUP ROTATION INIT
# ─────────────────────────────────────────────

def init_daily_rotation(match_ids: list[str]):
    """
    Call once each morning with all match_ids for the day.
    Assigns groups, ensuring no match repeats yesterday's group.
    """
    group_rotation.rotate_if_new_day(match_ids)
    logger.info(f"[GroupRotation] Today's assignments: {group_rotation._assignments}")
