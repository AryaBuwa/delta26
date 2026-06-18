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
    url_template: str
    group: int
    status: SourceStatus = SourceStatus.OK
    blocked_until: Optional[datetime] = None
    failed_attempts: int = 0
    last_user_agent_idx: int = 0


# 18 confirmed sources in 6 groups of 3
ALL_SOURCES: list[Source] = [
    # GROUP 1 — Primary official
    Source(1,  "FIFA",             "https://www.fifa.com/fifaplus/en/match-centre/{match_id}",          group=1),
    Source(2,  "BBC Sport",        "https://www.bbc.com/sport/football/live/{match_slug}",              group=1),
    Source(3,  "ESPN FC",          "https://www.espn.com/soccer/match/_/gameId/{espn_id}",              group=1),

    # GROUP 2 — Live score specialists
    Source(4,  "Sofascore",        "https://www.sofascore.com/football/match/{sofascore_id}",           group=2),
    Source(5,  "FlashScore",       "https://www.flashscore.com/match/{flashscore_id}/",                 group=2),
    Source(6,  "FotMob",           "https://www.fotmob.com/match/{fotmob_id}",                          group=2),

    # GROUP 3 — Wire / agency
    Source(7,  "Reuters Sports",   "https://www.reuters.com/sports/soccer/{reuters_slug}/",             group=3),
    Source(8,  "AP News Sports",   "https://apnews.com/sports/soccer/{ap_slug}",                       group=3),
    Source(9,  "CBS Sports",       "https://www.cbssports.com/soccer/gametracker/live/{cbs_id}/",       group=3),

    # GROUP 4 — Live blogs
    Source(10, "The Guardian",     "https://www.theguardian.com/football/live/{guardian_slug}",         group=4),
    Source(11, "Sky Sports",       "https://www.skysports.com/football/live/{sky_id}",                  group=4),
    Source(12, "Goal.com",         "https://www.goal.com/en/match/{goal_id}",                           group=4),

    # GROUP 5 — Stats / data
    Source(13, "WhoScored",        "https://www.whoscored.com/Matches/{ws_id}/Live/",                   group=5),
    Source(14, "LiveScore",        "https://www.livescore.com/en/football/{ls_id}/",                    group=5),
    Source(15, "AS English",       "https://en.as.com/soccer/world-cup/{as_id}/",                       group=5),

    # GROUP 6 — South Asian / international
    Source(16, "Sportstar",        "https://sportstar.thehindu.com/football/{sportstar_slug}/",         group=6),
    Source(17, "Indian Express",   "https://indianexpress.com/sports/football/{ie_slug}/",              group=6),
    Source(18, "NDTV Sports",      "https://sports.ndtv.com/football/{ndtv_slug}",                     group=6),
]

# ─────────────────────────────────────────────
# USER AGENT ROTATION
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
    idx = (source.last_user_agent_idx + 1) % len(USER_AGENTS)
    source.last_user_agent_idx = idx
    return USER_AGENTS[idx]


# ─────────────────────────────────────────────
# GROUP ROTATION STATE
# ─────────────────────────────────────────────

class GroupRotation:
    def __init__(self):
        self._assignments: dict[str, int] = {}
        self._previous: dict[str, int] = {}
        self._last_rotation_date: Optional[str] = None

    def _groups_for_match(self, match_id: str) -> list[int]:
        excluded = self._previous.get(match_id)
        return [g for g in range(1, 7) if g != excluded]

    def rotate_if_new_day(self, match_ids: list[str]):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._last_rotation_date == today:
            return
        logger.info(f"[GroupRotation] Rotating groups for {len(match_ids)} matches — {today}")
        self._previous = dict(self._assignments)
        for match_id in match_ids:
            available = self._groups_for_match(match_id)
            self._assignments[match_id] = random.choice(available)
        self._last_rotation_date = today

    def get_group(self, match_id: str) -> int:
        return self._assignments.get(match_id, 1)

    def swap_group(self, match_id: str):
        current = self._assignments.get(match_id, 1)
        previous = self._previous.get(match_id)
        excluded = {current, previous}
        available = [g for g in range(1, 7) if g not in excluded]
        if available:
            new_group = random.choice(available)
            logger.warning(f"[GroupRotation] Match {match_id}: swapping group {current} → {new_group}")
            self._assignments[match_id] = new_group
        else:
            logger.error(f"[GroupRotation] Match {match_id}: no available groups to swap to!")


group_rotation = GroupRotation()


# ─────────────────────────────────────────────
# SOURCE HEALTH TRACKING
# ─────────────────────────────────────────────

class SourceHealthTracker:
    BLOCK_RETRY_SECONDS = 360
    REMOVE_AFTER_ATTEMPTS = 3
    HOURLY_RESTORE_SECONDS = 3600

    def __init__(self):
        self._sources: dict[int, Source] = {s.id: s for s in ALL_SOURCES}
        self._removed_until: dict[int, datetime] = {}

    def get_sources_for_group(self, group_id: int) -> list[Source]:
        now = datetime.utcnow()
        result = []
        for s in self._sources.values():
            if s.group != group_id:
                continue
            removed_until = self._removed_until.get(s.id)
            if removed_until and now < removed_until:
                continue
            elif removed_until and now >= removed_until:
                logger.info(f"[Health] Auto-restoring source {s.name}")
                del self._removed_until[s.id]
                s.status = SourceStatus.OK
                s.failed_attempts = 0
            result.append(s)
        return result

    def mark_blocked(self, source_id: int) -> bool:
        s = self._sources[source_id]
        s.failed_attempts += 1
        s.status = SourceStatus.BLOCKED
        s.blocked_until = datetime.utcnow() + timedelta(seconds=self.BLOCK_RETRY_SECONDS)
        if s.failed_attempts >= self.REMOVE_AFTER_ATTEMPTS:
            self._removed_until[source_id] = datetime.utcnow() + timedelta(seconds=self.HOURLY_RESTORE_SECONDS)
            logger.warning(f"[Health] Source {s.name} removed for 1 hour")
            return True
        logger.warning(f"[Health] Source {s.name} blocked — retry in 6 min")
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
        now = datetime.utcnow()
        return [{
            "id": s.id,
            "name": s.name,
            "group": s.group,
            "status": s.status.value,
            "failed_attempts": s.failed_attempts,
            "blocked_until": s.blocked_until.isoformat() if s.blocked_until else None,
            "removed_until": self._removed_until[s.id].isoformat()
                if s.id in self._removed_until and now < self._removed_until[s.id] else None,
        } for s in self._sources.values()]


source_health = SourceHealthTracker()


# ─────────────────────────────────────────────
# FETCH RESULT
# ─────────────────────────────────────────────

@dataclass
class FetchResult:
    source_name: str
    source_id: int
    success: bool
    raw_html: Optional[str] = None
    raw_text: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None


# ─────────────────────────────────────────────
# SourceResult — pipeline.py interface
# ─────────────────────────────────────────────
# pipeline.py checks: r.ok, r.blocked, r.text, r.source, r.http_code, r.latency_ms

@dataclass
class SourceResult:
    """
    Pipeline-facing result shape. Wraps FetchResult with friendlier field names.
    pipeline.py checks: r.ok, r.blocked, r.text, r.source, r.http_code, r.latency_ms
    """
    source: str
    ok: bool
    text: Optional[str]
    blocked: bool
    http_code: Optional[int]
    latency_ms: Optional[float]

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
        )


# ─────────────────────────────────────────────
# fetch_match_data — pipeline.py entry point
# ─────────────────────────────────────────────

async def fetch_match_data(
    match_id: str,
    home: str,
    away: str,
    sources: Optional[list] = None,
) -> list[SourceResult]:
    """
    Pipeline-facing fetch entry point.
    Runs fetch_match_group for this match and returns SourceResult list.
    Handles Tavily and Linkup fallbacks automatically if < 3 valid sources.
    """
    match_context = {
        "match_id": match_id,
        "home": home,
        "away": away,
    }

    connector = aiohttp.TCPConnector(limit=10, limit_per_host=3, ttl_dns_cache=300, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        group_result: GroupFetchResult = await fetch_match_group(
            match_id=match_id,
            match_context=match_context,
            session=session,
        )

    # Convert FetchResult → SourceResult
    source_results = [
        SourceResult.from_fetch_result(r)
        for r in group_result.all_results
    ]

    # Tavily fallback if < 3 valid sources
    if group_result.needs_fallback:
        logger.warning(
            f"[Fetch] {match_id}: Only {len(group_result.valid_results)} valid source(s) — trying Tavily"
        )
        tavily_text = await tavily_fallback(match_id, home, away)
        if tavily_text:
            source_results.append(SourceResult(
                source="Tavily",
                ok=True,
                text=tavily_text,
                blocked=False,
                http_code=200,
                latency_ms=None,
            ))
        else:
            linkup_text = await linkup_fallback(match_id, home, away)
            if linkup_text:
                source_results.append(SourceResult(
                    source="Linkup",
                    ok=True,
                    text=linkup_text,
                    blocked=False,
                    http_code=200,
                    latency_ms=None,
                ))

    return source_results


# ─────────────────────────────────────────────
# SINGLE SOURCE FETCH
# ─────────────────────────────────────────────

async def fetch_single_source(
    session: aiohttp.ClientSession,
    source: Source,
    match_context: dict,
) -> FetchResult:
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
                return FetchResult(source.name, source.id, False, status_code=resp.status,
                                   error=f"HTTP {resp.status}", latency_ms=latency)

            html = await resp.text(errors="replace")
            text = _extract_commentary(html, source)
            source_health.mark_ok(source.id)
            return FetchResult(source.name, source.id, True, raw_html=html, raw_text=text,
                               status_code=200, latency_ms=latency)

    except asyncio.TimeoutError:
        source_health.mark_failed(source.id)
        return FetchResult(source.name, source.id, False, error="Timeout (15s)")
    except Exception as e:
        source_health.mark_failed(source.id)
        return FetchResult(source.name, source.id, False, error=str(e))


def _build_url(source: Source, match_context: dict) -> Optional[str]:
    try:
        return source.url_template.format(**match_context)
    except KeyError:
        return None


def _extract_commentary(html: str, source: Source) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        selectors_by_source = {
            "BBC Sport":       ["#live-text-commentary", ".lx-stream__post", ".sp-c-live-region"],
            "ESPN FC":         [".LiveScore", ".match-header__events", ".Commentary"],
            "Sofascore":       [".incident", ".event__item", "[data-testid='incident']"],
            "The Guardian":    [".block--content", ".sport-body-text", ".liveblog-body"],
            "Sky Sports":      [".sdc-live-blog__entry", ".match-centre__commentary"],
            "FotMob":          [".matchEvent", ".live-event", "[class*='event']"],
            "Goal.com":        [".match-events", ".live-commentary", "[class*='commentary']"],
            "WhoScored":       [".match-centre-header", ".incidents-table"],
            "CBS Sports":      [".game-tracker", ".play-by-play"],
            "Reuters Sports":  [".article-body", ".StandardArticleBody_body"],
            "AP News Sports":  [".Article", ".RichTextStoryBody"],
            "AS English":      [".live-commentary", ".match-tracker"],
            "LiveScore":       [".match-detail", ".event-list"],
            "Sportstar":       [".article-content", ".live-blog"],
            "Indian Express":  [".full-details", ".story-details"],
            "NDTV Sports":     [".ins_storybody", ".story__content"],
        }

        selectors = selectors_by_source.get(source.name, [])
        texts = []
        for sel in selectors:
            elements = soup.select(sel)
            if elements:
                for el in elements[:50]:
                    t = el.get_text(separator=" ", strip=True)
                    if t:
                        texts.append(t)
                break

        if not texts:
            for tag in soup.find_all(["p", "li", "span", "div"], limit=100):
                t = tag.get_text(separator=" ", strip=True)
                if len(t) > 30:
                    texts.append(t)

        combined = " | ".join(texts)
        return combined[:8000]
    except Exception as e:
        logger.warning(f"[Fetch] Commentary extraction failed for {source.name}: {e}")
        return ""


# ─────────────────────────────────────────────
# PARALLEL GROUP FETCH
# ─────────────────────────────────────────────

@dataclass
class GroupFetchResult:
    match_id: str
    valid_results: list[FetchResult]
    all_results: list[FetchResult]
    group_used: int
    needs_fallback: bool


async def fetch_match_group(
    match_id: str,
    match_context: dict,
    session: aiohttp.ClientSession,
) -> GroupFetchResult:
    group_id = group_rotation.get_group(match_id)
    sources = source_health.get_sources_for_group(group_id)

    if not sources:
        logger.error(f"[Fetch] Match {match_id}: No available sources in group {group_id}!")
        group_rotation.swap_group(match_id)
        group_id = group_rotation.get_group(match_id)
        sources = source_health.get_sources_for_group(group_id)

    logger.info(f"[Fetch] Match {match_id}: Fetching group {group_id} — {[s.name for s in sources]}")

    tasks = [
        _fetch_with_delay(session, source, match_context, random.uniform(0, 5))
        for source in sources
    ]
    results: list[FetchResult] = await asyncio.gather(*tasks, return_exceptions=False)
    valid = [r for r in results if r.success and r.raw_text]

    all_blocked = all(r.status_code in (403, 429) for r in results if r.status_code)
    if all_blocked and results:
        logger.warning(f"[Fetch] Match {match_id}: Entire group {group_id} blocked — swapping")
        group_rotation.swap_group(match_id)

    return GroupFetchResult(
        match_id=match_id,
        valid_results=valid,
        all_results=results,
        group_used=group_id,
        needs_fallback=len(valid) < 3,
    )


async def _fetch_with_delay(session, source, match_context, delay):
    if delay > 0:
        await asyncio.sleep(delay)
    return await fetch_single_source(session, source, match_context)


async def fetch_all_active_matches(matches: list[dict]) -> list[GroupFetchResult]:
    if not matches:
        return []
    logger.info(f"[Fetch] Starting parallel fetch for {len(matches)} matches")
    connector = aiohttp.TCPConnector(limit=30, limit_per_host=5, ttl_dns_cache=300, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            fetch_match_group(match_id=m["match_id"], match_context=m, session=session)
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
# FALLBACKS
# ─────────────────────────────────────────────

async def tavily_fallback(match_id: str, home_team: str, away_team: str) -> Optional[str]:
    import os
    from tavily import TavilyClient
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return None
    query = f"{home_team} vs {away_team} live score World Cup 2026"
    logger.warning(f"[Fallback] Tavily: '{query}'")
    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(query=query, search_depth="basic", max_results=3, include_raw_content=True)
        texts = [
            f"[{r.get('title', '')}] {(r.get('raw_content') or r.get('content', ''))[:1500]}"
            for r in response.get("results", [])
            if r.get("raw_content") or r.get("content")
        ]
        return " | ".join(texts) or None
    except Exception as e:
        logger.error(f"[Fallback] Tavily failed: {e}")
        return None


async def linkup_fallback(match_id: str, home_team: str, away_team: str) -> Optional[str]:
    import os, httpx
    api_key = os.getenv("LINKUP_API_KEY")
    if not api_key:
        return None
    query = f"{home_team} {away_team} live World Cup score"
    logger.warning(f"[Fallback] Linkup: '{query}'")
    try:
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
    if active_matches == 1:
        return 15
    if active_matches == 2:
        return 20
    return random.randint(30, 60)


def init_daily_rotation(match_ids: list[str]):
    group_rotation.rotate_if_new_day(match_ids)
    logger.info(f"[GroupRotation] Today's assignments: {group_rotation._assignments}")