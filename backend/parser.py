"""
parser.py — Layer 2: Parsing + Validation
==========================================
Groq LLM extracts structured JSON from raw commentary text.
Pydantic v2 validates schema. Player names checked against 1,104 squad list.
Hallucination guard rejects impossible values.
Failover: Groq 8b Key 1 → Key 2 → 70b → cached state.
"""

import os
import json
import re
import time
from datetime import datetime
from typing import Optional, Literal
from enum import Enum

from groq import AsyncGroq
from pydantic import BaseModel, Field, field_validator, model_validator
from loguru import logger

# ─────────────────────────────────────────────
# SQUAD DATA (48 teams × 23 players = 1,104 names)
# Loaded from JSON file at startup
# ─────────────────────────────────────────────

_KNOWN_PLAYERS: set[str] = set()
_KNOWN_TEAMS: set[str] = set()


def load_squad_data(squads_path: str = "backend/squads/wc2026_squads.json"):
    """
    Load all 48 WC 2026 squads from JSON.
    Structure: {"Team Name": ["Player1", "Player2", ...], ...}
    """
    global _KNOWN_PLAYERS, _KNOWN_TEAMS
    try:
        with open(squads_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _KNOWN_TEAMS = set(data.keys())
        for team, players in data.items():
            _KNOWN_PLAYERS.update(p.lower() for p in players)
        logger.info(f"[Parser] Loaded {len(_KNOWN_PLAYERS)} players from {len(_KNOWN_TEAMS)} teams")
    except FileNotFoundError:
        logger.warning(f"[Parser] Squad file not found at {squads_path} — player validation disabled")
    except Exception as e:
        logger.error(f"[Parser] Failed to load squad data: {e}")


def _player_known(name: str) -> bool:
    """Fuzzy check: exact match or last-name match."""
    if not _KNOWN_PLAYERS:
        return True  # disabled if no squad data loaded
    name_lower = name.lower().strip()
    # Exact match
    if name_lower in _KNOWN_PLAYERS:
        return True
    # Last name match (e.g. "Haaland" matches "Erling Haaland")
    for known in _KNOWN_PLAYERS:
        if name_lower in known or known.endswith(name_lower):
            return True
    return False


def _team_known(name: str) -> bool:
    if not _KNOWN_TEAMS:
        return True
    return any(name.lower() in t.lower() or t.lower() in name.lower() for t in _KNOWN_TEAMS)


# ─────────────────────────────────────────────
# PYDANTIC SCHEMAS
# ─────────────────────────────────────────────

class EventType(str, Enum):
    GOAL = "goal"
    OWN_GOAL = "own_goal"
    PENALTY_SCORED = "penalty_scored"
    PENALTY_MISSED = "penalty_missed"
    YELLOW_CARD = "yellow_card"
    RED_CARD = "red_card"
    SECOND_YELLOW = "second_yellow"
    SUBSTITUTION = "substitution"
    INJURY = "injury"
    VAR_REVIEW = "var_review"
    VAR_GOAL_DISALLOWED = "var_goal_disallowed"
    KICK_OFF = "kick_off"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"
    EXTRA_TIME_START = "extra_time_start"
    PENALTIES_START = "penalties_start"
    MATCH_FINISHED = "match_finished"


class MatchEvent(BaseModel):
    type: EventType
    minute: str = Field(..., description="Match minute as string, e.g. '45+3'")
    player: Optional[str] = Field(None, description="Primary player involved")
    player_off: Optional[str] = Field(None, description="Player subbed off (substitutions only)")
    team: Optional[str] = Field(None, description="Team involved")
    description: Optional[str] = Field(None, description="Brief factual description, max 20 words")
    sentiment: Optional[Literal["calm", "notable", "dramatic", "chaotic"]] = None
    context: Optional[str] = Field(None, description="Tactical context if clear, e.g. 'Counter attack'")

    @field_validator("minute")
    @classmethod
    def validate_minute(cls, v: str) -> str:
        # Accept "45", "45+3", "90+7", "120" etc.
        clean = v.strip().replace("'", "")
        base_match = re.match(r"^(\d+)(\+\d+)?$", clean)
        if not base_match:
            raise ValueError(f"Invalid minute format: {v}")
        base = int(base_match.group(1))
        if base > 130:
            raise ValueError(f"Minute {base} exceeds 130 — likely hallucination")
        return clean

    @field_validator("player", "player_off")
    @classmethod
    def validate_player(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _player_known(v):
            logger.warning(f"[Parser] Unknown player: '{v}' — accepting but flagging")
            # Don't reject — player might be unlisted (late squad change, etc.)
            # Parser logs warning, admin can review
        return v.strip()

    @field_validator("team")
    @classmethod
    def validate_team(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _team_known(v):
            raise ValueError(f"Team '{v}' not in known WC 2026 teams — likely hallucination")
        return v.strip()


class ScoreModel(BaseModel):
    home: int = Field(..., ge=0, le=15)
    away: int = Field(..., ge=0, le=15)

    @model_validator(mode="after")
    def validate_total_goals(self) -> "ScoreModel":
        if self.home + self.away > 20:
            raise ValueError(f"Total goals {self.home + self.away} > 20 — hallucination guard")
        return self


class ConfidenceModel(BaseModel):
    home_win: float = Field(..., ge=0.02, le=0.98)
    draw: float = Field(..., ge=0.02, le=0.98)
    away_win: float = Field(..., ge=0.02, le=0.98)

    @model_validator(mode="after")
    def probabilities_sum(self) -> "ConfidenceModel":
        total = self.home_win + self.draw + self.away_win
        if not (0.98 <= total <= 1.02):
            raise ValueError(f"Probabilities sum to {total:.3f}, expected ~1.0")
        return self


class ParsedMatchState(BaseModel):
    match_id: str
    timestamp: str              # UTC ISO string
    score: ScoreModel
    minute: str
    state: str                  # LIVE, HT, FT, etc.
    events: list[MatchEvent] = Field(default_factory=list)
    ai_context: str = Field(..., max_length=200, description="One-sentence narrative for UI")
    model_confidence: ConfidenceModel
    source_used: str
    parse_latency_ms: float
    hallucination_flags: list[str] = Field(default_factory=list)
    raw_text_length: int = 0


# ─────────────────────────────────────────────
# GROQ CLIENT SETUP
# ─────────────────────────────────────────────

def _get_groq_clients() -> tuple[AsyncGroq, AsyncGroq]:
    key1 = os.getenv("GROQ_API_KEY_1")
    key2 = os.getenv("GROQ_API_KEY_2")
    if not key1 or not key2:
        raise ValueError("GROQ_API_KEY_1 and GROQ_API_KEY_2 must be set in .env")
    return AsyncGroq(api_key=key1), AsyncGroq(api_key=key2)


GROQ_MODEL_FAST = "llama-3.1-8b-instant"    # speed-critical live parsing
GROQ_MODEL_SMART = "llama-3.3-70b-versatile" # fallback when 8b schema fails

SYSTEM_PROMPT = """You are a football match data extraction AI for a research project.
Extract structured match data from live commentary text and return ONLY valid JSON.
No preamble. No markdown. No explanation. Only the JSON object.

Rules:
- Extract only events explicitly mentioned in the text
- Never invent scores, players, or events not in the source text
- If minute is unclear, use the most recent clear minute mentioned
- Player names: use the most complete version mentioned (prefer last name)
- Sentiment: "calm" (normal play), "notable" (significant event), "dramatic" (crucial moment), "chaotic" (multiple events/controversy)
- ai_context: one sentence max, factual, present tense
- model_confidence: your statistical estimate given current score and minute (home_win + draw + away_win must sum to 1.0)
"""

def _build_extraction_prompt(
    match_id: str,
    home_team: str,
    away_team: str,
    current_score: dict,
    current_minute: str,
    match_state: str,
    raw_text: str,
) -> str:
    return f"""Extract match data from this live commentary.

Match: {home_team} vs {away_team} (ID: {match_id})
Current known score: {home_team} {current_score.get('home', 0)} - {current_score.get('away', 0)} {away_team}
Current minute: {current_minute}
Match state: {match_state}

Live commentary text:
{raw_text[:4000]}

Return this exact JSON structure (no other text):
{{
  "match_id": "{match_id}",
  "timestamp": "<UTC ISO datetime>",
  "score": {{"home": <int>, "away": <int>}},
  "minute": "<string>",
  "state": "<SCHEDULED|LIVE|HT|LIVE_2H|FT|ET_1H|ET_HT|ET_2H|PENALTIES|FINISHED>",
  "events": [
    {{
      "type": "<event_type>",
      "minute": "<string>",
      "player": "<name or null>",
      "player_off": "<name or null>",
      "team": "<team or null>",
      "description": "<max 20 words>",
      "sentiment": "<calm|notable|dramatic|chaotic>",
      "context": "<tactical context or null>"
    }}
  ],
  "ai_context": "<one sentence, present tense, factual>",
  "model_confidence": {{
    "home_win": <0.02-0.98>,
    "draw": <0.02-0.98>,
    "away_win": <0.02-0.98>
  }},
  "source_used": "<source name>"
}}
"""


# ─────────────────────────────────────────────
# CROSS-REFERENCE (3+ sources)
# ─────────────────────────────────────────────

def cross_reference_texts(texts: list[str]) -> str:
    """
    When 3+ sources available, combine them for more reliable input to Groq.
    Prioritise consistent facts, flag contradictions for Groq to resolve.
    """
    if len(texts) == 1:
        return texts[0]

    # Simple approach: concatenate with source separators
    # Groq will naturally weight consistent information
    combined = "\n\n---\n\n".join([
        f"SOURCE {i+1}:\n{text[:2000]}"
        for i, text in enumerate(texts[:3])
    ])
    return combined


# ─────────────────────────────────────────────
# MAIN PARSE FUNCTION
# ─────────────────────────────────────────────

async def parse_match_state(
    match_id: str,
    home_team: str,
    away_team: str,
    raw_texts: list[str],           # from 1-3 sources
    source_names: list[str],
    current_score: dict,
    current_minute: str,
    match_state: str,
    match_index: int = 0,           # 0-2 use key 1, 3-5 use key 2
    last_good_state: Optional[dict] = None,
) -> Optional[ParsedMatchState]:
    """
    Main parse entry point.
    Failover: Groq 8b Key1 → Key2 → 70b → cached state.
    """
    start = time.time()

    # Cross-reference if multiple sources
    combined_text = cross_reference_texts(raw_texts)
    source_used = ", ".join(source_names[:3])

    client_1, client_2 = _get_groq_clients()
    clients_8b = [client_1 if match_index < 3 else client_2]  # key split by match
    clients_fallback = [client_2 if match_index < 3 else client_1, client_1]  # other key then retry

    prompt = _build_extraction_prompt(
        match_id, home_team, away_team,
        current_score, current_minute, match_state,
        combined_text,
    )

    # Attempt 1: Groq 8b (fast)
    result = await _try_groq(
        client=clients_8b[0],
        model=GROQ_MODEL_FAST,
        prompt=prompt,
        system=SYSTEM_PROMPT,
        attempt_label="8b-primary",
    )

    # Attempt 2: Other 8b key
    if result is None:
        logger.warning(f"[Parser] {match_id}: 8b primary failed — trying 8b secondary key")
        result = await _try_groq(
            client=clients_fallback[0],
            model=GROQ_MODEL_FAST,
            prompt=prompt,
            system=SYSTEM_PROMPT,
            attempt_label="8b-secondary",
        )

    # Attempt 3: 70b (intelligence fallback for schema failures)
    if result is None:
        logger.warning(f"[Parser] {match_id}: Both 8b keys failed — escalating to 70b")
        result = await _try_groq(
            client=clients_fallback[1],
            model=GROQ_MODEL_SMART,
            prompt=prompt,
            system=SYSTEM_PROMPT,
            attempt_label="70b-fallback",
        )

    if result is None:
        logger.error(f"[Parser] {match_id}: All Groq attempts failed — serving last known good state")
        if last_good_state:
            # Reconstruct from cached dict
            try:
                return ParsedMatchState(**last_good_state)
            except Exception:
                pass
        return None

    # Validate with Pydantic
    validated = _validate_parsed(result, match_id, source_used)
    if validated is None:
        logger.error(f"[Parser] {match_id}: Validation failed — serving last known good state")
        return None

    latency = (time.time() - start) * 1000
    validated.parse_latency_ms = latency
    validated.raw_text_length = len(combined_text)

    logger.info(
        f"[Parser] {match_id}: Parsed OK "
        f"({validated.score.home}-{validated.score.away}) "
        f"min={validated.minute} latency={latency:.0f}ms"
    )
    return validated


async def _try_groq(
    client: AsyncGroq,
    model: str,
    prompt: str,
    system: str,
    attempt_label: str,
) -> Optional[dict]:
    """Single Groq attempt. Returns raw dict or None."""
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,        # low temperature = consistent JSON
            max_tokens=1000,
            timeout=20,
        )
        text = response.choices[0].message.content.strip()

        # Strip any accidental markdown code fences
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

        data = json.loads(text)
        logger.debug(f"[Parser] {attempt_label}: JSON parsed OK")
        return data

    except json.JSONDecodeError as e:
        logger.warning(f"[Parser] {attempt_label}: JSON decode failed — {e}")
        return None
    except Exception as e:
        logger.warning(f"[Parser] {attempt_label}: Groq call failed — {e}")
        return None


def _validate_parsed(raw: dict, match_id: str, source_used: str) -> Optional[ParsedMatchState]:
    """Run Pydantic validation + hallucination checks."""
    flags = []

    try:
        # Ensure match_id matches
        raw["match_id"] = match_id
        raw["source_used"] = source_used
        raw["parse_latency_ms"] = 0.0   # set by caller
        raw["hallucination_flags"] = flags

        # Timestamp: ensure valid UTC
        if "timestamp" not in raw or not raw["timestamp"]:
            raw["timestamp"] = datetime.utcnow().isoformat()

        # Minute sanity
        minute_str = str(raw.get("minute", "0"))
        try:
            base_minute = int(re.match(r"(\d+)", minute_str).group(1))
            if base_minute > 130:
                flags.append(f"minute>{base_minute} rejected")
                raw["minute"] = "90"
        except Exception:
            raw["minute"] = "0"

        # Validate events list
        events = raw.get("events", [])
        valid_events = []
        for ev in events:
            try:
                validated_ev = MatchEvent(**ev)
                valid_events.append(validated_ev.model_dump())
            except Exception as ve:
                flags.append(f"event_rejected:{ev.get('type','?')}:{ve}")
                logger.debug(f"[Parser] Event rejected: {ev} — {ve}")
        raw["events"] = valid_events

        # Normalise confidence to sum to 1.0
        conf = raw.get("model_confidence", {})
        total = sum([
            conf.get("home_win", 0.33),
            conf.get("draw", 0.33),
            conf.get("away_win", 0.34),
        ])
        if total > 0:
            raw["model_confidence"] = {
                "home_win": max(0.02, min(0.98, conf.get("home_win", 0.33) / total)),
                "draw": max(0.02, min(0.98, conf.get("draw", 0.33) / total)),
                "away_win": max(0.02, min(0.98, conf.get("away_win", 0.34) / total)),
            }

        parsed = ParsedMatchState(**raw)
        if flags:
            parsed.hallucination_flags = flags
            logger.warning(f"[Parser] {match_id}: Flags raised: {flags}")
        return parsed

    except Exception as e:
        logger.error(f"[Parser] Pydantic validation failed for {match_id}: {e}")
        return None


# ─────────────────────────────────────────────
# PRE/POST MATCH INTELLIGENCE
# ─────────────────────────────────────────────

async def generate_pre_match_brief(
    home_team: str,
    away_team: str,
    news_summaries: list[dict],     # [{headline, source, url}, ...]
    model_prediction: dict,
) -> str:
    """
    Generate pre-match brief using Groq 70b.
    Includes team news, form, key battles, AI prediction reasoning.
    """
    client_1, _ = _get_groq_clients()

    news_text = "\n".join([
        f"- {n.get('headline', '')} (Source: {n.get('source', '')})"
        for n in news_summaries[:5]
    ])

    prompt = f"""Write a pre-match brief for {home_team} vs {away_team} in the FIFA World Cup 2026.

Team news extracted from sources:
{news_text}

AI model prediction:
- {home_team} win: {model_prediction.get('home_win_pct', '?')}%
- Draw: {model_prediction.get('draw_pct', '?')}%
- {away_team} win: {model_prediction.get('away_win_pct', '?')}%
- Predicted scorer: {model_prediction.get('predicted_scorer', 'Unknown')}
- Confidence range: {model_prediction.get('confidence_range', '?')}
- Based on: {model_prediction.get('training_matches', '?')} matches

Write 3-4 sentences covering:
1. Team news and fitness updates (cite sources by name, e.g. "per BBC Sport")
2. Key tactical battle to watch
3. AI prediction with brief reasoning
4. One sentence on what could upset the prediction

Keep it factual, analytical, and concise. No hype. Label all AI content clearly."""

    result = await _try_groq(
        client=client_1,
        model=GROQ_MODEL_SMART,
        prompt=prompt,
        system="You are a football analyst. Be factual, concise, analytical. Return plain text only.",
        attempt_label="pre-match-brief",
    )

    if isinstance(result, str):
        return result
    # If 70b returned JSON-like, extract text
    return str(result) if result else f"AI pre-match analysis unavailable for {home_team} vs {away_team}."


async def generate_post_match_debrief(
    home_team: str,
    away_team: str,
    final_score: dict,
    ai_prediction: dict,
    actual_result: str,
    model_updates: list[str],
) -> str:
    """
    Generate post-match debrief: what model got right/wrong, what updated.
    """
    client_1, _ = _get_groq_clients()

    was_correct = ai_prediction.get("predicted_winner") == actual_result
    prompt = f"""Write a post-match debrief for {home_team} {final_score['home']}-{final_score['away']} {away_team}.

AI prediction: {ai_prediction.get('home_win_pct')}% {home_team} / {ai_prediction.get('draw_pct')}% Draw / {ai_prediction.get('away_win_pct')}% {away_team}
AI predicted winner: {ai_prediction.get('predicted_winner')}
Actual result: {actual_result}
AI correct: {'Yes' if was_correct else 'No'}

Model updates after this match:
{chr(10).join(f'- {u}' for u in model_updates[:5])}

Write 3 sentences:
1. Whether AI was correct and what it got right or wrong
2. What surprised the model and why
3. What the model now knows that it didn't before

Label all AI analysis clearly. Be honest about failures."""

    result = await _try_groq(
        client=client_1,
        model=GROQ_MODEL_SMART,
        prompt=prompt,
        system="You are a football ML analyst. Be honest about model failures. Return plain text only.",
        attempt_label="post-match-debrief",
    )

    return str(result) if result else f"AI post-match analysis unavailable for {home_team} vs {away_team}."


async def summarise_news_article(url: str, title: str, source_name: str) -> str:
    """
    Summarise a single news article into one bullet point.
    Used for pre-match team news section.
    """
    client_1, _ = _get_groq_clients()

    prompt = f"""Summarise this football news in ONE bullet point (max 15 words, factual only):
Title: {title}
Source: {source_name}
URL: {url}

Return ONLY the bullet point text. No dash, no asterisk. Just the plain factual sentence."""

    result = await _try_groq(
        client=client_1,
        model=GROQ_MODEL_FAST,
        prompt=prompt,
        system="Extract one key fact. 15 words max. Plain text only.",
        attempt_label="news-summary",
    )

    return str(result).strip() if result else title[:80]
