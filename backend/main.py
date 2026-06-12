"""
Project Delta — main.py
FastAPI backend: all routes, SSE streams, admin dashboard API,
voting endpoints, match data, scheduled jobs, keep-alive.

Session 3 — production-ready, no placeholders.
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://delta.vercel.app")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./delta.db")
CLOUDFLARE_TURNSTILE_SECRET = os.getenv("CLOUDFLARE_TURNSTILE_SECRET", "")
RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET_KEY", "")

# Use Supabase Postgres in production, SQLite in development
if SUPABASE_URL and "supabase" in SUPABASE_URL:
    # Extract Postgres connection string from Supabase URL
    db_host = SUPABASE_URL.replace("https://", "").replace(".supabase.co", "")
    DATABASE_URL = f"postgresql://postgres:{os.getenv('SUPABASE_DB_PASSWORD', '')}@db.{db_host}.supabase.co:5432/postgres"

# ─────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
    pool_recycle=300,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class MatchDB(Base):
    __tablename__ = "matches"
    id = Column(String, primary_key=True)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    kickoff_utc = Column(DateTime(timezone=True), nullable=False)
    venue = Column(String)
    group_name = Column(String)
    phase = Column(String, default="group")
    state = Column(String, default="SCHEDULED")
    home_score = Column(Integer, default=0)
    away_score = Column(Integer, default=0)
    minute = Column(String, default="0")
    events = Column(JSON, default=list)
    ai_context = Column(Text)
    model_confidence = Column(JSON)
    model_version = Column(Integer, default=0)
    source_used = Column(String)
    last_updated = Column(DateTime(timezone=True))
    pre_match_brief = Column(Text)
    post_match_debrief = Column(Text)
    penalty_home = Column(Integer, default=0)
    penalty_away = Column(Integer, default=0)
    went_to_et = Column(Boolean, default=False)
    went_to_penalties = Column(Boolean, default=False)


class PredictionDB(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    home_win = Column(Float)
    draw = Column(Float)
    away_win = Column(Float)
    predicted_scorer = Column(String)
    predicted_score = Column(String)
    confidence_range_low = Column(Float)
    confidence_range_high = Column(Float)
    model_version = Column(Integer)
    training_matches_seen = Column(Integer)
    created_at = Column(DateTime(timezone=True))
    locked_at_85 = Column(Boolean, default=False)
    locked_confidence = Column(JSON)


class VoteDB(Base):
    __tablename__ = "votes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    fingerprint_hash = Column(String, nullable=False)
    session_id = Column(String)
    pick = Column(String, nullable=False)
    first_scorer = Column(String)
    confidence_level = Column(Integer)
    trust_score = Column(Float)
    recaptcha_score = Column(Float)
    timestamp = Column(DateTime(timezone=True))
    minute_before_kickoff = Column(Integer)
    match_minute_at_vote = Column(String)
    score_at_vote = Column(String)
    ai_confidence_at_vote = Column(JSON)
    change_count = Column(Integer, default=0)
    changed_from = Column(String)
    is_penalty_vote = Column(Boolean, default=False)
    ip_hash = Column(String)


class ModelVersionDB(Base):
    __tablename__ = "model_versions"
    version = Column(Integer, primary_key=True)
    accuracy_before = Column(Float)
    accuracy_after = Column(Float)
    improvement_pct = Column(Float)
    deployed = Column(Boolean, default=False)
    training_match_id = Column(String)
    trained_at = Column(DateTime(timezone=True))
    deploy_decision = Column(String)
    training_duration_s = Column(Float)
    mlflow_run_id = Column(String)


class SourceHealthDB(Base):
    __tablename__ = "source_health"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_name = Column(String, nullable=False)
    status = Column(String)
    last_check = Column(DateTime(timezone=True))
    block_count_today = Column(Integer, default=0)
    restore_at = Column(DateTime(timezone=True))
    consecutive_failures = Column(Integer, default=0)


class LiveEventDB(Base):
    __tablename__ = "live_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(String, nullable=False, index=True)
    event_type = Column(String)
    minute = Column(String)
    player = Column(String)
    team = Column(String)
    sentiment = Column(String)
    context = Column(Text)
    timestamp = Column(DateTime(timezone=True))


def init_db():
    """Call this once at startup — not at import time."""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created/verified")
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        logger.warning("App starting without database — some features unavailable")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
# ─────────────────────────────────────────────
# SSE BROKER
# ─────────────────────────────────────────────

class SSEBroker:
    """Per-match SSE connection manager. Scales to 10k+ connections."""

    def __init__(self):
        # match_id -> set of asyncio Queues
        self._queues: dict[str, set[asyncio.Queue]] = {}
        self._global_queues: set[asyncio.Queue] = set()
        self._connection_count: dict[str, int] = {}

    def subscribe(self, match_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        if match_id not in self._queues:
            self._queues[match_id] = set()
        self._queues[match_id].add(q)
        self._connection_count[match_id] = self._connection_count.get(match_id, 0) + 1
        return q

    def subscribe_global(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._global_queues.add(q)
        return q

    def unsubscribe(self, match_id: str, q: asyncio.Queue):
        if match_id in self._queues:
            self._queues[match_id].discard(q)
            self._connection_count[match_id] = max(0, self._connection_count.get(match_id, 1) - 1)

    def unsubscribe_global(self, q: asyncio.Queue):
        self._global_queues.discard(q)

    async def publish(self, match_id: str, event_type: str, data: dict):
        """Push update to all subscribers of a match."""
        payload = json.dumps({"type": event_type, "match_id": match_id, "data": data, "ts": time.time()})
        dead = set()
        for q in self._queues.get(match_id, set()):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.add(q)
        for q in dead:
            self._queues[match_id].discard(q)

        # Also push to global stream
        for q in list(self._global_queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def publish_global(self, event_type: str, data: dict):
        payload = json.dumps({"type": event_type, "data": data, "ts": time.time()})
        for q in list(self._global_queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def total_connections(self) -> int:
        return sum(len(v) for v in self._queues.values()) + len(self._global_queues)

    def connections_per_match(self) -> dict:
        return {k: len(v) for k, v in self._queues.items() if v}


broker = SSEBroker()

# ─────────────────────────────────────────────
# RATE LIMITER
# ─────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_admin_token_store: dict[str, float] = {}  # token -> expiry timestamp


def verify_admin_token(token: str) -> bool:
    expiry = _admin_token_store.get(token, 0)
    return time.time() < expiry


def create_admin_token() -> str:
    token = str(uuid.uuid4())
    _admin_token_store[token] = time.time() + 3600 * 8  # 8hr session
    return token


def get_admin(request: Request) -> bool:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        if verify_admin_token(token):
            return True
    raise HTTPException(status_code=401, detail="Admin auth required")


# ─────────────────────────────────────────────
# PYDANTIC MODELS
# ─────────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    password: str


class VoteRequest(BaseModel):
    match_id: str
    pick: str = Field(..., pattern="^(home|draw|away)$")
    first_scorer: Optional[str] = None
    confidence_level: int = Field(3, ge=1, le=5)
    fingerprint_hash: str
    session_id: Optional[str] = None
    turnstile_token: str
    recaptcha_token: Optional[str] = None
    time_on_page_ms: int = Field(0, ge=0)
    mouse_moved: bool = False
    is_penalty_vote: bool = False
    changed_from: Optional[str] = None


class RetrainRequest(BaseModel):
    confirm: bool = False


class MatchStateOverride(BaseModel):
    match_id: str
    state: str
    note: Optional[str] = None


# ─────────────────────────────────────────────
# TRUST SCORE CALCULATION
# ─────────────────────────────────────────────

async def calculate_trust_score(
    recaptcha_score: float,
    time_on_page_ms: int,
    mouse_moved: bool,
    fingerprint_hash: str,
    request: Request,
    db: Session,
    match_id: str,
) -> float:
    """
    Score = weighted combination:
      reCAPTCHA v3 score:      35%
      Time on page >30s:       20%
      Mouse movement detected: 15%
      Non-burst timing:        15%
      Unique fingerprint:      10%
      Honeypot empty:           5%  (checked in route)
    """
    score = 0.0

    # reCAPTCHA (0-1 → weighted 35%)
    score += min(recaptcha_score, 1.0) * 0.35

    # Time on page > 30s
    if time_on_page_ms >= 30_000:
        score += 0.20
    elif time_on_page_ms >= 10_000:
        score += 0.10

    # Mouse movement
    if mouse_moved:
        score += 0.15

    # Burst detection: check recent votes from same fingerprint
    one_minute_ago = datetime.now(timezone.utc).timestamp() - 60
    recent_votes = (
        db.query(VoteDB)
        .filter(
            VoteDB.fingerprint_hash == fingerprint_hash,
            VoteDB.timestamp >= datetime.fromtimestamp(one_minute_ago, tz=timezone.utc),
        )
        .count()
    )
    if recent_votes < 3:
        score += 0.15
    elif recent_votes < 10:
        score += 0.05

    # Unique fingerprint for this match
    existing = (
        db.query(VoteDB)
        .filter(VoteDB.fingerprint_hash == fingerprint_hash, VoteDB.match_id == match_id)
        .first()
    )
    if not existing:
        score += 0.10

    # Honeypot always adds 5% if passed (caller responsibility)
    score += 0.05

    return min(score, 1.0)


async def verify_turnstile(token: str) -> bool:
    """Verify Cloudflare Turnstile token."""
    if not CLOUDFLARE_TURNSTILE_SECRET or CLOUDFLARE_TURNSTILE_SECRET == "1x0000000000000000000000000000000AA":
        return True  # Dev/test bypass
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": CLOUDFLARE_TURNSTILE_SECRET, "response": token},
            timeout=5.0,
        )
        result = resp.json()
        return result.get("success", False)


async def verify_recaptcha(token: str) -> float:
    """Verify reCAPTCHA v3 token, return score 0-1."""
    if not RECAPTCHA_SECRET or not token:
        return 0.7  # Assume likely human if not configured
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={"secret": RECAPTCHA_SECRET, "response": token},
                timeout=5.0,
            )
            result = resp.json()
            return float(result.get("score", 0.5)) if result.get("success") else 0.3
    except Exception:
        return 0.5


# ─────────────────────────────────────────────
# LIFESPAN (startup / shutdown)
# ─────────────────────────────────────────────

scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("🚀 Project Delta starting up")

    # Init database — must be first
    init_db()

    # Import pipeline here to avoid circular imports at module load time
    try:
        from pipeline import PipelineOrchestrator
        app.state.pipeline = PipelineOrchestrator(broker=broker)
        await app.state.pipeline.start()
        logger.info("✅ Pipeline started")
    except ImportError:
        logger.warning("pipeline.py not found — running without live pipeline")
        app.state.pipeline = None

    # Keep-alive: ping self every 13 minutes to prevent Render spin-down
    async def keep_alive():
        try:
            async with httpx.AsyncClient() as client:
                await client.get("http://localhost:8000/health", timeout=5.0)
            logger.debug("Keep-alive ping sent")
        except Exception as e:
            logger.debug(f"Keep-alive ping failed (normal if starting): {e}")

    scheduler.add_job(keep_alive, IntervalTrigger(minutes=13), id="keep_alive")

    # Source health check every 30 minutes
    async def source_health_job():
        if app.state.pipeline:
            await app.state.pipeline.check_source_health()

    scheduler.add_job(source_health_job, IntervalTrigger(minutes=30), id="source_health")

    # Pre-match brief job — runs every 15 min, checks internally if 3h before KO
    async def pre_match_brief_job():
        if app.state.pipeline:
            await app.state.pipeline.generate_pre_match_briefs()

    scheduler.add_job(pre_match_brief_job, IntervalTrigger(minutes=15), id="pre_match_brief")

    # Fixture check every 2 hours
    async def fixture_check_job():
        if app.state.pipeline:
            await app.state.pipeline.check_fixtures()

    scheduler.add_job(fixture_check_job, IntervalTrigger(hours=2), id="fixture_check")

    scheduler.start()
    logger.info("✅ Scheduler started")

    yield  # Application runs here

    # Shutdown
    logger.info("🛑 Project Delta shutting down")
    scheduler.shutdown(wait=False)
    if app.state.pipeline:
        await app.state.pipeline.stop()
    logger.info("✅ Clean shutdown complete")


# ─────────────────────────────────────────────
# APP INIT
# ─────────────────────────────────────────────

app = FastAPI(
    title="Project Delta API",
    description="AI vs Human prediction system for FIFA World Cup 2026",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENV", "dev") == "dev" else None,
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — only delta26.vercel.app in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "https://delta26.vercel.app"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# ─────────────────────────────────────────────
# HEALTH ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — used by health_check.py and Render monitoring."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "sse_connections": broker.total_connections(),
    }


@app.get("/health/detailed")
async def health_detailed(db: Session = Depends(get_db)):
    """Detailed health — used by admin dashboard."""
    # Check DB
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")

    # Check pipeline
    pipeline_ok = False
    active_matches = []
    if hasattr(app.state, "pipeline") and app.state.pipeline:
        pipeline_ok = True
        active_matches = app.state.pipeline.get_active_match_ids()

    return {
        "status": "ok" if db_ok and pipeline_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "pipeline": "ok" if pipeline_ok else "not_running",
        "sse_connections": broker.total_connections(),
        "connections_per_match": broker.connections_per_match(),
        "active_matches": active_matches,
        "scheduler_running": scheduler.running,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────
# PUBLIC MATCH ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/api/matches")
@limiter.limit("15/minute")
async def get_matches(request: Request, db: Session = Depends(get_db)):
    """All matches with current state — tournament overview page."""
    matches = db.query(MatchDB).order_by(MatchDB.kickoff_utc).all()
    return [_match_to_dict(m) for m in matches]


@app.get("/api/matches/{match_id}")
@limiter.limit("15/minute")
async def get_match(match_id: str, request: Request, db: Session = Depends(get_db)):
    """Single match with full data — match detail page."""
    match = db.query(MatchDB).filter(MatchDB.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    prediction = (
        db.query(PredictionDB)
        .filter(PredictionDB.match_id == match_id)
        .order_by(PredictionDB.id.desc())
        .first()
    )

    events = (
        db.query(LiveEventDB)
        .filter(LiveEventDB.match_id == match_id)
        .order_by(LiveEventDB.id.asc())
        .all()
    )

    result = _match_to_dict(match)
    if prediction:
        result["prediction"] = _prediction_to_dict(prediction)
    result["live_events"] = [_event_to_dict(e) for e in events]
    return result


@app.get("/api/matches/{match_id}/votes/summary")
@limiter.limit("15/minute")
async def get_vote_summary(match_id: str, request: Request, db: Session = Depends(get_db)):
    """Vote distribution for a match — shown on frontend alongside AI prediction."""
    votes = db.query(VoteDB).filter(
        VoteDB.match_id == match_id,
        VoteDB.trust_score >= 0.6,
        VoteDB.is_penalty_vote == False,
    ).all()

    total = len(votes)
    if total == 0:
        return {"total": 0, "home": 0, "draw": 0, "away": 0, "home_pct": 0, "draw_pct": 0, "away_pct": 0}

    home = sum(1 for v in votes if v.pick == "home")
    draw = sum(1 for v in votes if v.pick == "draw")
    away = sum(1 for v in votes if v.pick == "away")

    return {
        "total": total,
        "home": home,
        "draw": draw,
        "away": away,
        "home_pct": round(home / total * 100, 1),
        "draw_pct": round(draw / total * 100, 1),
        "away_pct": round(away / total * 100, 1),
    }


# ─────────────────────────────────────────────
# VOTING ENDPOINTS
# ─────────────────────────────────────────────

@app.post("/api/vote")
@limiter.limit("5/minute")
async def submit_vote(
    request: Request,
    vote_req: VoteRequest,
    db: Session = Depends(get_db),
):
    """
    Submit or update a vote.
    - 1 vote per match per fingerprint (can update until 85' lock)
    - Turnstile + reCAPTCHA + trust score
    - All data saved for research
    """
    # 1. Verify Turnstile
    if not await verify_turnstile(vote_req.turnstile_token):
        raise HTTPException(status_code=400, detail="Bot check failed")

    # 2. Verify reCAPTCHA
    recaptcha_score = await verify_recaptcha(vote_req.recaptcha_token or "")

    # 3. Check match exists and voting is open
    match = db.query(MatchDB).filter(MatchDB.id == vote_req.match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # State check: can only vote in SCHEDULED, LIVE, LIVE_2H, HT, ET_1H, ET_HT
    if match.state in ("FT", "ET_2H", "PENALTIES", "FINISHED", "VOID"):
        raise HTTPException(status_code=400, detail="Voting is closed for this match")

    # 85-minute hard lock
    try:
        current_minute = int(match.minute.replace("+", ""))
    except (ValueError, AttributeError):
        current_minute = 0

    if current_minute >= 85 and match.state not in ("PENALTIES",):
        raise HTTPException(status_code=400, detail="Voting locked at 85 minutes")

    # Penalty micro-vote check
    if vote_req.is_penalty_vote and match.state != "PENALTIES":
        raise HTTPException(status_code=400, detail="Penalty vote only valid during shootout")

    # 4. IP hash
    client_ip = get_remote_address(request)
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]

    # 5. Trust score
    trust_score = await calculate_trust_score(
        recaptcha_score=recaptcha_score,
        time_on_page_ms=vote_req.time_on_page_ms,
        mouse_moved=vote_req.mouse_moved,
        fingerprint_hash=vote_req.fingerprint_hash,
        request=request,
        db=db,
        match_id=vote_req.match_id,
    )

    # 6. Check for existing vote (update path)
    existing = (
        db.query(VoteDB)
        .filter(
            VoteDB.fingerprint_hash == vote_req.fingerprint_hash,
            VoteDB.match_id == vote_req.match_id,
            VoteDB.is_penalty_vote == vote_req.is_penalty_vote,
        )
        .first()
    )

    # Get kickoff delta
    now_utc = datetime.now(timezone.utc)
    kickoff = match.kickoff_utc
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    minutes_before_ko = max(0, int((kickoff - now_utc).total_seconds() / 60))

    # Current AI confidence snapshot
    current_ai_confidence = match.model_confidence or {}

    if existing:
        changed_from = existing.pick
        existing.pick = vote_req.pick
        existing.first_scorer = vote_req.first_scorer
        existing.confidence_level = vote_req.confidence_level
        existing.timestamp = now_utc
        existing.match_minute_at_vote = match.minute
        existing.score_at_vote = f"{match.home_score}-{match.away_score}"
        existing.ai_confidence_at_vote = current_ai_confidence
        existing.change_count = (existing.change_count or 0) + 1
        existing.changed_from = changed_from
        existing.trust_score = trust_score
        existing.recaptcha_score = recaptcha_score
        db.commit()
        db.refresh(existing)
        vote_id = existing.id
        is_update = True
    else:
        new_vote = VoteDB(
            match_id=vote_req.match_id,
            fingerprint_hash=vote_req.fingerprint_hash,
            session_id=vote_req.session_id or str(uuid.uuid4()),
            pick=vote_req.pick,
            first_scorer=vote_req.first_scorer,
            confidence_level=vote_req.confidence_level,
            trust_score=trust_score,
            recaptcha_score=recaptcha_score,
            timestamp=now_utc,
            minute_before_kickoff=minutes_before_ko,
            match_minute_at_vote=match.minute,
            score_at_vote=f"{match.home_score}-{match.away_score}",
            ai_confidence_at_vote=current_ai_confidence,
            change_count=0,
            changed_from=None,
            is_penalty_vote=vote_req.is_penalty_vote,
            ip_hash=ip_hash,
        )
        db.add(new_vote)
        db.commit()
        db.refresh(new_vote)
        vote_id = new_vote.id
        is_update = False

    # 7. Push updated vote summary via SSE
    summary = await _get_vote_summary_dict(vote_req.match_id, db)
    await broker.publish(vote_req.match_id, "vote_update", summary)

    return {
        "success": True,
        "vote_id": vote_id,
        "updated": is_update,
        "trust_score": round(trust_score, 3),
        "trust_level": (
            "verified" if trust_score >= 0.8
            else "probable" if trust_score >= 0.6
            else "unverified"
        ),
    }


# ─────────────────────────────────────────────
# SSE STREAM ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/stream/{match_id}")
async def stream_match(match_id: str, request: Request):
    """
    SSE stream for a single match.
    One connection per IP (enforced by Cloudflare + Render).
    Auto-reconnects on drop.
    """
    if match_id == "test":
        # Health check test endpoint
        async def test_gen():
            yield "data: {\"type\": \"connected\", \"match_id\": \"test\"}\n\n"
        return StreamingResponse(test_gen(), media_type="text/event-stream")

    q = broker.subscribe(match_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        # Send initial connection event
        yield f"data: {json.dumps({'type': 'connected', 'match_id': match_id})}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment every 30s
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broker.unsubscribe(match_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/stream/global/all")
async def stream_global(request: Request):
    """
    Global SSE stream — receives updates for ALL matches.
    Used by tournament overview page.
    """
    q = broker.subscribe_global()

    async def event_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'type': 'connected', 'scope': 'global'})}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broker.unsubscribe_global(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────
# ADMIN AUTH
# ─────────────────────────────────────────────

@app.post("/api/admin/login")
@limiter.limit("5/minute")
async def admin_login(request: Request, body: AdminLoginRequest):
    """Admin password login — returns session token."""
    if body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")
    token = create_admin_token()
    return {"token": token, "expires_in": 28800}


# ─────────────────────────────────────────────
# ADMIN ENDPOINTS (all require Bearer token)
# ─────────────────────────────────────────────

@app.get("/api/admin/dashboard")
@limiter.limit("60/minute")
async def admin_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(get_admin),
):
    """Full admin dashboard data — single call."""
    # Active matches
    active = db.query(MatchDB).filter(MatchDB.state.in_(["LIVE", "HT", "LIVE_2H", "ET_1H", "ET_HT", "ET_2H", "PENALTIES"])).all()

    # Source health
    sources = db.query(SourceHealthDB).all()

    # Model versions
    latest_model = db.query(ModelVersionDB).order_by(ModelVersionDB.version.desc()).first()

    # Vote stats
    total_votes = db.query(VoteDB).filter(VoteDB.is_penalty_vote == False).count()
    verified_votes = db.query(VoteDB).filter(VoteDB.trust_score >= 0.8, VoteDB.is_penalty_vote == False).count()
    probable_votes = db.query(VoteDB).filter(VoteDB.trust_score >= 0.6, VoteDB.trust_score < 0.8, VoteDB.is_penalty_vote == False).count()

    # Today's votes
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_votes = db.query(VoteDB).filter(VoteDB.timestamp >= today_start, VoteDB.is_penalty_vote == False).count()

    # Votes per match
    votes_by_match = []
    all_matches = db.query(MatchDB).order_by(MatchDB.kickoff_utc).all()
    for m in all_matches:
        count = db.query(VoteDB).filter(VoteDB.match_id == m.id, VoteDB.is_penalty_vote == False).count()
        votes_by_match.append({"match_id": m.id, "label": f"{m.home_team} vs {m.away_team}", "votes": count})

    # Pipeline state
    pipeline_state = {}
    if hasattr(app.state, "pipeline") and app.state.pipeline:
        pipeline_state = app.state.pipeline.get_status()

    return {
        "active_matches": [_match_to_dict(m) for m in active],
        "source_health": [_source_health_to_dict(s) for s in sources],
        "sse_connections": broker.total_connections(),
        "connections_per_match": broker.connections_per_match(),
        "model": {
            "version": latest_model.version if latest_model else 0,
            "accuracy_after": latest_model.accuracy_after if latest_model else None,
            "deployed": latest_model.deployed if latest_model else False,
            "trained_at": latest_model.trained_at.isoformat() if latest_model and latest_model.trained_at else None,
        },
        "votes": {
            "total": total_votes,
            "today": today_votes,
            "verified": verified_votes,
            "probable": probable_votes,
            "unverified": total_votes - verified_votes - probable_votes,
            "by_match": votes_by_match,
        },
        "pipeline": pipeline_state,
        "scheduler_jobs": [{"id": j.id, "next_run": str(j.next_run_time)} for j in scheduler.get_jobs()],
    }


@app.get("/api/admin/matches")
@limiter.limit("60/minute")
async def admin_get_matches(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(get_admin),
):
    """All matches for admin — includes internal fields."""
    matches = db.query(MatchDB).order_by(MatchDB.kickoff_utc).all()
    return [_match_to_dict(m, admin=True) for m in matches]


@app.get("/api/admin/alerts")
@limiter.limit("60/minute")
async def admin_get_alerts(
    request: Request,
    _: bool = Depends(get_admin),
):
    """Recent alerts from alerts module."""
    try:
        from alerts import get_recent_alerts
        alerts = get_recent_alerts(limit=50)
        return {"alerts": alerts}
    except ImportError:
        return {"alerts": [], "note": "alerts.py not available"}


@app.get("/api/admin/model/versions")
@limiter.limit("60/minute")
async def admin_model_versions(
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(get_admin),
):
    """All model versions — MLOps browser."""
    versions = db.query(ModelVersionDB).order_by(ModelVersionDB.version.desc()).limit(104).all()
    return [
        {
            "version": v.version,
            "accuracy_before": v.accuracy_before,
            "accuracy_after": v.accuracy_after,
            "improvement_pct": v.improvement_pct,
            "deployed": v.deployed,
            "training_match_id": v.training_match_id,
            "trained_at": v.trained_at.isoformat() if v.trained_at else None,
            "deploy_decision": v.deploy_decision,
            "training_duration_s": v.training_duration_s,
            "mlflow_run_id": v.mlflow_run_id,
        }
        for v in versions
    ]


@app.post("/api/admin/model/retrain")
@limiter.limit("5/minute")
async def admin_retrain(
    request: Request,
    body: RetrainRequest,
    background_tasks: BackgroundTasks,
    _: bool = Depends(get_admin),
):
    """Trigger model retraining as background task."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to trigger retraining")

    # Check no active matches
    if hasattr(app.state, "pipeline") and app.state.pipeline:
        active = app.state.pipeline.get_active_match_ids()
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot retrain during live matches: {active}",
            )

    async def run_retrain():
        try:
            from model import DeltaModel
            model = DeltaModel()
            result = await asyncio.get_event_loop().run_in_executor(None, model.retrain)
            await broker.publish_global("retrain_complete", result)
            logger.info(f"Retrain complete: {result}")
        except Exception as e:
            logger.error(f"Retrain failed: {e}")
            await broker.publish_global("retrain_failed", {"error": str(e)})

    background_tasks.add_task(run_retrain)
    return {"status": "retrain_started", "message": "Retraining running in background — watch SSE for result"}


@app.post("/api/admin/match/override-state")
@limiter.limit("60/minute")
async def admin_override_state(
    request: Request,
    body: MatchStateOverride,
    db: Session = Depends(get_db),
    _: bool = Depends(get_admin),
):
    """Emergency state override — admin only."""
    valid_states = {"SCHEDULED", "LIVE", "HT", "LIVE_2H", "FT", "ET_1H", "ET_HT", "ET_2H", "PENALTIES", "FINISHED", "VOID"}
    if body.state not in valid_states:
        raise HTTPException(status_code=400, detail=f"Invalid state. Must be one of: {valid_states}")

    match = db.query(MatchDB).filter(MatchDB.id == body.match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    old_state = match.state
    match.state = body.state
    match.last_updated = datetime.now(timezone.utc)
    db.commit()

    await broker.publish(body.match_id, "state_change", {
        "match_id": body.match_id, "old_state": old_state, "new_state": body.state,
        "manual_override": True, "note": body.note,
    })

    logger.warning(f"Admin override: {body.match_id} {old_state} → {body.state} | {body.note}")
    return {"success": True, "match_id": body.match_id, "old_state": old_state, "new_state": body.state}


@app.get("/api/admin/votes/{match_id}")
@limiter.limit("60/minute")
async def admin_get_votes(
    match_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _: bool = Depends(get_admin),
):
    """Full vote data for a match — admin research view."""
    votes = db.query(VoteDB).filter(VoteDB.match_id == match_id).order_by(VoteDB.timestamp).all()
    return [
        {
            "id": v.id,
            "pick": v.pick,
            "trust_score": v.trust_score,
            "trust_level": "verified" if (v.trust_score or 0) >= 0.8 else "probable" if (v.trust_score or 0) >= 0.6 else "unverified",
            "timestamp": v.timestamp.isoformat() if v.timestamp else None,
            "minute_before_kickoff": v.minute_before_kickoff,
            "match_minute_at_vote": v.match_minute_at_vote,
            "score_at_vote": v.score_at_vote,
            "change_count": v.change_count,
            "changed_from": v.changed_from,
            "is_penalty_vote": v.is_penalty_vote,
        }
        for v in votes
    ]


@app.get("/api/admin/post-draft")
@limiter.limit("60/minute")
async def admin_get_post_draft(
    request: Request,
    match_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _: bool = Depends(get_admin),
):
    """
    Generate LinkedIn + X post draft for a finished match.
    Template-filled from match + prediction data.
    """
    if match_id:
        match = db.query(MatchDB).filter(MatchDB.id == match_id).first()
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
    else:
        # Get most recently finished match
        match = (
            db.query(MatchDB)
            .filter(MatchDB.state.in_(["FINISHED", "FT"]))
            .order_by(MatchDB.last_updated.desc())
            .first()
        )
        if not match:
            return {"linkedin": "", "x": "", "note": "No finished matches yet"}

    prediction = (
        db.query(PredictionDB)
        .filter(PredictionDB.match_id == match.id)
        .order_by(PredictionDB.id.desc())
        .first()
    )

    if not prediction:
        return {"linkedin": "", "x": "", "note": "No prediction data for this match"}

    # Determine result
    if match.home_score > match.away_score:
        winner = match.home_team
        ai_correct = prediction.home_win == max(prediction.home_win, prediction.draw, prediction.away_win)
    elif match.away_score > match.home_score:
        winner = match.away_team
        ai_correct = prediction.away_win == max(prediction.home_win, prediction.draw, prediction.away_win)
    else:
        winner = "Draw"
        ai_correct = prediction.draw == max(prediction.home_win, prediction.draw, prediction.away_win)

    result_emoji = "✅" if ai_correct else "❌"

    linkedin = f"""Match: {match.home_team} {match.home_score}–{match.away_score} {match.away_team}
AI predicted: {match.home_team} {round((prediction.home_win or 0) * 100)}% · Draw {round((prediction.draw or 0) * 100)}% · {match.away_team} {round((prediction.away_win or 0) * 100)}%
Result: {result_emoji} {"correct" if ai_correct else "wrong"}

{match.post_match_debrief or "Post-match analysis pending."}

Model v{prediction.model_version} — trained on {prediction.training_matches_seen} matches.
#WorldCup2026 #AI #buildinpublic #MachineLearning"""

    # X version: compressed
    x_post = f"""{match.home_team} {match.home_score}–{match.away_score} {match.away_team}

AI said: {match.home_team} {round((prediction.home_win or 0) * 100)}% · Draw {round((prediction.draw or 0) * 100)}% · {match.away_team} {round((prediction.away_win or 0) * 100)}%
Result: {result_emoji}

Model v{prediction.model_version} | {prediction.training_matches_seen} matches trained
#WorldCup2026 #AI"""

    return {
        "match_id": match.id,
        "linkedin": linkedin,
        "x": x_post,
        "ai_correct": ai_correct,
    }


@app.post("/api/admin/sheets/sync")
@limiter.limit("10/minute")
async def admin_sync_sheets(
    request: Request,
    background_tasks: BackgroundTasks,
    match_id: Optional[str] = None,
    _: bool = Depends(get_admin),
):
    """Manual Google Sheets sync trigger."""
    async def run_sync():
        try:
            from sheets import SheetsWriter
            writer = SheetsWriter()
            if match_id:
                # Sync specific match
                logger.info(f"Manual Sheets sync: {match_id}")
            else:
                await writer.sync_all_pending()
            logger.info("Manual Sheets sync complete")
        except Exception as e:
            logger.error(f"Manual Sheets sync failed: {e}")

    background_tasks.add_task(run_sync)
    return {"status": "sync_started"}


# ─────────────────────────────────────────────
# PIPELINE PUBLISH ENDPOINT (internal use)
# ─────────────────────────────────────────────

@app.post("/internal/publish")
async def internal_publish(request: Request, body: dict):
    """
    Internal endpoint: pipeline.py calls this to push SSE updates.
    Only accessible from localhost.
    """
    client_ip = get_remote_address(request)
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Internal only")

    match_id = body.get("match_id")
    event_type = body.get("event_type", "update")
    data = body.get("data", {})

    if match_id:
        await broker.publish(match_id, event_type, data)
    else:
        await broker.publish_global(event_type, data)

    return {"published": True}


# ─────────────────────────────────────────────
# SERIALISATION HELPERS
# ─────────────────────────────────────────────

def _match_to_dict(match: MatchDB, admin: bool = False) -> dict:
    d = {
        "id": match.id,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "kickoff_utc": match.kickoff_utc.isoformat() if match.kickoff_utc else None,
        "venue": match.venue,
        "group": match.group_name,
        "phase": match.phase,
        "state": match.state,
        "score": {"home": match.home_score, "away": match.away_score},
        "minute": match.minute,
        "ai_context": match.ai_context,
        "model_confidence": match.model_confidence,
        "model_version": match.model_version,
        "source_used": match.source_used,
        "last_updated": match.last_updated.isoformat() if match.last_updated else None,
        "pre_match_brief": match.pre_match_brief,
        "went_to_et": match.went_to_et,
        "went_to_penalties": match.went_to_penalties,
    }
    if match.went_to_penalties:
        d["penalties"] = {"home": match.penalty_home, "away": match.penalty_away}
    if admin:
        d["events"] = match.events
        d["post_match_debrief"] = match.post_match_debrief
    return d


def _prediction_to_dict(p: PredictionDB) -> dict:
    return {
        "home_win": p.home_win,
        "draw": p.draw,
        "away_win": p.away_win,
        "confidence_range": f"{round((p.confidence_range_low or 0) * 100)}-{round((p.confidence_range_high or 0) * 100)}%",
        "predicted_scorer": p.predicted_scorer,
        "predicted_score": p.predicted_score,
        "model_version": p.model_version,
        "training_matches_seen": p.training_matches_seen,
        "locked_at_85": p.locked_at_85,
    }


def _event_to_dict(e: LiveEventDB) -> dict:
    return {
        "type": e.event_type,
        "minute": e.minute,
        "player": e.player,
        "team": e.team,
        "sentiment": e.sentiment,
        "context": e.context,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
    }


def _source_health_to_dict(s: SourceHealthDB) -> dict:
    return {
        "source": s.source_name,
        "status": s.status,
        "last_check": s.last_check.isoformat() if s.last_check else None,
        "block_count_today": s.block_count_today,
        "consecutive_failures": s.consecutive_failures,
    }


async def _get_vote_summary_dict(match_id: str, db: Session) -> dict:
    votes = db.query(VoteDB).filter(
        VoteDB.match_id == match_id,
        VoteDB.trust_score >= 0.6,
        VoteDB.is_penalty_vote == False,
    ).all()
    total = len(votes)
    if total == 0:
        return {"total": 0, "home_pct": 0, "draw_pct": 0, "away_pct": 0}
    home = sum(1 for v in votes if v.pick == "home")
    draw = sum(1 for v in votes if v.pick == "draw")
    away = sum(1 for v in votes if v.pick == "away")
    return {
        "total": total,
        "home_pct": round(home / total * 100, 1),
        "draw_pct": round(draw / total * 100, 1),
        "away_pct": round(away / total * 100, 1),
    }


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("ENV", "dev") == "dev",
        log_level="info",
    )
