#!/usr/bin/env python3
"""
PROJECT DELTA — Final Seeder (June 19, 2026)
Reads ADMIN_PASSWORD from environment — already set in Render dashboard.

HOW TO RUN (two options):

OPTION A — From your LOCAL terminal (safe, password never in code):
  export ADMIN_PASSWORD="your-render-env-password"
  python seed_final.py

OPTION B — As a one-off Render job (permanent, no local terminal needed):
  In Render dashboard → your service → Shell tab → type:
  python seed_final.py
  (ADMIN_PASSWORD already exists as env var on Render, no export needed)
"""
import os, sys, requests

BASE = os.getenv("API_BASE", "https://delta26.onrender.com")
PASS = os.getenv("ADMIN_PASSWORD", "")
H = {"Authorization": f"Bearer {PASS}", "Content-Type": "application/json"}

if not PASS:
    print("❌ ADMIN_PASSWORD not set.")
    print("   Run: export ADMIN_PASSWORD='your-password'")
    print("   OR use Render Shell tab where it's already set automatically.")
    sys.exit(1)

# ── ALL MATCHES M001–M032 ─────────────────────────────────────────────────────
# (match_id, home, hcode, away, acode, group, venue, kickoff_utc, home_score, away_score, state)
MATCHES = [
    # JUN 11
    ("M001","Mexico","MEX","South Africa","RSA","A","Estadio Azteca, Mexico City","2026-06-11T20:00:00Z",2,0,"FINISHED"),
    ("M002","South Korea","KOR","Czechia","CZE","A","Estadio Akron, Guadalajara","2026-06-12T03:00:00Z",2,1,"FINISHED"),
    # JUN 12
    ("M003","Canada","CAN","Bosnia and Herzegovina","BIH","B","BMO Field, Toronto","2026-06-12T19:00:00Z",1,1,"FINISHED"),
    ("M004","United States","USA","Paraguay","PAR","D","SoFi Stadium, Los Angeles","2026-06-13T01:00:00Z",4,1,"FINISHED"),
    # JUN 13
    ("M005","Qatar","QAT","Switzerland","SUI","B","Levi's Stadium, Santa Clara","2026-06-13T19:00:00Z",1,1,"FINISHED"),
    ("M006","Brazil","BRA","Morocco","MAR","C","MetLife Stadium, East Rutherford","2026-06-13T22:00:00Z",1,1,"FINISHED"),
    ("M007","Haiti","HAI","Scotland","SCO","C","Gillette Stadium, Boston","2026-06-14T01:00:00Z",0,1,"FINISHED"),
    ("M008","Australia","AUS","Turkiye","TUR","D","BC Place, Vancouver","2026-06-14T01:00:00Z",2,0,"FINISHED"),
    # JUN 14
    ("M009","Germany","GER","Curacao","CUW","E","AT&T Stadium, Arlington","2026-06-14T19:00:00Z",7,1,"FINISHED"),
    ("M010","Netherlands","NED","Japan","JPN","F","Estadio Azteca, Mexico City","2026-06-14T22:00:00Z",2,2,"FINISHED"),
    ("M011","Ivory Coast","CIV","Ecuador","ECU","E","Arrowhead Stadium, Kansas City","2026-06-15T01:00:00Z",1,0,"FINISHED"),
    ("M012","Sweden","SWE","Tunisia","TUN","F","Rose Bowl, Los Angeles","2026-06-15T01:00:00Z",5,1,"FINISHED"),
    # JUN 15
    ("M013","Spain","ESP","Cape Verde","CPV","H","Lincoln Financial Field, Philadelphia","2026-06-15T19:00:00Z",0,0,"FINISHED"),
    ("M014","Belgium","BEL","Egypt","EGY","G","Hard Rock Stadium, Miami","2026-06-15T22:00:00Z",1,1,"FINISHED"),
    ("M015","Saudi Arabia","KSA","Uruguay","URU","H","Lumen Field, Seattle","2026-06-16T01:00:00Z",1,1,"FINISHED"),
    ("M016","Iran","IRN","New Zealand","NZL","G","Estadio BBVA, Monterrey","2026-06-16T01:00:00Z",2,2,"FINISHED"),
    # JUN 16
    ("M017","France","FRA","Senegal","SEN","I","Allianz Field, Minneapolis","2026-06-16T19:00:00Z",3,1,"FINISHED"),
    ("M018","Iraq","IRQ","Norway","NOR","I","Empower Field, Denver","2026-06-16T22:00:00Z",1,4,"FINISHED"),
    ("M019","Argentina","ARG","Algeria","ALG","J","AT&T Stadium, Arlington","2026-06-17T01:00:00Z",3,0,"FINISHED"),
    ("M020","Austria","AUT","Jordan","JOR","J","Arrowhead Stadium, Kansas City","2026-06-17T01:00:00Z",3,1,"FINISHED"),
    # JUN 17
    ("M021","Portugal","POR","Congo DR","COD","K","NRG Stadium, Houston","2026-06-17T17:00:00Z",1,1,"FINISHED"),
    ("M022","England","ENG","Croatia","CRO","L","AT&T Stadium, Arlington","2026-06-17T20:00:00Z",4,2,"FINISHED"),
    ("M023","Ghana","GHA","Panama","PAN","L","BMO Field, Toronto","2026-06-17T23:00:00Z",1,0,"FINISHED"),
    ("M024","Uzbekistan","UZB","Colombia","COL","K","Estadio Banorte, Monterrey","2026-06-18T02:00:00Z",1,3,"FINISHED"),
    # JUN 18 — confirmed scores
    ("M025","Czechia","CZE","South Africa","RSA","A","Mercedes-Benz Stadium, Atlanta","2026-06-18T16:00:00Z",1,1,"FINISHED"),
    ("M026","Switzerland","SUI","Bosnia and Herzegovina","BIH","B","SoFi Stadium, Los Angeles","2026-06-18T19:00:00Z",4,1,"FINISHED"),
    ("M027","Canada","CAN","Qatar","QAT","B","BC Place, Vancouver","2026-06-18T22:00:00Z",6,0,"FINISHED"),
    ("M028","Mexico","MEX","South Korea","KOR","A","Estadio Akron, Guadalajara","2026-06-19T02:00:00Z",1,0,"FINISHED"),
    # JUN 19 — TODAY (scheduled)
    ("M029","United States","USA","Australia","AUS","D","Lumen Field, Seattle","2026-06-19T19:00:00Z",None,None,"SCHEDULED"),
    ("M030","Scotland","SCO","Morocco","MAR","C","Gillette Stadium, Boston","2026-06-19T22:00:00Z",None,None,"SCHEDULED"),
    ("M031","Brazil","BRA","Haiti","HAI","C","Lincoln Financial Field, Philadelphia","2026-06-20T01:00:00Z",None,None,"SCHEDULED"),
    ("M032","Turkiye","TUR","Paraguay","PAR","D","Levi's Stadium, Santa Clara","2026-06-20T01:00:00Z",None,None,"SCHEDULED"),
]

def upsert(m):
    mid,home,hc,away,ac,grp,venue,ko,hs,as_,state = m
    payload = {
        "match_id": mid,
        "home_team": {"name": home, "code": hc, "fifa_rank": 0},
        "away_team": {"name": away, "code": ac, "fifa_rank": 0},
        "group": grp, "venue": venue, "kickoff_utc": ko, "state": state,
    }
    if hs is not None:
        payload["home_score"] = hs
        payload["away_score"] = as_

    try:
        r = requests.post(f"{BASE}/admin/matches/add", json=payload, headers=H, timeout=20)
        if r.status_code == 200:
            score = f" {hs}-{as_}" if hs is not None else ""
            print(f"  ✅ {mid}: {home} vs {away}{score} [{state}]")
            return True

        # Already exists — update score
        if hs is not None:
            r2 = requests.post(f"{BASE}/admin/matches/update-score",
                json={"match_id": mid, "home_score": hs, "away_score": as_, "state": state},
                headers=H, timeout=20)
            if r2.status_code == 200:
                print(f"  🔄 {mid}: {home} {hs}-{as_} {away} [{state}]")
                return True
            print(f"  ⚠️  {mid} update failed: {r2.status_code} {r2.text[:80]}")
            return False

        print(f"  ℹ️  {mid} already exists, no score to update")
        return True

    except requests.exceptions.ConnectionError:
        print(f"  ❌ {mid}: Cannot reach {BASE}")
        return False
    except Exception as e:
        print(f"  ❌ {mid}: {e}")
        return False

def main():
    print(f"\n{'='*55}")
    print(f"  DELTA SEEDER | {BASE}")
    print(f"{'='*55}\n")

    try:
        r = requests.get(f"{BASE}/health", timeout=15)
        print(f"✅ Backend: {r.json()}\n")
    except Exception as e:
        print(f"❌ Backend unreachable: {e}")
        print("   Wait for Render to finish deploying, then re-run.")
        sys.exit(1)

    ok = err = 0
    for m in MATCHES:
        if upsert(m): ok += 1
        else: err += 1

    print(f"\n{'='*55}")
    print(f"  Done: ✅ {ok} seeded  ❌ {err} failed")
    print(f"{'='*55}")
    if err == 0:
        print("\n🏆 Scores live. Check your app now.")
    else:
        print(f"\n⚠️  {err} failed. Re-run to retry.")

if __name__ == "__main__":
    main()