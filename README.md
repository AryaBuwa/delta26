# DEΔTA

**An AI that experiences the World Cup in real time.**

> Who predicts better — a machine that learns from every match, or humans watching the same game?

Currently being built during FIFA World Cup 2026. App is in active development.

---

## What is this?

DEΔTA is a live prediction system running across all 104 FIFA World Cup 2026 matches. After every match, the AI model retrains on the new result. Humans vote on each match before and during the game. The gap between the two — who's right, who's wrong, and when — is the core question.

---

## How it works

```
18 live sources → async parallel fetch → Groq LLM parsing → prediction model → SSE stream → browser
                                                                      ↑
                                                              retrains after every match
```

**Data layer** — 18 sources split into 6 rotating groups. Each match gets its own group. Async parallel fetch with bot prevention, rate limiting, and automatic fallback to Tavily search if sources block.

**Parsing layer** — Raw HTML commentary fed to Groq (llama-3.1-8b) for structured JSON extraction. Pydantic v2 schema validation. Hallucination guards.

**Prediction model** — Dixon-Coles statistical model + Monte Carlo simulation (10,000 runs) + XGBoost with contextual features. Retrains after every match.

**Voting** — One vote per match per browser fingerprint. Voting locks hard at 85 minutes.

**Live updates** — Server-Sent Events per match. Only changed components re-render.

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind CSS |
| Backend | FastAPI, Python 3.11, SQLAlchemy |
| Database | PostgreSQL (Render) + Google Sheets archive |
| LLM | Groq — llama-3.1-8b (parsing) + llama-3.3-70b (analysis) |
| ML | XGBoost + Dixon-Coles + Monte Carlo |
| MLOps | MLflow + DVC *(upcoming)* |
| Infra | Vercel (frontend) + Render (backend) |
| Search fallback | Tavily + Linkup |
| Bot protection | Cloudflare Turnstile + reCAPTCHA v3 *(upcoming)* |
| Alerts | Telegram bot |

**Total infrastructure cost: $0/month**

---

## Project structure

```
delta/
├── backend/
│   ├── main.py          # FastAPI — all routes, SSE, admin
│   ├── fetcher.py       # Async parallel 18-source fetch
│   ├── parser.py        # Groq LLM parsing + validation
│   ├── model.py         # Dixon-Coles + Monte Carlo + XGBoost
│   ├── pipeline.py      # Orchestrator + state machine
│   ├── alerts.py        # Telegram bot — 3-tier alerting
│   ├── sheets.py        # Google Sheets archive
│   ├── fixtures.py      # All 104 match definitions
│   └── health_check.py  # Pre-deploy verification
└── frontend/
    ├── src/app/         # Next.js App Router pages
    ├── src/components/  # Match cards, voting, live score
    ├── src/hooks/       # SSE connection, fingerprinting
    └── src/lib/         # API client, Zustand store
```

---

## Build status

| Component | Status |
|---|---|
| Backend API | ✅ Live |
| Frontend | ✅ Deployed |
| Database | ✅ Connected |
| Match cards | 🔄 In progress |
| Live data pipeline | 🔄 In progress |
| ML model | 🔄 In progress |
| Voting system | 🔄 In progress |

Tournament: June 11 – July 19, 2026 · 104 matches · 48 teams

---

## Disclaimer

Independent project. Not affiliated with FIFA or UEFA. No gambling, no prizes, no real money. All match data from public sources.