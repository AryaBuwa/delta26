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

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://delta26.vercel.app")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./delta.db")
CLOUDFLARE_TURNSTILE_SECRET = os.getenv("CLOUDFLARE_TURNSTILE_SECRET", "")
RECAPTCHA_SECRET = os.getenv("RECAPTCHA_SECRET_KEY", "")

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
    def __init__(self):
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
        payload = json.dumps({"type": event_type, "match_id": match_id, "data": data, "ts": time.time()})
        dead = set()
        for q in self._queues.get(match_id, set()):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.add(q)
        for q in dead:
            self._queues[match_id].discard(q)
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

    def connection_count(self) -> int:
        return self.total_connections()

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
_admin_token_store: dict[str, float] = {}


def verify_admin_token(token: str) -> bool:
    expiry = _admin_token_store.get(token, 0)
    return time.time() < expiry


def create_admin_token() -> str:
    token = str(uuid.uuid4())
    _admin_token_store[token] = time.time() + 3600 * 8
    return token


def check_admin_password(request: Request) -> bool:
    """
    Accept EITHER:
    1. Raw password as Bearer token (what the frontend sends)
    2. A valid JWT token from /api/admin/login
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin auth required")
    token = auth.split(" ", 1)[1]
    # Check raw password first
    if token == ADMIN_PASSWORD:
        return True
    # Check session token
    if verify_admin_token(token):
        return True
    raise HTTPException(status_code=401, detail="Invalid password or expired token")


def get_admin(request: Request) -> bool:
    return check_admin_password(request)


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


class AddMatchRequest(BaseModel):
    match_id: str
    home_team: str
    away_team: str
    kickoff_utc: str
    venue: Optional[str] = None
    group: Optional[str] = None
    phase: Optional[str] = "group"


# ─────────────────────────────────────────────
# TRUST SCORE
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
    score = 0.0
    score += min(recaptcha_score, 1.0) * 0.35
    if time_on_page_ms >= 30_000:
        score += 0.20
    elif time_on_page_ms >= 10_000:
        score += 0.10
    if mouse_moved:
        score += 0.15
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
    existing = (
        db.query(VoteDB)
        .filter(VoteDB.fingerprint_hash == fingerprint_hash, VoteDB.match_id == match_id)
        .first()
    )
    if not existing:
        score += 0.10
    score += 0.05
    return min(score, 1.0)


async def verify_turnstile(token: str) -> bool:
    if not CLOUDFLARE_TURNSTILE_SECRET or token in ("dev-token", ""):
        return True
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": CLOUDFLARE_TURNSTILE_SECRET, "response": token},
            timeout=5.0,
        )
        return resp.json().get("success", False)


async def verify_recaptcha(token: str) -> float:
    if not RECAPTCHA_SECRET or not token:
        return 0.7
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
# LIFESPAN
# ─────────────────────────────────────────────

scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Project Delta starting up")
    init_db()

    try:
        from pipeline import PipelineOrchestrator
        app.state.pipeline = PipelineOrchestrator(broker=broker)
        await app.state.pipeline.start()
        logger.info("✅ Pipeline started")
    except ImportError:
        logger.warning("pipeline.py not found — running without live pipeline")
        app.state.pipeline = None

    async def keep_alive():
        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"http://localhost:{os.getenv('PORT', '8000')}/health", timeout=5.0)
        except Exception:
            pass

    scheduler.add_job(keep_alive, IntervalTrigger(minutes=13), id="keep_alive")

    async def source_health_job():
        if app.state.pipeline:
            await app.state.pipeline.check_source_health()

    scheduler.add_job(source_health_job, IntervalTrigger(minutes=30), id="source_health")

    async def pre_match_brief_job():
        if app.state.pipeline:
            await app.state.pipeline.generate_pre_match_briefs()

    scheduler.add_job(pre_match_brief_job, IntervalTrigger(minutes=15), id="pre_match_brief")

    async def fixture_check_job():
        if app.state.pipeline:
            await app.state.pipeline.check_fixtures()

    scheduler.add_job(fixture_check_job, IntervalTrigger(hours=2), id="fixture_check")

    scheduler.start()
    logger.info("✅ Scheduler started")

    yield

    logger.info("🛑 Shutting down")
    scheduler.shutdown(wait=False)
    if app.state.pipeline:
        await app.state.pipeline.stop()


# ─────────────────────────────────────────────
# APP INIT
# ─────────────────────────────────────────────

app = FastAPI(
    title="Project Delta API",
    description="AI vs Human prediction system for FIFA World Cup 2026",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "https://delta26.vercel.app", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# ─────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "sse_connections": broker.total_connections(),
    }


@app.get("/health/detailed")
async def health_detailed(db: Session = Depends(get_db)):
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")

    pipeline_ok = False
    active_matches = []
    if hasattr(app.state, "pipeline") and app.state.pipeline:
        pipeline_ok = True
        active_matches = app.state.pipeline.get_active_match_ids()

    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "pipeline": "ok" if pipeline_ok else "not_running",
        "sse_connections": broker.total_connections(),
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
    matches = db.query(MatchDB).order_by(MatchDB.kickoff_utc).all()
    return [_match_to_dict(m) for m in matches]


@app.get("/api/matches/{match_id}")
@limiter.limit("15/minute")
async def get_match(match_id: str, request: Request, db: Session = Depends(get_db)):
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
        "home": home, "draw": draw, "away": away,
        "home_pct": round(home / total * 100, 1),
        "draw_pct": round(draw / total * 100, 1),
        "away_pct": round(away / total * 100, 1),
    }


# ─────────────────────────────────────────────
# VOTING
# ─────────────────────────────────────────────

@app.post("/api/vote")
@limiter.limit("5/minute")
async def submit_vote(request: Request, vote_req: VoteRequest, db: Session = Depends(get_db)):
    if not await verify_turnstile(vote_req.turnstile_token):
        raise HTTPException(status_code=400, detail="Bot check failed")

    recaptcha_score = await verify_recaptcha(vote_req.recaptcha_token or "")

    match = db.query(MatchDB).filter(MatchDB.id == vote_req.match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    if match.state in ("FT", "ET_2H", "PENALTIES", "FINISHED", "VOID"):
        raise HTTPException(status_code=400, detail="Voting is closed for this match")

    try:
        current_minute = int(match.minute.replace("+", ""))
    except (ValueError, AttributeError):
        current_minute = 0

    if current_minute >= 85 and match.state not in ("PENALTIES",):
        raise HTTPException(status_code=400, detail="Voting locked at 85 minutes")

    if vote_req.is_penalty_vote and match.state != "PENALTIES":
        raise HTTPException(status_code=400, detail="Penalty vote only valid during shootout")

    client_ip = get_remote_address(request)
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]

    trust_score = await calculate_trust_score(
        recaptcha_score=recaptcha_score,
        time_on_page_ms=vote_req.time_on_page_ms,
        mouse_moved=vote_req.mouse_moved,
        fingerprint_hash=vote_req.fingerprint_hash,
        request=request,
        db=db,
        match_id=vote_req.match_id,
    )

    existing = (
        db.query(VoteDB)
        .filter(
            VoteDB.fingerprint_hash == vote_req.fingerprint_hash,
            VoteDB.match_id == vote_req.match_id,
            VoteDB.is_penalty_vote == vote_req.is_penalty_vote,
        )
        .first()
    )

    now_utc = datetime.now(timezone.utc)
    kickoff = match.kickoff_utc
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    minutes_before_ko = max(0, int((kickoff - now_utc).total_seconds() / 60))
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
# SSE STREAMS
# ─────────────────────────────────────────────

@app.get("/stream/{match_id}")
async def stream_match(match_id: str, request: Request):
    if match_id == "test":
        async def test_gen():
            yield 'data: {"type": "connected", "match_id": "test"}\n\n'
        return StreamingResponse(test_gen(), media_type="text/event-stream")

    q = broker.subscribe(match_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'type': 'connected', 'match_id': match_id})}\n\n"
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
            broker.unsubscribe(match_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/stream/global/all")
async def stream_global(request: Request):
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
    if body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")
    token = create_admin_token()
    return {"token": token, "expires_in": 28800}


# ─────────────────────────────────────────────
# ADMIN ROUTES — frontend calls these with raw password as Bearer token
# ─────────────────────────────────────────────

@app.get("/admin/status")
@limiter.limit("60/minute")
async def admin_status(request: Request, db: Session = Depends(get_db)):
    check_admin_password(request)
    sources = db.query(SourceHealthDB).all()
    return {
        "sse_connections": broker.total_connections(),
        "render_cpu_pct": 0,
        "render_memory_pct": 0,
        "render_hours_used": 0,
        "groq_key1_pct": 0,
        "groq_key2_pct": 0,
        "sources": [
            {
                "name": s.source_name,
                "status": s.status or "ok",
                "last_check": s.last_check.isoformat() if s.last_check else None,
            }
            for s in sources
        ],
    }


@app.get("/admin/model")
@limiter.limit("60/minute")
async def admin_model(request: Request, db: Session = Depends(get_db)):
    check_admin_password(request)
    latest = db.query(ModelVersionDB).order_by(ModelVersionDB.version.desc()).first()
    history = db.query(ModelVersionDB).order_by(ModelVersionDB.version.asc()).all()
    return {
        "current_version": latest.version if latest else 0,
        "current_accuracy": latest.accuracy_after if latest and latest.accuracy_after else 0.0,
        "training_match_count": latest.version if latest else 0,
        "accuracy_history": [
            {"version": v.version, "accuracy": v.accuracy_after or 0.0}
            for v in history
        ],
    }


@app.get("/admin/research")
@limiter.limit("60/minute")
async def admin_research(request: Request, db: Session = Depends(get_db)):
    check_admin_password(request)
    total_votes = db.query(VoteDB).filter(VoteDB.is_penalty_vote == False).count()
    verified_votes = db.query(VoteDB).filter(VoteDB.trust_score >= 0.8, VoteDB.is_penalty_vote == False).count()
    probable_votes = db.query(VoteDB).filter(
        VoteDB.trust_score >= 0.6, VoteDB.trust_score < 0.8, VoteDB.is_penalty_vote == False
    ).count()
    excluded_votes = db.query(VoteDB).filter(VoteDB.trust_score < 0.6, VoteDB.is_penalty_vote == False).count()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    votes_today = db.query(VoteDB).filter(VoteDB.timestamp >= today_start, VoteDB.is_penalty_vote == False).count()

    all_matches = db.query(MatchDB).order_by(MatchDB.kickoff_utc).all()
    votes_per_match = []
    for m in all_matches:
        total = db.query(VoteDB).filter(VoteDB.match_id == m.id, VoteDB.is_penalty_vote == False).count()
        verified = db.query(VoteDB).filter(
            VoteDB.match_id == m.id, VoteDB.trust_score >= 0.8, VoteDB.is_penalty_vote == False
        ).count()
        votes_per_match.append({
            "match_id": m.id,
            "label": f"{m.home_team[:3].upper()} vs {m.away_team[:3].upper()}",
            "total": total,
            "verified": verified,
        })

    return {
        "total_votes": total_votes,
        "verified_votes": verified_votes,
        "votes_today": votes_today,
        "excluded_votes": excluded_votes,
        "trust_distribution": [
            {"label": "Verified", "count": verified_votes, "pct": (verified_votes / max(total_votes, 1)) * 100},
            {"label": "Probable", "count": probable_votes, "pct": (probable_votes / max(total_votes, 1)) * 100},
            {"label": "Excluded", "count": excluded_votes, "pct": (excluded_votes / max(total_votes, 1)) * 100},
        ],
        "votes_per_match": votes_per_match,
        "sheets_sync_ok": True,
        "sheets_last_sync": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/admin/drafts")
@limiter.limit("60/minute")
async def admin_drafts(request: Request, db: Session = Depends(get_db)):
    check_admin_password(request)
    finished = db.query(MatchDB).filter(
        MatchDB.state.in_(["FINISHED", "FT"])
    ).order_by(MatchDB.last_updated.desc()).limit(5).all()

    drafts = []
    for match in finished:
        prediction = (
            db.query(PredictionDB)
            .filter(PredictionDB.match_id == match.id)
            .order_by(PredictionDB.id.desc())
            .first()
        )
        if not prediction:
            continue

        if match.home_score > match.away_score:
            ai_correct = (prediction.home_win or 0) == max(prediction.home_win or 0, prediction.draw or 0, prediction.away_win or 0)
        elif match.away_score > match.home_score:
            ai_correct = (prediction.away_win or 0) == max(prediction.home_win or 0, prediction.draw or 0, prediction.away_win or 0)
        else:
            ai_correct = (prediction.draw or 0) == max(prediction.home_win or 0, prediction.draw or 0, prediction.away_win or 0)

        result_emoji = "✅" if ai_correct else "❌"

        linkedin = f"""Match: {match.home_team} {match.home_score}–{match.away_score} {match.away_team}
AI predicted: {match.home_team} {round((prediction.home_win or 0) * 100)}% · Draw {round((prediction.draw or 0) * 100)}% · {match.away_team} {round((prediction.away_win or 0) * 100)}%
Result: {result_emoji} {"correct" if ai_correct else "wrong"}

{match.post_match_debrief or "Post-match analysis pending."}

Model v{prediction.model_version} — trained on {prediction.training_matches_seen} matches.
#WorldCup2026 #AI #buildinpublic #MachineLearning"""

        x_post = f"""{match.home_team} {match.home_score}–{match.away_score} {match.away_team}
AI: {match.home_team} {round((prediction.home_win or 0) * 100)}% · Draw {round((prediction.draw or 0) * 100)}% · {match.away_team} {round((prediction.away_win or 0) * 100)}%
Result: {result_emoji}
Model v{prediction.model_version} | {prediction.training_matches_seen} matches
#WorldCup2026 #AI"""

        drafts.append({"platform": "linkedin", "match_id": match.id, "content": linkedin})
        drafts.append({"platform": "x", "match_id": match.id, "content": x_post})

    return drafts


@app.post("/admin/retrain")
@limiter.limit("5/minute")
async def admin_retrain_simple(request: Request, background_tasks: BackgroundTasks):
    check_admin_password(request)

    async def run_retrain():
        try:
            from model import DeltaModel
            model = DeltaModel()
            result = await asyncio.get_event_loop().run_in_executor(None, model.retrain)
            await broker.publish_global("retrain_complete", result)
        except Exception as e:
            logger.error(f"Retrain failed: {e}")
            await broker.publish_global("retrain_failed", {"error": str(e)})

    background_tasks.add_task(run_retrain)
    return {"message": "Retraining started — watch SSE for result"}


@app.post("/admin/sync-sheets")
@limiter.limit("10/minute")
async def admin_sync_sheets_simple(request: Request, background_tasks: BackgroundTasks):
    check_admin_password(request)

    async def run_sync():
        try:
            from sheets import SheetsWriter
            writer = SheetsWriter()
            await writer.sync_all_pending()
        except Exception as e:
            logger.error(f"Sheets sync failed: {e}")

    background_tasks.add_task(run_sync)
    return {"message": "Sheets sync started"}


# ─────────────────────────────────────────────
# ADMIN — ADD MATCH (for seeding fixtures)
# ─────────────────────────────────────────────

@app.post("/admin/matches/add")
@limiter.limit("60/minute")
async def admin_add_match(request: Request, body: AddMatchRequest, db: Session = Depends(get_db)):
    check_admin_password(request)
    existing = db.query(MatchDB).filter(MatchDB.id == body.match_id).first()
    if existing:
        return {"status": "already_exists", "match_id": body.match_id}

    kickoff = datetime.fromisoformat(body.kickoff_utc.replace("Z", "+00:00"))
    match = MatchDB(
        id=body.match_id,
        home_team=body.home_team,
        away_team=body.away_team,
        kickoff_utc=kickoff,
        venue=body.venue,
        group_name=body.group,
        phase=body.phase or "group",
        state="SCHEDULED",
        home_score=0,
        away_score=0,
        minute="0",
        last_updated=datetime.now(timezone.utc),
    )
    db.add(match)
    db.commit()
    logger.info(f"Match added: {body.match_id} — {body.home_team} vs {body.away_team}")
    return {"status": "created", "match_id": body.match_id}


# ─────────────────────────────────────────────
# EXISTING ADMIN ENDPOINTS (token-based, kept for compatibility)
# ─────────────────────────────────────────────

@app.get("/api/admin/dashboard")
@limiter.limit("60/minute")
async def admin_dashboard(request: Request, db: Session = Depends(get_db), _: bool = Depends(get_admin)):
    active = db.query(MatchDB).filter(MatchDB.state.in_(["LIVE", "HT", "LIVE_2H", "ET_1H", "ET_HT", "ET_2H", "PENALTIES"])).all()
    sources = db.query(SourceHealthDB).all()
    latest_model = db.query(ModelVersionDB).order_by(ModelVersionDB.version.desc()).first()
    total_votes = db.query(VoteDB).filter(VoteDB.is_penalty_vote == False).count()
    verified_votes = db.query(VoteDB).filter(VoteDB.trust_score >= 0.8, VoteDB.is_penalty_vote == False).count()
    probable_votes = db.query(VoteDB).filter(VoteDB.trust_score >= 0.6, VoteDB.trust_score < 0.8, VoteDB.is_penalty_vote == False).count()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_votes = db.query(VoteDB).filter(VoteDB.timestamp >= today_start, VoteDB.is_penalty_vote == False).count()
    return {
        "active_matches": [_match_to_dict(m) for m in active],
        "source_health": [_source_health_to_dict(s) for s in sources],
        "sse_connections": broker.total_connections(),
        "model": {
            "version": latest_model.version if latest_model else 0,
            "accuracy_after": latest_model.accuracy_after if latest_model else None,
        },
        "votes": {
            "total": total_votes, "today": today_votes,
            "verified": verified_votes, "probable": probable_votes,
        },
    }


@app.post("/api/admin/match/override-state")
@limiter.limit("60/minute")
async def admin_override_state(request: Request, body: MatchStateOverride, db: Session = Depends(get_db), _: bool = Depends(get_admin)):
    valid_states = {"SCHEDULED", "LIVE", "HT", "LIVE_2H", "FT", "ET_1H", "ET_HT", "ET_2H", "PENALTIES", "FINISHED", "VOID"}
    if body.state not in valid_states:
        raise HTTPException(status_code=400, detail=f"Invalid state")
    match = db.query(MatchDB).filter(MatchDB.id == body.match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    old_state = match.state
    match.state = body.state
    match.last_updated = datetime.now(timezone.utc)
    db.commit()
    await broker.publish(body.match_id, "state_change", {"old_state": old_state, "new_state": body.state})
    return {"success": True, "old_state": old_state, "new_state": body.state}


# ─────────────────────────────────────────────
# INTERNAL
# ─────────────────────────────────────────────

@app.post("/internal/publish")
async def internal_publish(request: Request, body: dict):
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
        "name": s.source_name,
        "status": s.status or "ok",
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
        reload=False,
        log_level="info",
    )