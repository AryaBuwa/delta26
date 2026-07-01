"""
fixtures.py — Match URL Mappings + Source Group Rotation
REBUILT June 30, 2026 — full sequential renumbering.

ROOT CAUSE FIXED: the previous file assembled fixtures across many sessions
in non-chronological order, leaving gaps (M069, M070 never existed) and
mid-file jumps (M053-M054 appeared before M049-M052, etc). Match IDs were
NOT sorted by kickoff time, so "next ID after group stage" was ambiguous —
this is what caused predictions/briefs/debriefs to silently fail for
knockout matches: schedule_match() looked up an ID that pointed to the
wrong fixture or didn't exist as expected.

FIX: every fixture below is numbered 1-72 in TRUE kickoff_utc order, no
gaps, no exceptions. Knockout (R32 onward) continues cleanly from M073.
This file is now the single source of truth for match numbering — the
database should match these IDs exactly going forward.

Group E: Germany, Ivory Coast, Ecuador, Curacao
Group F: Netherlands, Japan, Sweden, Tunisia
"""

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

# ─────────────────────────────────────────────
# 18 confirmed sources in 6 groups of 3
# ─────────────────────────────────────────────

SOURCE_GROUPS: dict[str, list[str]] = {
    "G1": ["fifa",      "bbc",       "espn"],
    "G2": ["sofascore", "flash",     "fotmob"],
    "G3": ["reuters",   "ap_news",   "cbs"],
    "G4": ["guardian",  "sky",       "goal"],
    "G5": ["whoscored", "livescore", "as_english"],
    "G6": ["sportstar", "ie_sports", "ndtv"],
}

ALL_SOURCES: list[str] = [s for g in SOURCE_GROUPS.values() for s in g]

SOURCE_NAMES: dict[str, str] = {
    "fifa":       "FIFA.com",
    "bbc":        "BBC Sport",
    "espn":       "ESPN FC",
    "sofascore":  "Sofascore",
    "flash":      "FlashScore",
    "fotmob":     "FotMob",
    "reuters":    "Reuters Sports",
    "ap_news":    "AP News Sports",
    "cbs":        "CBS Sports",
    "guardian":   "The Guardian",
    "sky":        "Sky Sports",
    "goal":       "Goal.com",
    "whoscored":  "WhoScored",
    "livescore":  "LiveScore",
    "as_english": "AS English",
    "sportstar":  "Sportstar",
    "ie_sports":  "Indian Express Sports",
    "ndtv":       "NDTV Sports",
}

SOURCE_BASE_URLS: dict[str, str] = {
    "fifa":       "https://www.fifa.com/fifaplus/en/match-centre",
    "bbc":        "https://www.bbc.com/sport/football",
    "espn":       "https://www.espn.com/soccer/match",
    "sofascore":  "https://www.sofascore.com",
    "flash":      "https://www.flashscore.com",
    "fotmob":     "https://www.fotmob.com",
    "reuters":    "https://www.reuters.com/sports/soccer",
    "ap_news":    "https://apnews.com/sports/soccer",
    "cbs":        "https://www.cbssports.com/soccer",
    "guardian":   "https://www.theguardian.com/football",
    "sky":        "https://www.skysports.com/football",
    "goal":       "https://www.goal.com",
    "whoscored":  "https://www.whoscored.com",
    "livescore":  "https://www.livescore.com",
    "as_english": "https://en.as.com/soccer",
    "sportstar":  "https://sportstar.thehindu.com/football",
    "ie_sports":  "https://indianexpress.com/sports/football",
    "ndtv":       "https://sports.ndtv.com/football",
}

# ─────────────────────────────────────────────
# ALL 72 GROUP STAGE FIXTURES — strictly sequential by kickoff_utc
# ─────────────────────────────────────────────

FIXTURES: list[dict] = [
    {"match_id":"WC2026_M001","home":"Mexico","away":"South Africa","kickoff_utc":"2026-06-11T19:00:00Z","venue":"Mexico City Stadium","phase":"group","group":"A","home_score":2,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M002","home":"South Korea","away":"Czechia","kickoff_utc":"2026-06-12T03:00:00Z","venue":"Guadalajara Stadium","phase":"group","group":"A","home_score":2,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M003","home":"Canada","away":"Bosnia and Herzegovina","kickoff_utc":"2026-06-12T20:00:00Z","venue":"BMO Field, Toronto","phase":"group","group":"B","home_score":1,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M004","home":"United States","away":"Paraguay","kickoff_utc":"2026-06-13T02:00:00Z","venue":"SoFi Stadium, Los Angeles","phase":"group","group":"D","home_score":4,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M005","home":"Qatar","away":"Switzerland","kickoff_utc":"2026-06-13T19:00:00Z","venue":"Levi's Stadium, San Francisco","phase":"group","group":"B","home_score":1,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M006","home":"Brazil","away":"Morocco","kickoff_utc":"2026-06-13T22:00:00Z","venue":"MetLife Stadium, New Jersey","phase":"group","group":"C","home_score":1,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M007","home":"Haiti","away":"Scotland","kickoff_utc":"2026-06-14T01:00:00Z","venue":"Gillette Stadium, Boston","phase":"group","group":"C","home_score":0,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M008","home":"Australia","away":"Turkiye","kickoff_utc":"2026-06-14T01:00:00Z","venue":"Hard Rock Stadium, Miami","phase":"group","group":"D","home_score":2,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M009","home":"Germany","away":"Curacao","kickoff_utc":"2026-06-14T17:00:00Z","venue":"MetLife Stadium, New Jersey","phase":"group","group":"E","home_score":7,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M010","home":"Netherlands","away":"Japan","kickoff_utc":"2026-06-14T20:00:00Z","venue":"AT&T Stadium, Dallas","phase":"group","group":"F","home_score":2,"away_score":2,"state":"FINISHED"},
    {"match_id":"WC2026_M011","home":"Ivory Coast","away":"Ecuador","kickoff_utc":"2026-06-14T23:00:00Z","venue":"Arrowhead Stadium, Kansas City","phase":"group","group":"E","home_score":1,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M012","home":"Sweden","away":"Tunisia","kickoff_utc":"2026-06-15T01:00:00Z","venue":"Estadio BBVA, Monterrey","phase":"group","group":"F","home_score":5,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M013","home":"Spain","away":"Cape Verde","kickoff_utc":"2026-06-15T16:00:00Z","venue":"SoFi Stadium, Los Angeles","phase":"group","group":"H","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M014","home":"Belgium","away":"Egypt","kickoff_utc":"2026-06-15T19:00:00Z","venue":"Lincoln Financial Field, Philadelphia","phase":"group","group":"G","home_score":1,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M015","home":"Saudi Arabia","away":"Uruguay","kickoff_utc":"2026-06-15T22:00:00Z","venue":"Camping World Stadium, Orlando","phase":"group","group":"H","home_score":1,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M016","home":"Iran","away":"New Zealand","kickoff_utc":"2026-06-16T01:00:00Z","venue":"BC Place, Vancouver","phase":"group","group":"G","home_score":2,"away_score":2,"state":"FINISHED"},
    {"match_id":"WC2026_M017","home":"France","away":"Senegal","kickoff_utc":"2026-06-16T19:00:00Z","venue":"Mercedes-Benz Stadium, Atlanta","phase":"group","group":"I","home_score":3,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M018","home":"Iraq","away":"Norway","kickoff_utc":"2026-06-16T22:00:00Z","venue":"Empower Field, Denver","phase":"group","group":"I","home_score":1,"away_score":4,"state":"FINISHED"},
    {"match_id":"WC2026_M019","home":"Argentina","away":"Algeria","kickoff_utc":"2026-06-17T01:00:00Z","venue":"MetLife Stadium, New Jersey","phase":"group","group":"J","home_score":3,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M020","home":"Austria","away":"Jordan","kickoff_utc":"2026-06-17T04:00:00Z","venue":"Hard Rock Stadium, Miami","phase":"group","group":"J","home_score":3,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M021","home":"Portugal","away":"DR Congo","kickoff_utc":"2026-06-17T17:00:00Z","venue":"Levi's Stadium, San Francisco","phase":"group","group":"K","home_score":1,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M022","home":"England","away":"Croatia","kickoff_utc":"2026-06-17T20:00:00Z","venue":"AT&T Stadium, Dallas","phase":"group","group":"L","home_score":4,"away_score":2,"state":"FINISHED"},
    {"match_id":"WC2026_M023","home":"Ghana","away":"Panama","kickoff_utc":"2026-06-17T23:00:00Z","venue":"Mexico City Stadium","phase":"group","group":"L","home_score":1,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M024","home":"Uzbekistan","away":"Colombia","kickoff_utc":"2026-06-18T02:00:00Z","venue":"Arrowhead Stadium, Kansas City","phase":"group","group":"K","home_score":1,"away_score":3,"state":"FINISHED"},
    {"match_id":"WC2026_M025","home":"Czechia","away":"South Africa","kickoff_utc":"2026-06-18T16:00:00Z","venue":"Mercedes-Benz Stadium, Atlanta","phase":"group","group":"A","home_score":1,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M026","home":"Switzerland","away":"Bosnia and Herzegovina","kickoff_utc":"2026-06-18T19:00:00Z","venue":"SoFi Stadium, Los Angeles","phase":"group","group":"B","home_score":4,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M027","home":"Canada","away":"Qatar","kickoff_utc":"2026-06-18T22:00:00Z","venue":"BC Place, Vancouver","phase":"group","group":"B","home_score":6,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M028","home":"Mexico","away":"South Korea","kickoff_utc":"2026-06-19T02:00:00Z","venue":"Guadalajara Stadium","phase":"group","group":"A","home_score":1,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M029","home":"United States","away":"Australia","kickoff_utc":"2026-06-19T19:00:00Z","venue":"Lumen Field, Seattle","phase":"group","group":"D","home_score":2,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M030","home":"Scotland","away":"Brazil","kickoff_utc":"2026-06-19T22:00:00Z","venue":"Rose Bowl, Los Angeles","phase":"group","group":"C","home_score":0,"away_score":3,"state":"FINISHED"},
    {"match_id":"WC2026_M031","home":"Morocco","away":"Haiti","kickoff_utc":"2026-06-20T01:00:00Z","venue":"Gillette Stadium, Boston","phase":"group","group":"C","home_score":1,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M032","home":"Turkiye","away":"Paraguay","kickoff_utc":"2026-06-20T02:00:00Z","venue":"Levi's Stadium, Santa Clara","phase":"group","group":"D","home_score":0,"away_score":1,"state":"FINISHED"},
    {"match_id":"WC2026_M033","home":"Netherlands","away":"Sweden","kickoff_utc":"2026-06-20T17:00:00Z","venue":"NRG Stadium, Houston","phase":"group","group":"F","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M034","home":"Germany","away":"Ivory Coast","kickoff_utc":"2026-06-20T20:00:00Z","venue":"BMO Field, Toronto","phase":"group","group":"E","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M035","home":"Ecuador","away":"Curacao","kickoff_utc":"2026-06-21T01:00:00Z","venue":"Arrowhead Stadium, Kansas City","phase":"group","group":"E","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M036","home":"Tunisia","away":"Japan","kickoff_utc":"2026-06-21T04:00:00Z","venue":"Estadio BBVA, Monterrey","phase":"group","group":"F","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M037","home":"Spain","away":"Saudi Arabia","kickoff_utc":"2026-06-21T16:00:00Z","venue":"Mercedes-Benz Stadium, Atlanta","phase":"group","group":"H","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M038","home":"Belgium","away":"Iran","kickoff_utc":"2026-06-21T19:00:00Z","venue":"SoFi Stadium, Los Angeles","phase":"group","group":"G","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M039","home":"Uruguay","away":"Cape Verde","kickoff_utc":"2026-06-21T22:00:00Z","venue":"Hard Rock Stadium, Miami","phase":"group","group":"H","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M040","home":"New Zealand","away":"Egypt","kickoff_utc":"2026-06-22T01:00:00Z","venue":"BC Place, Vancouver","phase":"group","group":"G","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M041","home":"Argentina","away":"Austria","kickoff_utc":"2026-06-22T17:00:00Z","venue":"Mercedes-Benz Stadium, Atlanta","phase":"group","group":"J","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M042","home":"France","away":"Iraq","kickoff_utc":"2026-06-22T21:00:00Z","venue":"Lincoln Financial Field, Philadelphia","phase":"group","group":"I","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M043","home":"Norway","away":"Senegal","kickoff_utc":"2026-06-23T00:00:00Z","venue":"MetLife Stadium, New Jersey","phase":"group","group":"I","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M044","home":"Jordan","away":"Algeria","kickoff_utc":"2026-06-23T03:00:00Z","venue":"Levi's Stadium, San Francisco","phase":"group","group":"J","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M045","home":"Portugal","away":"Uzbekistan","kickoff_utc":"2026-06-23T17:00:00Z","venue":"AT&T Stadium, Dallas","phase":"group","group":"K","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M046","home":"England","away":"Ghana","kickoff_utc":"2026-06-23T20:00:00Z","venue":"Empower Field, Denver","phase":"group","group":"L","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M047","home":"Panama","away":"Croatia","kickoff_utc":"2026-06-23T23:00:00Z","venue":"Hard Rock Stadium, Miami","phase":"group","group":"L","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M048","home":"Colombia","away":"DR Congo","kickoff_utc":"2026-06-24T02:00:00Z","venue":"Rose Bowl, Los Angeles","phase":"group","group":"K","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M049","home":"Switzerland","away":"Canada","kickoff_utc":"2026-06-24T19:00:00Z","venue":"BC Place, Vancouver","phase":"group","group":"B","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M050","home":"Bosnia and Herzegovina","away":"Qatar","kickoff_utc":"2026-06-24T19:00:00Z","venue":"Lincoln Financial Field, Philadelphia","phase":"group","group":"B","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M051","home":"Scotland","away":"Brazil","kickoff_utc":"2026-06-24T22:00:00Z","venue":"Hard Rock Stadium, Miami","phase":"group","group":"C","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M052","home":"Morocco","away":"Haiti","kickoff_utc":"2026-06-24T22:00:00Z","venue":"Mercedes-Benz Stadium, Atlanta","phase":"group","group":"C","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M053","home":"Czechia","away":"Mexico","kickoff_utc":"2026-06-25T01:00:00Z","venue":"Mexico City Stadium","phase":"group","group":"A","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M054","home":"South Africa","away":"South Korea","kickoff_utc":"2026-06-25T01:00:00Z","venue":"Guadalajara Stadium","phase":"group","group":"A","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M055","home":"Ecuador","away":"Germany","kickoff_utc":"2026-06-25T20:00:00Z","venue":"MetLife Stadium, New Jersey","phase":"group","group":"E","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M056","home":"Curacao","away":"Ivory Coast","kickoff_utc":"2026-06-25T20:00:00Z","venue":"Lincoln Financial Field, Philadelphia","phase":"group","group":"E","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M057","home":"Japan","away":"Sweden","kickoff_utc":"2026-06-25T23:00:00Z","venue":"AT&T Stadium, Dallas","phase":"group","group":"F","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M058","home":"Tunisia","away":"Netherlands","kickoff_utc":"2026-06-25T23:00:00Z","venue":"Arrowhead Stadium, Kansas City","phase":"group","group":"F","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M059","home":"Turkiye","away":"United States","kickoff_utc":"2026-06-26T02:00:00Z","venue":"SoFi Stadium, Los Angeles","phase":"group","group":"D","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M060","home":"Paraguay","away":"Australia","kickoff_utc":"2026-06-26T02:00:00Z","venue":"Levi's Stadium, Santa Clara","phase":"group","group":"D","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M061","home":"Norway","away":"France","kickoff_utc":"2026-06-26T19:00:00Z","venue":"Gillette Stadium, Boston","phase":"group","group":"I","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M062","home":"Senegal","away":"Iraq","kickoff_utc":"2026-06-26T19:00:00Z","venue":"BMO Field, Toronto","phase":"group","group":"I","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M063","home":"Algeria","away":"Argentina","kickoff_utc":"2026-06-26T22:00:00Z","venue":"Mercedes-Benz Stadium, Atlanta","phase":"group","group":"J","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M064","home":"Austria","away":"Jordan","kickoff_utc":"2026-06-26T22:00:00Z","venue":"Gillette Stadium, Boston","phase":"group","group":"J","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M065","home":"Egypt","away":"Iran","kickoff_utc":"2026-06-27T03:00:00Z","venue":"Lumen Field, Seattle","phase":"group","group":"G","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M066","home":"New Zealand","away":"Belgium","kickoff_utc":"2026-06-27T03:00:00Z","venue":"BC Place, Vancouver","phase":"group","group":"G","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M067","home":"Uruguay","away":"Spain","kickoff_utc":"2026-06-27T19:00:00Z","venue":"Guadalajara Stadium","phase":"group","group":"H","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M068","home":"Cape Verde","away":"Saudi Arabia","kickoff_utc":"2026-06-27T19:00:00Z","venue":"NRG Stadium, Houston","phase":"group","group":"H","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M069","home":"DR Congo","away":"Uzbekistan","kickoff_utc":"2026-06-28T01:00:00Z","venue":"Mercedes-Benz Stadium, Atlanta","phase":"group","group":"K","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M070","home":"Colombia","away":"Portugal","kickoff_utc":"2026-06-28T01:00:00Z","venue":"Rose Bowl, Los Angeles","phase":"group","group":"K","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M071","home":"Croatia","away":"England","kickoff_utc":"2026-06-28T19:00:00Z","venue":"AT&T Stadium, Dallas","phase":"group","group":"L","home_score":0,"away_score":0,"state":"FINISHED"},
    {"match_id":"WC2026_M072","home":"Panama","away":"Ghana","kickoff_utc":"2026-06-28T19:00:00Z","venue":"Arrowhead Stadium, Kansas City","phase":"group","group":"L","home_score":0,"away_score":0,"state":"FINISHED"},

    # ── KNOCKOUT — Round of 32 (M073–M088, 16 matches) ──────────────────────
    {"match_id":"WC2026_M073","home":"TBD","away":"TBD","kickoff_utc":"2026-06-28T19:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M074","home":"TBD","away":"TBD","kickoff_utc":"2026-06-28T22:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M075","home":"South Africa","away":"Canada","kickoff_utc":"2026-06-29T17:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M076","home":"Brazil","away":"Japan","kickoff_utc":"2026-06-29T20:30:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M077","home":"Germany","away":"Paraguay","kickoff_utc":"2026-06-30T01:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M078","home":"Netherlands","away":"Morocco","kickoff_utc":"2026-06-30T17:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M079","home":"Ivory Coast","away":"Norway","kickoff_utc":"2026-06-30T21:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M080","home":"France","away":"Sweden","kickoff_utc":"2026-07-01T01:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M081","home":"Mexico","away":"Ecuador","kickoff_utc":"2026-07-01T16:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M082","home":"England","away":"DR Congo","kickoff_utc":"2026-07-01T20:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M083","home":"Belgium","away":"Senegal","kickoff_utc":"2026-07-02T00:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M084","home":"USA","away":"Bosnia and Herzegovina","kickoff_utc":"2026-07-02T19:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M085","home":"Spain","away":"Austria","kickoff_utc":"2026-07-02T23:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M086","home":"Portugal","away":"Croatia","kickoff_utc":"2026-07-03T03:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M087","home":"Switzerland","away":"Algeria","kickoff_utc":"2026-07-03T18:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M088","home":"Argentina","away":"Cape Verde","kickoff_utc":"2026-07-03T22:00:00Z","venue":"TBD","phase":"r32","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},

    # ── KNOCKOUT — Round of 16 (M089–M096, 8 matches) ────────────────────
    {"match_id":"WC2026_M089","home":"TBD","away":"TBD","kickoff_utc":"2026-07-09T19:00:00Z","venue":"TBD","phase":"r16","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M090","home":"TBD","away":"TBD","kickoff_utc":"2026-07-09T22:00:00Z","venue":"TBD","phase":"r16","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M091","home":"TBD","away":"TBD","kickoff_utc":"2026-07-10T19:00:00Z","venue":"TBD","phase":"r16","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M092","home":"TBD","away":"TBD","kickoff_utc":"2026-07-10T22:00:00Z","venue":"TBD","phase":"r16","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M093","home":"TBD","away":"TBD","kickoff_utc":"2026-07-11T19:00:00Z","venue":"TBD","phase":"r16","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M094","home":"TBD","away":"TBD","kickoff_utc":"2026-07-11T22:00:00Z","venue":"TBD","phase":"r16","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M095","home":"TBD","away":"TBD","kickoff_utc":"2026-07-12T19:00:00Z","venue":"TBD","phase":"r16","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M096","home":"TBD","away":"TBD","kickoff_utc":"2026-07-12T22:00:00Z","venue":"TBD","phase":"r16","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},

    # ── KNOCKOUT — Quarter-Finals (M097–M100, 4 matches) ─────────────────
    {"match_id":"WC2026_M097","home":"TBD","away":"TBD","kickoff_utc":"2026-07-14T19:00:00Z","venue":"TBD","phase":"qf","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M098","home":"TBD","away":"TBD","kickoff_utc":"2026-07-14T22:00:00Z","venue":"TBD","phase":"qf","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M099","home":"TBD","away":"TBD","kickoff_utc":"2026-07-15T19:00:00Z","venue":"TBD","phase":"qf","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M100","home":"TBD","away":"TBD","kickoff_utc":"2026-07-15T22:00:00Z","venue":"TBD","phase":"qf","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},

    # ── Semi-Finals (M101–M102), Third Place (M103), Final (M104) ─────────
    {"match_id":"WC2026_M101","home":"TBD","away":"TBD","kickoff_utc":"2026-07-18T19:00:00Z","venue":"TBD","phase":"sf","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M102","home":"TBD","away":"TBD","kickoff_utc":"2026-07-18T22:00:00Z","venue":"TBD","phase":"sf","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M103","home":"TBD","away":"TBD","kickoff_utc":"2026-07-19T17:00:00Z","venue":"TBD","phase":"3rd","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
    {"match_id":"WC2026_M104","home":"TBD","away":"TBD","kickoff_utc":"2026-07-19T20:00:00Z","venue":"MetLife Stadium, New Jersey","phase":"final","group":None,"home_score":0,"away_score":0,"state":"SCHEDULED"},
]

FIXTURE_BY_ID: dict[str, dict] = {f["match_id"]: f for f in FIXTURES}

# ─────────────────────────────────────────────
# URL + GROUP ROTATION STATE
# ─────────────────────────────────────────────

_match_urls: dict[str, dict[str, str]] = {}
_match_group_assignments: dict[str, str] = {}
_match_group_history: dict[str, list[str]] = {}


def assign_groups_for_day(match_ids: list[str]) -> dict[str, str]:
    group_keys = list(SOURCE_GROUPS.keys())
    random.shuffle(group_keys)
    assignments: dict[str, str] = {}
    used_groups: set[str] = set()

    for match_id in match_ids[:6]:
        history = _match_group_history.get(match_id, [])
        last_used = history[-1] if history else None
        for g in group_keys:
            if g not in used_groups and g != last_used:
                assignments[match_id] = g
                used_groups.add(g)
                _match_group_history.setdefault(match_id, []).append(g)
                break
        else:
            for g in group_keys:
                if g not in used_groups:
                    assignments[match_id] = g
                    used_groups.add(g)
                    break

    _match_group_assignments.update(assignments)
    _persist_state()
    return assignments


def swap_group(match_id: str) -> str:
    current = _match_group_assignments.get(match_id)
    active_groups = set(_match_group_assignments.values())
    history = _match_group_history.get(match_id, [])
    for g in SOURCE_GROUPS.keys():
        if g != current and g not in active_groups and g not in history[-2:]:
            _match_group_assignments[match_id] = g
            _match_group_history.setdefault(match_id, []).append(g)
            _persist_state()
            return g
    for g in SOURCE_GROUPS.keys():
        if g != current:
            _match_group_assignments[match_id] = g
            _persist_state()
            return g
    return current


def get_sources_for_match(match_id: str) -> list[str]:
    group_key = _match_group_assignments.get(match_id)
    if not group_key:
        return SOURCE_GROUPS["G1"]
    return SOURCE_GROUPS[group_key]


def get_url(match_id: str, source: str) -> Optional[str]:
    return _match_urls.get(match_id, {}).get(source)


def paste_match_urls(match_id: str, urls: dict[str, str]) -> None:
    if match_id not in FIXTURE_BY_ID:
        return
    _match_urls.setdefault(match_id, {}).update(urls)
    _persist_state()


def get_match_info(match_id: str) -> Optional[dict]:
    return FIXTURE_BY_ID.get(match_id)


def get_todays_matches() -> list[dict]:
    today = datetime.now(timezone.utc).date()
    return sorted(
        [f for f in FIXTURES if _ko_date(f) == today],
        key=lambda x: x["kickoff_utc"]
    )


def get_upcoming_matches(hours_ahead: int = 24) -> list[dict]:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours_ahead)
    return sorted(
        [f for f in FIXTURES if now <= _ko_dt(f) <= cutoff],
        key=lambda x: x["kickoff_utc"]
    )


def get_finished_matches() -> list[dict]:
    return [f for f in FIXTURES if f.get("state") == "FINISHED"]


def update_fixture_teams(match_id: str, home: str, away: str) -> None:
    if match_id in FIXTURE_BY_ID:
        FIXTURE_BY_ID[match_id]["home"] = home
        FIXTURE_BY_ID[match_id]["away"] = away
        _persist_state()


def update_fixture_result(match_id: str, home_score: int, away_score: int, state: str = "FINISHED") -> None:
    if match_id in FIXTURE_BY_ID:
        FIXTURE_BY_ID[match_id]["home_score"] = home_score
        FIXTURE_BY_ID[match_id]["away_score"] = away_score
        FIXTURE_BY_ID[match_id]["state"] = state
        _persist_state()


def _ko_dt(fixture: dict) -> datetime:
    try:
        return datetime.fromisoformat(fixture["kickoff_utc"].replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _ko_date(fixture: dict):
    return _ko_dt(fixture).date()


_STATE_FILE = Path("fixtures_state.json")


def _persist_state() -> None:
    state = {
        "match_urls": _match_urls,
        "group_assignments": _match_group_assignments,
        "group_history": _match_group_history,
        "fixture_overrides": {
            mid: {
                "home": f["home"], "away": f["away"],
                "home_score": f.get("home_score", 0),
                "away_score": f.get("away_score", 0),
                "state": f.get("state", "SCHEDULED"),
            }
            for mid, f in FIXTURE_BY_ID.items()
        },
    }
    try:
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.warning(f"Failed to persist fixtures state: {e}")


def load_state() -> None:
    global _match_urls, _match_group_assignments
    if not _STATE_FILE.exists():
        return
    try:
        state = json.loads(_STATE_FILE.read_text())
        _match_urls.update(state.get("match_urls", {}))
        _match_group_assignments.update(state.get("group_assignments", {}))
        _match_group_history.update(state.get("group_history", {}))
        for mid, data in state.get("fixture_overrides", {}).items():
            if mid in FIXTURE_BY_ID:
                FIXTURE_BY_ID[mid].update(data)
    except Exception as e:
        logger.error(f"Failed to load fixtures state: {e}")


def build_tavily_query(match_id: str, source: str) -> str:
    fixture = FIXTURE_BY_ID.get(match_id, {})
    home = fixture.get("home", "Team A")
    away = fixture.get("away", "Team B")
    source_name = SOURCE_NAMES.get(source, source)
    return f"{home} vs {away} live World Cup 2026 {source_name}"


def safe_to_deploy() -> tuple[bool, str]:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    for f in get_todays_matches():
        ko = _ko_dt(f)
        end_est = ko + timedelta(hours=2, minutes=30)
        if ko <= now <= end_est:
            remaining = int((end_est - now).total_seconds() / 60)
            return False, f"Deploy after ~{remaining} min. {f['home']} vs {f['away']} still live."
    upcoming = get_upcoming_matches(hours_ahead=2)
    if upcoming:
        f = upcoming[0]
        mins = int((_ko_dt(f) - now).total_seconds() / 60)
        if mins < 30:
            return False, f"Wait. {f['home']} vs {f['away']} kicks off in {mins} min."
    next_matches = get_upcoming_matches(hours_ahead=48)
    if next_matches:
        next_ko = _ko_dt(next_matches[0])
        gap = next_ko - now
        h = int(gap.total_seconds() / 3600)
        m = int((gap.total_seconds() % 3600) / 60)
        return True, f"Safe. No matches for {h}h {m}m."
    return True, "No upcoming matches found."


PHASE_RETRAIN_THRESHOLD: dict[str, float] = {
    "group": 0.02, "r32": 0.05, "r16": 0.05,
    "qf": 0.08, "sf": 0.08, "3rd": 0.08, "final": 0.08,
}


def get_retrain_threshold(match_id: str) -> float:
    phase = FIXTURE_BY_ID.get(match_id, {}).get("phase", "group")
    return PHASE_RETRAIN_THRESHOLD.get(phase, 0.02)


def get_phase_label(match_id: str) -> str:
    labels = {
        "group": "Group Stage", "r32": "Round of 32", "r16": "Round of 16",
        "qf": "Quarter-Final", "sf": "Semi-Final", "3rd": "Third Place", "final": "Final",
    }
    phase = FIXTURE_BY_ID.get(match_id, {}).get("phase", "group")
    return labels.get(phase, phase.upper())