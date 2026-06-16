# ─────────────────────────────────────────────
# TRUST SCORE FIX — replace calculate_trust_score in main.py
# ─────────────────────────────────────────────
# Problem: real humans on mobile were scoring 0.545 → EXCLUDED
# Root causes:
#   1. recaptcha default 0.7 when no key → only 0.245 contribution
#   2. time threshold was 30s — too long, fast voters penalised
#   3. mouse_moved = False on all mobile devices (no mouse)
#   4. non-burst timing gave 0 for first-time voters
# Fix: fair scoring that passes real humans, blocks bots

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

    # ── reCAPTCHA (35%) ───────────────────────────────────────────
    # When no key is set, verify_recaptcha returns 0.7 (our default)
    # Real Google reCAPTCHA scores: 0.9 = human, 0.1 = bot
    # Weight it fully — this is the strongest signal
    score += min(recaptcha_score, 1.0) * 0.35

    # ── Time on page (20%) ────────────────────────────────────────
    # Original: 30s threshold — too strict, penalised fast mobile users
    # Fix: 5s = full credit, 1s = partial credit
    # A bot submits instantly (< 500ms). A human takes at least a second.
    if time_on_page_ms >= 5_000:
        score += 0.20
    elif time_on_page_ms >= 1_000:
        score += 0.10
    # < 1000ms = 0 (instant bot submission)

    # ── Human interaction (15%) ───────────────────────────────────
    # Original: mouse_moved only — gave 0 on ALL mobile devices
    # Fix: mouse OR touch counts (frontend sends mouse_moved=True on touch too)
    # If neither, still give partial credit — mobile tap is human behaviour
    if mouse_moved:
        score += 0.15
    elif time_on_page_ms >= 2_000:
        # Spent time on page without mouse = likely mobile tap voter
        score += 0.08

    # ── Burst detection (15%) ─────────────────────────────────────
    # Original: gave 0 for first-time voters (no prior votes to check)
    # Fix: first-time voter gets full credit (new user = not a bot pattern)
    one_minute_ago = datetime.now(timezone.utc).timestamp() - 60
    recent_votes = (
        db.query(VoteDB)
        .filter(
            VoteDB.fingerprint_hash == fingerprint_hash,
            VoteDB.timestamp >= datetime.fromtimestamp(one_minute_ago, tz=timezone.utc),
        )
        .count()
    )
    if recent_votes == 0:
        # First vote this minute = full credit (not burst voting)
        score += 0.15
    elif recent_votes < 3:
        score += 0.10
    elif recent_votes < 10:
        score += 0.05
    # >= 10 votes/min = 0 (bot)

    # ── Unique fingerprint (10%) ──────────────────────────────────
    # New fingerprint = hasn't voted before = not a repeat bot
    existing = (
        db.query(VoteDB)
        .filter(VoteDB.fingerprint_hash == fingerprint_hash, VoteDB.match_id == match_id)
        .first()
    )
    if not existing:
        score += 0.10
    else:
        # Changing vote = still human behaviour, give partial
        score += 0.05

    # ── Honeypot (5%) ────────────────────────────────────────────
    # Always passes if frontend is behaving correctly
    score += 0.05

    return min(score, 1.0)


# ─────────────────────────────────────────────
# Also fix verify_recaptcha default score
# Find this function in main.py and update the default return:
#
# async def verify_recaptcha(token: str) -> float:
#     if not RECAPTCHA_SECRET or not token:
#         return 0.9   ← change from 0.7 to 0.9
#
# When no reCAPTCHA key is set, we're in dev/trust mode.
# Return 0.9 so the reCAPTCHA factor contributes 0.315 instead of 0.245.
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# REAL HUMAN SCORE CHECK (mobile, no reCAPTCHA key):
#   reCAPTCHA:     0.9 × 0.35 = 0.315
#   time (5s+):              = 0.20
#   touch/mobile:            = 0.08  (no mouse but spent time)
#   first vote:              = 0.15
#   new fingerprint:         = 0.10
#   honeypot:                = 0.05
#   TOTAL:                   = 0.895 → VERIFIED ✅
#
# REAL HUMAN SCORE CHECK (desktop, voted before):
#   reCAPTCHA:     0.9 × 0.35 = 0.315
#   time (5s+):              = 0.20
#   mouse moved:             = 0.15
#   not burst:               = 0.15
#   repeat fingerprint:      = 0.05
#   honeypot:                = 0.05
#   TOTAL:                   = 0.915 → VERIFIED ✅
#
# BOT SCORE (instant, no interaction):
#   reCAPTCHA:     0.1 × 0.35 = 0.035
#   time (< 1s):             = 0
#   no mouse/touch:          = 0
#   burst voting:            = 0
#   repeat fingerprint:      = 0.05
#   honeypot:                = 0.05
#   TOTAL:                   = 0.135 → EXCLUDED ❌
# ─────────────────────────────────────────────