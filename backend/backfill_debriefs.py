"""
backfill_debriefs.py — One-off post-match debrief generator
Run once locally to backfill all finished matches that have no debrief yet.

Context: The first 12 matches finished before the live pipeline was operational
(app was still being built and deployed). This script generates honest debriefs
that acknowledge the model was at v0 with no live data during those matches,
but includes full real match data: scorers, assists, cards.

Safe to run multiple times — skips matches that already have a debrief.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from groq import AsyncGroq
from main import SessionLocal, MatchDB, PredictionDB
from loguru import logger

# ─────────────────────────────────────────────
# FULL MATCH DATA — M001 to M012
# ─────────────────────────────────────────────

MATCH_DATA = {
    "WC2026_M001": {
        "home": "Mexico", "away": "South Africa",
        "home_score": 2, "away_score": 0,
        "goals": [
            {"player": "Julián Quiñones", "team": "Mexico", "minute": "9'"},
            {"player": "Raúl Jiménez", "team": "Mexico", "minute": "35'"},
        ],
        "assists": [
            {"player": "César Montes", "team": "Mexico"},
            {"player": "Henry Martín", "team": "Mexico"},
        ],
        "cards": [
            {"player": "T. Mokoena", "team": "South Africa", "type": "Yellow", "minute": "41'"},
            {"player": "M. Mvala", "team": "South Africa", "type": "Yellow", "minute": "68'"},
            {"player": "S. Sithole", "team": "South Africa", "type": "Red", "minute": "74'"},
        ],
    },
    "WC2026_M002": {
        "home": "South Korea", "away": "Czech Republic",
        "home_score": 2, "away_score": 1,
        "goals": [
            {"player": "Ladislav Krejčí", "team": "Czech Republic", "minute": "58'"},
            {"player": "Hwang In-beom", "team": "South Korea", "minute": "66'"},
            {"player": "Oh Hyeon-gyu", "team": "South Korea", "minute": "79'"},
        ],
        "assists": [
            {"player": "Lee Kang-in", "team": "South Korea"},
            {"player": "Tomáš Souček", "team": "Czech Republic"},
        ],
        "cards": [
            {"player": "Kim Min-jae", "team": "South Korea", "type": "Yellow", "minute": "31'"},
            {"player": "L. Krejčí", "team": "Czech Republic", "type": "Yellow", "minute": "44'"},
            {"player": "V. Coufal", "team": "Czech Republic", "type": "Yellow", "minute": "70'"},
        ],
    },
    "WC2026_M003": {
        "home": "Canada", "away": "Bosnia and Herzegovina",
        "home_score": 1, "away_score": 1,
        "goals": [
            {"player": "Jovo Lukić", "team": "Bosnia and Herzegovina", "minute": "20'"},
            {"player": "Cyle Larin", "team": "Canada", "minute": "77'"},
        ],
        "assists": [
            {"player": "Alphonso Davies", "team": "Canada"},
            {"player": "Amar Dedić", "team": "Bosnia and Herzegovina"},
        ],
        "cards": [
            {"player": "Alistair Johnston", "team": "Canada", "type": "Yellow", "minute": "54'"},
            {"player": "Benjamin Tahirović", "team": "Bosnia and Herzegovina", "type": "Yellow", "minute": "62'"},
        ],
    },
    "WC2026_M004": {
        "home": "Qatar", "away": "Switzerland",
        "home_score": 1, "away_score": 1,
        "goals": [
            {"player": "Breel Embolo", "team": "Switzerland", "minute": "17' (pen)"},
            {"player": "Boualem Khoukhi", "team": "Qatar", "minute": "90+4'"},
        ],
        "assists": [
            {"player": "Akram Afif", "team": "Qatar"},
            {"player": "Granit Xhaka", "team": "Switzerland"},
        ],
        "cards": [
            {"player": "Pedro Miguel", "team": "Qatar", "type": "Yellow", "minute": "44'"},
            {"player": "Manuel Akanji", "team": "Switzerland", "type": "Yellow", "minute": "81'"},
        ],
    },
    "WC2026_M005": {
        "home": "Brazil", "away": "Morocco",
        "home_score": 1, "away_score": 1,
        "goals": [
            {"player": "Ismael Saibari", "team": "Morocco", "minute": "28'"},
            {"player": "Vinicius Jr.", "team": "Brazil", "minute": "35'"},
        ],
        "assists": [
            {"player": "Rodrygo", "team": "Brazil"},
            {"player": "Achraf Hakimi", "team": "Morocco"},
        ],
        "cards": [
            {"player": "Lucas Paquetá", "team": "Brazil", "type": "Yellow", "minute": "19'"},
            {"player": "Marquinhos", "team": "Brazil", "type": "Yellow", "minute": "74'"},
            {"player": "Sofyan Amrabat", "team": "Morocco", "type": "Yellow", "minute": "52'"},
        ],
    },
    "WC2026_M006": {
        "home": "Haiti", "away": "Scotland",
        "home_score": 0, "away_score": 1,
        "goals": [
            {"player": "John McGinn", "team": "Scotland", "minute": "27'"},
        ],
        "assists": [
            {"player": "Billy Gilmour", "team": "Scotland"},
        ],
        "cards": [
            {"player": "Carlens Arcus", "team": "Haiti", "type": "Yellow", "minute": "33'"},
            {"player": "Scott McTominay", "team": "Scotland", "type": "Yellow", "minute": "61'"},
        ],
    },
    "WC2026_M007": {
        "home": "USA", "away": "Paraguay",
        "home_score": 4, "away_score": 1,
        "goals": [
            {"player": "C. Bobadilla (OG)", "team": "USA", "minute": "5'"},
            {"player": "Folarin Balogun", "team": "USA", "minute": "22'"},
            {"player": "M. González", "team": "Paraguay", "minute": "39'"},
            {"player": "Folarin Balogun", "team": "USA", "minute": "45+2'"},
            {"player": "Gio Reyna", "team": "USA", "minute": "88'"},
        ],
        "assists": [
            {"player": "Christian Pulisic", "team": "USA"},
            {"player": "Christian Pulisic", "team": "USA"},
            {"player": "Antonee Robinson", "team": "USA"},
            {"player": "Julio Enciso", "team": "Paraguay"},
        ],
        "cards": [
            {"player": "Weston McKennie", "team": "USA", "type": "Yellow", "minute": "14'"},
            {"player": "Chris Richards", "team": "USA", "type": "Yellow", "minute": "70'"},
            {"player": "Gustavo Gómez", "team": "Paraguay", "type": "Yellow", "minute": "41'"},
        ],
    },
    "WC2026_M008": {
        "home": "Australia", "away": "Türkiye",
        "home_score": 2, "away_score": 0,
        "goals": [
            {"player": "Nestory Irankunda", "team": "Australia", "minute": "41'"},
            {"player": "Connor Metcalfe", "team": "Australia", "minute": "72'"},
        ],
        "assists": [
            {"player": "Craig Goodwin", "team": "Australia"},
            {"player": "Jackson Irvine", "team": "Australia"},
        ],
        "cards": [
            {"player": "Harry Souttar", "team": "Australia", "type": "Yellow", "minute": "29'"},
            {"player": "Hakan Çalhanoğlu", "team": "Türkiye", "type": "Yellow", "minute": "45'"},
            {"player": "Merih Demiral", "team": "Türkiye", "type": "Yellow", "minute": "67'"},
        ],
    },
    "WC2026_M009": {
        "home": "Germany", "away": "Curaçao",
        "home_score": 7, "away_score": 1,
        "goals": [
            {"player": "Felix Nmecha", "team": "Germany", "minute": "6'"},
            {"player": "Livano Comenencia", "team": "Curaçao", "minute": "21'"},
            {"player": "Nico Schlotterbeck", "team": "Germany", "minute": "38'"},
            {"player": "Kai Havertz", "team": "Germany", "minute": "45+5' (pen)"},
            {"player": "Jamal Musiala", "team": "Germany", "minute": "47'"},
            {"player": "Nathaniel Brown", "team": "Germany", "minute": "68'"},
            {"player": "Deniz Undav", "team": "Germany", "minute": "78'"},
            {"player": "Kai Havertz", "team": "Germany", "minute": "88'"},
        ],
        "assists": [
            {"player": "Jamal Musiala", "team": "Germany"},
            {"player": "Jamal Musiala", "team": "Germany"},
            {"player": "Florian Wirtz", "team": "Germany"},
            {"player": "Florian Wirtz", "team": "Germany"},
            {"player": "Joshua Kimmich", "team": "Germany"},
            {"player": "Juninho Bacuna", "team": "Curaçao"},
        ],
        "cards": [
            {"player": "Antonio Rüdiger", "team": "Germany", "type": "Yellow", "minute": "44'"},
            {"player": "Leandro Bacuna", "team": "Curaçao", "type": "Yellow", "minute": "12'"},
        ],
    },
    "WC2026_M010": {
        "home": "Ivory Coast", "away": "Ecuador",
        "home_score": 1, "away_score": 0,
        "goals": [
            {"player": "Amad Diallo", "team": "Ivory Coast", "minute": "89'"},
        ],
        "assists": [
            {"player": "Franck Kessié", "team": "Ivory Coast"},
        ],
        "cards": [
            {"player": "Evan Ndicka", "team": "Ivory Coast", "type": "Yellow", "minute": "27'"},
            {"player": "Ibrahim Sangaré", "team": "Ivory Coast", "type": "Yellow", "minute": "37'"},
            {"player": "Wilfried Singo", "team": "Ivory Coast", "type": "Yellow", "minute": "39'"},
            {"player": "Piero Hincapié", "team": "Ecuador", "type": "Yellow", "minute": "72'"},
        ],
    },
    "WC2026_M011": {
        "home": "Netherlands", "away": "Japan",
        "home_score": 2, "away_score": 2,
        "goals": [
            {"player": "Virgil van Dijk", "team": "Netherlands", "minute": "50'"},
            {"player": "Keito Nakamura", "team": "Japan", "minute": "56'"},
            {"player": "Crysencio Summerville", "team": "Netherlands", "minute": "63'"},
            {"player": "Daichi Kamada", "team": "Japan", "minute": "88'"},
        ],
        "assists": [
            {"player": "Cody Gakpo", "team": "Netherlands"},
            {"player": "Jeremie Frimpong", "team": "Netherlands"},
            {"player": "Takefusa Kubo", "team": "Japan"},
        ],
        "cards": [
            {"player": "Nathan Aké", "team": "Netherlands", "type": "Yellow", "minute": "60'"},
            {"player": "Stefan de Vrij", "team": "Netherlands", "type": "Yellow", "minute": "82'"},
            {"player": "Tijjani Reijnders", "team": "Netherlands", "type": "Yellow", "minute": "90'"},
        ],
    },
    "WC2026_M012": {
        "home": "Sweden", "away": "Tunisia",
        "home_score": 5, "away_score": 1,
        "goals": [
            {"player": "Yasin Ayari", "team": "Sweden", "minute": "6'"},
            {"player": "Alexander Isak", "team": "Sweden", "minute": "29'"},
            {"player": "Omar Rekik", "team": "Tunisia", "minute": "42'"},
            {"player": "Viktor Gyökeres", "team": "Sweden", "minute": "58'"},
            {"player": "Mattias Svanberg", "team": "Sweden", "minute": "83'"},
            {"player": "Yasin Ayari", "team": "Sweden", "minute": "90+5'"},
        ],
        "assists": [
            {"player": "Alexander Isak", "team": "Sweden"},
            {"player": "Viktor Gyökeres", "team": "Sweden"},
            {"player": "Victor Lindelöf", "team": "Sweden"},
            {"player": "Hannibal Mejbri", "team": "Tunisia"},
        ],
        "cards": [
            {"player": "Ellyes Skhiri", "team": "Tunisia", "type": "Yellow", "minute": "53'"},
        ],
    },
}

DEV_MATCH_IDS = set(MATCH_DATA.keys())


# ─────────────────────────────────────────────
# GROQ WITH KEY 2 FALLBACK
# ─────────────────────────────────────────────

async def _call_groq_with_fallback(prompt: str, system: str) -> str:
    """Try key 1 first. If it fails, try key 2. We do not fail. We win. Always."""
    key1 = os.getenv("GROQ_API_KEY_1", "")
    key2 = os.getenv("GROQ_API_KEY_2", "")

    for key, label in [(key1, "key1"), (key2, "key2")]:
        if not key:
            logger.warning(f"[Backfill] Groq {label} not set — skipping")
            continue
        try:
            client = AsyncGroq(api_key=key)
            response = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=500,
                timeout=30,
            )
            result = response.choices[0].message.content.strip()
            logger.info(f"[Backfill] Groq {label} succeeded")
            return result
        except Exception as e:
            logger.warning(f"[Backfill] Groq {label} failed: {e} — trying next key")
            continue

    return ""  # both keys failed — caller handles with rich static fallback


# ─────────────────────────────────────────────
# FORMATTERS
# ─────────────────────────────────────────────

def _format_goals(goals: list[dict]) -> str:
    return ", ".join([f"{g['player']} ({g['minute']})" for g in goals]) or "None"

def _format_cards(cards: list[dict]) -> str:
    if not cards:
        return "None"
    return ", ".join([f"{c['player']} ({c['type']}, {c['minute']})" for c in cards])

def _format_assists(assists: list[dict]) -> str:
    if not assists:
        return "None"
    return ", ".join([f"{a['player']}" for a in assists])


# ─────────────────────────────────────────────
# DEBRIEF BUILDERS
# ─────────────────────────────────────────────

def _dev_period_debrief(match_id: str, data: dict) -> str:
    """Honest static debrief for M001-M012. Full real data, no AI fabrication."""
    home  = data["home"]
    away  = data["away"]
    hs    = data["home_score"]
    as_   = data["away_score"]

    if hs > as_:
        result_line = f"{home} won {hs}–{as_}"
    elif as_ > hs:
        result_line = f"{away} won {as_}–{hs}"
    else:
        result_line = f"The match ended {hs}–{hs} (draw)"

    match_num   = int(match_id.split("_M")[-1])
    goals_str   = _format_goals(data["goals"])
    assists_str = _format_assists(data["assists"])
    cards_str   = _format_cards(data["cards"])

    return (
        f"[AI Generated — Development Period Note] "
        f"This was Match {match_num} of the FIFA World Cup 2026 ({home} vs {away}). "
        f"{result_line}. "
        f"Goals: {goals_str}. "
        f"Assists: {assists_str}. "
        f"Cards: {cards_str}. "
        f"No live AI prediction was active during this match — the Delta system was "
        f"under active development and the model was at v0 with no World Cup 2026 "
        f"training data yet collected. "
        f"This result has since been added to the training dataset. "
        f"Full AI analysis and live predictions begin from Match 13 onwards."
    )


async def _ai_debrief(match_id: str, data: dict, pred) -> str:
    """AI-generated debrief for post-pipeline matches. Key 1 → key 2 fallback."""
    home  = data["home"]
    away  = data["away"]
    hs    = data["home_score"]
    as_   = data["away_score"]

    if hs > as_:
        winner = home
    elif as_ > hs:
        winner = away
    else:
        winner = "draw"

    if pred:
        home_pct = round((pred.home_win or 0.33) * 100)
        draw_pct = round((pred.draw or 0.33) * 100)
        away_pct = round((pred.away_win or 0.33) * 100)
        probs    = {"home": pred.home_win or 0, "draw": pred.draw or 0, "away": pred.away_win or 0}
        ai_pick  = max(probs, key=probs.get)
        ai_correct = (
            (ai_pick == "home" and hs > as_) or
            (ai_pick == "away" and as_ > hs) or
            (ai_pick == "draw" and hs == as_)
        )
        pred_line = (
            f"Model predicted: {home} {home_pct}% / Draw {draw_pct}% / {away} {away_pct}%. "
            f"AI was {'correct' if ai_correct else 'wrong'}."
        )
    else:
        pred_line = "No model prediction was stored for this match (model not yet trained)."

    goals_str   = _format_goals(data.get("goals", []))
    assists_str = _format_assists(data.get("assists", []))
    cards_str   = _format_cards(data.get("cards", []))

    prompt = f"""Write a post-match AI debrief for this FIFA World Cup 2026 match.

Match: {home} {hs}–{as_} {away}
Result: {winner} {'won' if winner not in ('draw',) else '(draw)'}
Goals: {goals_str}
Assists: {assists_str}
Cards: {cards_str}
{pred_line}

Write exactly 3 sentences:
1. What happened (mention key scorers by name and minutes)
2. Whether the AI prediction was correct and what it got right or wrong
3. What this result means for the model going forward

Factual, analytical, honest about failures. Label all AI content clearly. Plain text only."""

    system = "You are a football ML analyst writing post-match AI debriefs. Factual, concise, honest. Plain text only."

    result = await _call_groq_with_fallback(prompt, system)

    if not result:
        # Both Groq keys failed — rich static fallback, never return empty
        return (
            f"[AI Generated] {home} {hs}–{as_} {away}. "
            f"Goals: {goals_str}. Assists: {assists_str}. Cards: {cards_str}. "
            f"{pred_line} "
            f"Narrative unavailable — Groq unreachable during generation."
        )

    return f"[AI Generated] {result}"


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

async def backfill():
    db = SessionLocal()

    finished = db.query(MatchDB).filter(
        MatchDB.state.in_(["FINISHED", "FT"])
    ).order_by(MatchDB.kickoff_utc.asc()).all()

    if not finished:
        print("No finished matches found in database.")
        db.close()
        return

    print(f"Found {len(finished)} finished matches.\n")

    skipped_already = 0
    dev_period      = 0
    ai_generated    = 0
    failed          = 0

    for match in finished:
        print(f"{'─' * 60}")
        print(f"{match.id} — {match.home_team} {match.home_score}–{match.away_score} {match.away_team}")

        if match.post_match_debrief:
            print("⏭  Already has debrief — skipping")
            skipped_already += 1
            continue

        # ── Development period M001-M012 ───────────────────────────
        if match.id in DEV_MATCH_IDS:
            data = MATCH_DATA[match.id]
            print("📋 Development period match — writing honest context debrief")
            match.post_match_debrief = _dev_period_debrief(match.id, data)
            db.commit()
            print("✅ Saved.")
            dev_period += 1
            continue

        # ── Post-pipeline matches — full AI debrief ────────────────
        data = MATCH_DATA.get(match.id) or {
            "home": match.home_team,
            "away": match.away_team,
            "home_score": match.home_score,
            "away_score": match.away_score,
            "goals": [], "assists": [], "cards": [],
        }

        pred = db.query(PredictionDB).filter(
            PredictionDB.match_id == match.id
        ).order_by(PredictionDB.id.desc()).first()

        print("🤖 Generating AI debrief (key 1 → key 2 fallback)...")
        try:
            debrief = await _ai_debrief(match.id, data, pred)
            match.post_match_debrief = debrief
            db.commit()
            print(f"✅ {debrief[:120]}...")
            ai_generated += 1
            await asyncio.sleep(2)
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            failed += 1
            continue

    db.close()

    print(f"\n{'═' * 60}")
    print(f"Backfill complete.")
    print(f"  Already had debrief:        {skipped_already}")
    print(f"  Development period note:    {dev_period}")
    print(f"  AI debrief generated:       {ai_generated}")
    print(f"  Failed:                     {failed}")
    print(f"{'═' * 60}")
    if failed == 0:
        print("✅ All matches have post-match content. Refresh the frontend.")
    else:
        print(f"⚠️  {failed} failed. Run again — already-done matches are skipped.")


if __name__ == "__main__":
    asyncio.run(backfill())