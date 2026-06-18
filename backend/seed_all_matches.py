#!/usr/bin/env python3
"""
PROJECT DELTA — Master Match Seeder (Session 9)
Seeds all completed + today's + upcoming matches.

Usage:
  export ADMIN_PASSWORD="your-admin-password"
  export API_BASE="https://delta26.onrender.com"
  python seed_all_matches.py
  
  # For local testing:
  export API_BASE="http://localhost:8000"
  python seed_all_matches.py
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone

BASE = os.getenv("API_BASE", "https://delta26.onrender.com")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "changeme")
HEADERS = {
    "Authorization": f"Bearer {ADMIN_PASS}",
    "Content-Type": "application/json",
}

# ─── ALL CONFIRMED RESULTS (June 11–16, finished) ─────────────────────────────
# Format: (match_id, home, home_code, away, away_code, group, venue, kickoff_utc, home_score, away_score)

FINISHED_MATCHES = [
    # JUN 11 — DAY 1
    ("M001", "Mexico", "MEX", "South Africa", "RSA", "A",
     "Estadio Azteca, Mexico City", "2026-06-11T20:00:00Z", 2, 0),

    ("M002", "South Korea", "KOR", "Czechia", "CZE", "A",
     "Estadio Akron, Guadalajara", "2026-06-12T03:00:00Z", 2, 1),

    # JUN 12 — DAY 2
    ("M003", "Canada", "CAN", "Bosnia and Herzegovina", "BIH", "B",
     "BMO Field, Toronto", "2026-06-12T19:00:00Z", 1, 1),

    ("M004", "United States", "USA", "Paraguay", "PAR", "D",
     "SoFi Stadium, Los Angeles", "2026-06-13T01:00:00Z", 4, 1),

    # JUN 13 — DAY 3
    ("M005", "Qatar", "QAT", "Switzerland", "SUI", "B",
     "Levi's Stadium, Santa Clara", "2026-06-13T19:00:00Z", 1, 1),

    ("M006", "Brazil", "BRA", "Morocco", "MAR", "C",
     "MetLife Stadium, East Rutherford", "2026-06-13T22:00:00Z", 1, 1),

    ("M007", "Haiti", "HAI", "Scotland", "SCO", "C",
     "Gillette Stadium, Boston", "2026-06-14T01:00:00Z", 0, 1),

    ("M008", "Australia", "AUS", "Turkiye", "TUR", "D",
     "BC Place, Vancouver", "2026-06-14T01:00:00Z", 2, 0),

    # JUN 14 — DAY 4
    ("M009", "Germany", "GER", "Curacao", "CUW", "E",
     "AT&T Stadium, Arlington", "2026-06-14T19:00:00Z", 7, 1),

    ("M010", "Netherlands", "NED", "Japan", "JPN", "F",
     "Estadio Azteca, Mexico City", "2026-06-14T22:00:00Z", 2, 2),

    ("M011", "Ivory Coast", "CIV", "Ecuador", "ECU", "E",
     "Arrowhead Stadium, Kansas City", "2026-06-15T01:00:00Z", 1, 0),

    ("M012", "Sweden", "SWE", "Tunisia", "TUN", "F",
     "Rose Bowl, Los Angeles", "2026-06-15T01:00:00Z", 5, 1),

    # JUN 15 — DAY 5
    ("M013", "Spain", "ESP", "Cape Verde", "CPV", "H",
     "Lincoln Financial Field, Philadelphia", "2026-06-15T19:00:00Z", 0, 0),

    ("M014", "Belgium", "BEL", "Egypt", "EGY", "G",
     "Hard Rock Stadium, Miami", "2026-06-15T22:00:00Z", 1, 1),

    ("M015", "Saudi Arabia", "KSA", "Uruguay", "URU", "H",
     "Lumen Field, Seattle", "2026-06-16T01:00:00Z", 1, 1),

    ("M016", "Iran", "IRN", "New Zealand", "NZL", "G",
     "Estadio BBVA, Monterrey", "2026-06-16T01:00:00Z", 2, 2),

    # JUN 16 — DAY 6
    ("M017", "France", "FRA", "Senegal", "SEN", "I",
     "Allianz Field, Minneapolis", "2026-06-16T19:00:00Z", 3, 1),

    ("M018", "Iraq", "IRQ", "Norway", "NOR", "I",
     "Empower Field, Denver", "2026-06-16T22:00:00Z", 1, 4),

    ("M019", "Argentina", "ARG", "Algeria", "ALG", "J",
     "AT&T Stadium, Arlington", "2026-06-17T01:00:00Z", 3, 0),

    ("M020", "Austria", "AUT", "Jordan", "JOR", "J",
     "Arrowhead Stadium, Kansas City", "2026-06-17T01:00:00Z", 3, 1),
]

# ─── TODAY'S MATCHES (Jun 17) ─────────────────────────────────────────────────
TODAYS_MATCHES = [
    ("M021", "Portugal", "POR", "Congo DR", "COD", "K",
     "NRG Stadium, Houston", "2026-06-17T17:00:00Z", None, None),

    ("M022", "England", "ENG", "Croatia", "CRO", "L",
     "AT&T Stadium, Arlington", "2026-06-17T20:00:00Z", None, None),

    ("M023", "Ghana", "GHA", "Panama", "PAN", "L",
     "BMO Field, Toronto", "2026-06-17T23:00:00Z", None, None),

    ("M024", "Uzbekistan", "UZB", "Colombia", "COL", "K",
     "Estadio Azteca, Mexico City", "2026-06-18T02:00:00Z", None, None),
]

# ─── UPCOMING — JUN 18 (Matchday 2 starts) ────────────────────────────────────
UPCOMING = [
    ("M025", "Czechia", "CZE", "South Africa", "RSA", "A",
     "Mercedes-Benz Stadium, Atlanta", "2026-06-18T16:00:00Z", None, None),

    ("M026", "Switzerland", "SUI", "Bosnia and Herzegovina", "BIH", "B",
     "SoFi Stadium, Los Angeles", "2026-06-18T19:00:00Z", None, None),

    ("M027", "Canada", "CAN", "Qatar", "QAT", "B",
     "BC Place, Vancouver", "2026-06-18T22:00:00Z", None, None),

    ("M028", "Mexico", "MEX", "South Korea", "KOR", "A",
     "Estadio Akron, Guadalajara", "2026-06-19T02:00:00Z", None, None),

    ("M029", "United States", "USA", "Australia", "AUS", "D",
     "Lumen Field, Seattle", "2026-06-19T19:00:00Z", None, None),

    ("M030", "Scotland", "SCO", "Morocco", "MAR", "C",
     "Gillette Stadium, Boston", "2026-06-19T22:00:00Z", None, None),

    ("M031", "Brazil", "BRA", "Haiti", "HAI", "C",
     "Lincoln Financial Field, Philadelphia", "2026-06-20T01:00:00Z", None, None),

    ("M032", "Turkiye", "TUR", "Paraguay", "PAR", "D",
     "Levi's Stadium, Santa Clara", "2026-06-20T01:00:00Z", None, None),
]


def build_payload(m, state):
    mid, home, hcode, away, acode, grp, venue, ko = m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7]
    hs = m[8] if len(m) > 8 else None
    as_ = m[9] if len(m) > 9 else None

    payload = {
        "match_id": mid,
        "home_team": {"name": home, "code": hcode, "fifa_rank": 0},
        "away_team": {"name": away, "code": acode, "fifa_rank": 0},
        "group": grp,
        "venue": venue,
        "kickoff_utc": ko,
        "state": state,
    }
    if hs is not None:
        payload["home_score"] = hs
        payload["away_score"] = as_
    return payload


def upsert(m, state):
    payload = build_payload(m, state)
    mid = m[0]
    home_disp = m[1]
    away_disp = m[3]
    hs = m[8] if len(m) > 8 else None
    as_ = m[9] if len(m) > 9 else None

    try:
        r = requests.post(
            f"{BASE}/admin/matches/add",
            json=payload,
            headers=HEADERS,
            timeout=20,
        )

        if r.status_code == 200:
            score_str = f" ({hs}–{as_})" if hs is not None else ""
            print(f"  ✅ Added {mid}: {home_disp} vs {away_disp}{score_str}")
            return True

        elif r.status_code in (409, 422) or "already exists" in r.text.lower() or "unique" in r.text.lower():
            # Match exists — update score if we have one
            if hs is not None:
                r2 = requests.post(
                    f"{BASE}/admin/matches/update-score",
                    json={"match_id": mid, "home_score": hs, "away_score": as_, "state": state},
                    headers=HEADERS,
                    timeout=20,
                )
                if r2.status_code == 200:
                    print(f"  🔄 Updated {mid}: {home_disp} {hs}–{as_} {away_disp} [{state}]")
                    return True
                else:
                    print(f"  ⚠️  Update score failed {mid}: {r2.status_code} {r2.text[:100]}")
                    return False
            else:
                print(f"  ℹ️  {mid} already exists, no score to update")
                return True

        else:
            print(f"  ❌ Failed {mid}: HTTP {r.status_code}")
            print(f"     {r.text[:200]}")
            return False

    except requests.exceptions.ConnectionError:
        print(f"  ❌ CONNECTION ERROR — is {BASE} reachable?")
        return False
    except Exception as e:
        print(f"  ❌ Error {mid}: {e}")
        return False


def check_health():
    try:
        r = requests.get(f"{BASE}/health", timeout=10)
        if r.status_code == 200:
            data = r.json()
            print(f"  ✅ Backend healthy: {data}")
            return True
        else:
            print(f"  ❌ Backend /health returned {r.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ Cannot reach backend: {e}")
        print(f"     Make sure {BASE} is running")
        return False


def main():
    print("\n" + "="*60)
    print("  PROJECT DELTA — Match Seeder v9")
    print(f"  Target: {BASE}")
    print("="*60)

    print("\n🔍 Health check...")
    if not check_health():
        print("\n⛔ Backend not reachable. Deploy backend first, then re-run.")
        sys.exit(1)

    ok = err = 0

    print("\n── FINISHED MATCHES (M001–M020) ──────────────────────────────")
    for m in FINISHED_MATCHES:
        if upsert(m, "FINISHED"):
            ok += 1
        else:
            err += 1

    print("\n── TODAY'S MATCHES (M021–M024) ────────────────────────────────")
    for m in TODAYS_MATCHES:
        if upsert(m, "SCHEDULED"):
            ok += 1
        else:
            err += 1

    print("\n── UPCOMING MATCHES (M025–M032) ───────────────────────────────")
    for m in UPCOMING:
        if upsert(m, "SCHEDULED"):
            ok += 1
        else:
            err += 1

    print("\n" + "="*60)
    print(f"  ✅ Success: {ok}  ❌ Failed: {err}")
    print("="*60)

    if err == 0:
        print("\n🏆 All matches seeded! Verify with:")
        print(f"   curl {BASE}/api/matches | python3 -m json.tool | grep match_id")
    else:
        print(f"\n⚠️  {err} match(es) failed. Check errors above.")

    print()


if __name__ == "__main__":
    main()