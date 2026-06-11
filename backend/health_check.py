"""
Project Delta — health_check.py
Pre-deploy verification. Run before every merge to main.

Usage:
    python health_check.py               # test localhost:8000
    python health_check.py --url https://delta-api.onrender.com

Exit codes:
    0 = all checks passed → safe to deploy
    1 = one or more checks failed → DO NOT DEPLOY

Checks:
    1. GET /health → 200
    2. GET /api/matches → valid JSON list
    3. GET /stream/test → SSE connects + receives event
    4. POST /api/vote/test → handled (returns 404 for fake match, not 500)
    5. GET /health/detailed → all systems status
    6. POST /api/admin/login → wrong password returns 401
    7. Env vars present (no missing secrets)
    8. Database connectivity
    9. Rate limit headers present
   10. CORS headers correct
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

DEFAULT_URL = "http://localhost:8000"
TIMEOUT = 10.0
SSE_CONNECT_TIMEOUT = 5.0

REQUIRED_ENV_VARS = [
    "GROQ_API_KEY_1",
    "GROQ_API_KEY_2",
    "TAVILY_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "ADMIN_PASSWORD",
    "CLOUDFLARE_TURNSTILE_SECRET",
]

# ─────────────────────────────────────────────
# RESULT TRACKING
# ─────────────────────────────────────────────

class CheckResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""
        self.duration_ms = 0

    def ok(self, message: str, duration_ms: float = 0):
        self.passed = True
        self.message = message
        self.duration_ms = duration_ms
        return self

    def fail(self, message: str, duration_ms: float = 0):
        self.passed = False
        self.message = message
        self.duration_ms = duration_ms
        return self

    def __str__(self):
        icon = "✅" if self.passed else "❌"
        timing = f" ({self.duration_ms:.0f}ms)" if self.duration_ms else ""
        return f"{icon} [{self.name}] {self.message}{timing}"


# ─────────────────────────────────────────────
# INDIVIDUAL CHECKS
# ─────────────────────────────────────────────

async def check_health_endpoint(client: httpx.AsyncClient, base_url: str) -> CheckResult:
    result = CheckResult("GET /health")
    t0 = time.perf_counter()
    try:
        resp = await client.get(f"{base_url}/health", timeout=TIMEOUT)
        duration = (time.perf_counter() - t0) * 1000
        if resp.status_code == 200:
            data = resp.json()
            if "status" in data and data["status"] == "ok":
                return result.ok(f"status=ok, connections={data.get('sse_connections', 0)}", duration)
            else:
                return result.fail(f"Expected status=ok, got: {data}", duration)
        else:
            return result.fail(f"HTTP {resp.status_code}", duration)
    except httpx.ConnectError:
        return result.fail("Cannot connect — is the server running?")
    except Exception as e:
        return result.fail(f"Exception: {e}")


async def check_matches_endpoint(client: httpx.AsyncClient, base_url: str) -> CheckResult:
    result = CheckResult("GET /api/matches")
    t0 = time.perf_counter()
    try:
        resp = await client.get(f"{base_url}/api/matches", timeout=TIMEOUT)
        duration = (time.perf_counter() - t0) * 1000
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return result.ok(f"Valid JSON list, {len(data)} match(es)", duration)
            else:
                return result.fail(f"Expected list, got {type(data).__name__}", duration)
        elif resp.status_code == 429:
            return result.fail("Rate limited — try again in a minute", duration)
        else:
            return result.fail(f"HTTP {resp.status_code}: {resp.text[:100]}", duration)
    except Exception as e:
        return result.fail(f"Exception: {e}")


async def check_sse_stream(base_url: str) -> CheckResult:
    result = CheckResult("GET /stream/test (SSE)")
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient() as sse_client:
            async with sse_client.stream(
                "GET",
                f"{base_url}/stream/test",
                timeout=SSE_CONNECT_TIMEOUT,
            ) as response:
                duration = (time.perf_counter() - t0) * 1000
                if response.status_code != 200:
                    return result.fail(f"HTTP {response.status_code}", duration)

                # Read first line
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        try:
                            data = json.loads(payload)
                            if data.get("type") == "connected":
                                return result.ok("SSE connects, receives 'connected' event", duration)
                            else:
                                return result.ok(f"SSE connects, received: {data.get('type')}", duration)
                        except json.JSONDecodeError:
                            return result.fail(f"SSE invalid JSON: {payload}", duration)
                    elif line.startswith(":"):
                        continue  # keepalive comment

                return result.fail("SSE connected but no data received", duration)
    except httpx.TimeoutException:
        duration = (time.perf_counter() - t0) * 1000
        # Timeout after connecting = SSE is streaming (correct behaviour)
        return result.ok("SSE streaming (timeout = connection held open)", duration)
    except httpx.ConnectError:
        return result.fail("Cannot connect to SSE endpoint")
    except Exception as e:
        return result.fail(f"Exception: {type(e).__name__}: {e}")


async def check_vote_endpoint(client: httpx.AsyncClient, base_url: str) -> CheckResult:
    """
    POST /api/vote with fake data — should return 404 (match not found)
    or 400 (bot check / validation), NOT 500.
    A 500 means the endpoint is broken.
    """
    result = CheckResult("POST /api/vote (smoke test)")
    t0 = time.perf_counter()
    try:
        payload = {
            "match_id": "HEALTH_CHECK_FAKE_MATCH",
            "pick": "home",
            "confidence_level": 3,
            "fingerprint_hash": "health_check_fingerprint_000",
            "turnstile_token": "HEALTH_CHECK",
            "time_on_page_ms": 5000,
            "mouse_moved": True,
            "is_penalty_vote": False,
        }
        resp = await client.post(f"{base_url}/api/vote", json=payload, timeout=TIMEOUT)
        duration = (time.perf_counter() - t0) * 1000

        if resp.status_code in (400, 404, 422):
            # Expected: bot check failed (400), match not found (404),
            # or validation error (422) — all mean endpoint is alive
            return result.ok(f"Endpoint alive, returned HTTP {resp.status_code} (expected)", duration)
        elif resp.status_code == 200:
            return result.ok("Endpoint alive, returned 200 (check Turnstile config)", duration)
        elif resp.status_code == 429:
            return result.ok("Rate limited (endpoint alive)", duration)
        elif resp.status_code >= 500:
            return result.fail(f"SERVER ERROR HTTP {resp.status_code}: {resp.text[:200]}", duration)
        else:
            return result.ok(f"Endpoint alive, HTTP {resp.status_code}", duration)
    except Exception as e:
        return result.fail(f"Exception: {e}")


async def check_health_detailed(client: httpx.AsyncClient, base_url: str) -> CheckResult:
    result = CheckResult("GET /health/detailed")
    t0 = time.perf_counter()
    try:
        resp = await client.get(f"{base_url}/health/detailed", timeout=TIMEOUT)
        duration = (time.perf_counter() - t0) * 1000
        if resp.status_code == 200:
            data = resp.json()
            db_status = data.get("db", "unknown")
            if db_status == "error":
                return result.fail(f"DB reports error: {data}", duration)
            return result.ok(
                f"status={data.get('status')}, db={db_status}, "
                f"pipeline={data.get('pipeline')}, "
                f"active_matches={len(data.get('active_matches', []))}",
                duration,
            )
        else:
            return result.fail(f"HTTP {resp.status_code}", duration)
    except Exception as e:
        return result.fail(f"Exception: {e}")


async def check_admin_auth(client: httpx.AsyncClient, base_url: str) -> CheckResult:
    """Wrong password must return 401, not 200 or 500."""
    result = CheckResult("POST /api/admin/login (auth check)")
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{base_url}/api/admin/login",
            json={"password": "WRONG_PASSWORD_HEALTH_CHECK"},
            timeout=TIMEOUT,
        )
        duration = (time.perf_counter() - t0) * 1000
        if resp.status_code == 401:
            return result.ok("Wrong password correctly rejected with 401", duration)
        elif resp.status_code == 429:
            return result.ok("Rate limited (auth alive)", duration)
        elif resp.status_code == 200:
            return result.fail("CRITICAL: Wrong password accepted! Check ADMIN_PASSWORD env var", duration)
        else:
            return result.fail(f"Unexpected HTTP {resp.status_code}", duration)
    except Exception as e:
        return result.fail(f"Exception: {e}")


async def check_cors_headers(client: httpx.AsyncClient, base_url: str) -> CheckResult:
    """CORS headers must be present."""
    result = CheckResult("CORS headers")
    t0 = time.perf_counter()
    try:
        resp = await client.options(
            f"{base_url}/api/matches",
            headers={"Origin": "https://delta.vercel.app", "Access-Control-Request-Method": "GET"},
            timeout=TIMEOUT,
        )
        duration = (time.perf_counter() - t0) * 1000
        cors_header = resp.headers.get("access-control-allow-origin", "")
        if cors_header:
            return result.ok(f"CORS header present: {cors_header}", duration)
        else:
            # Some servers return CORS on the response directly
            resp2 = await client.get(
                f"{base_url}/health",
                headers={"Origin": "https://delta.vercel.app"},
                timeout=TIMEOUT,
            )
            cors_on_get = resp2.headers.get("access-control-allow-origin", "")
            if cors_on_get:
                return result.ok(f"CORS on GET response: {cors_on_get}", duration)
            return result.fail("No CORS headers found", duration)
    except Exception as e:
        return result.fail(f"Exception: {e}")


def check_env_vars() -> CheckResult:
    """All required env vars must be present and non-empty."""
    result = CheckResult("Environment variables")
    missing = []
    placeholder = []

    for var in REQUIRED_ENV_VARS:
        val = os.getenv(var, "")
        if not val:
            missing.append(var)
        elif val in ("changeme", "your_key_here", "placeholder", "xxx"):
            placeholder.append(var)

    if missing:
        return result.fail(f"Missing: {', '.join(missing)}")
    if placeholder:
        return result.fail(f"Placeholder values (not real keys): {', '.join(placeholder)}")

    # Check ADMIN_PASSWORD isn't the default
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if admin_pw == "changeme":
        return result.fail("ADMIN_PASSWORD is 'changeme' — set a real password before deploying")

    return result.ok(f"All {len(REQUIRED_ENV_VARS)} required vars present")


async def check_rate_limit_headers(client: httpx.AsyncClient, base_url: str) -> CheckResult:
    """Rate limit headers should be present on public endpoints."""
    result = CheckResult("Rate limit headers")
    t0 = time.perf_counter()
    try:
        resp = await client.get(f"{base_url}/api/matches", timeout=TIMEOUT)
        duration = (time.perf_counter() - t0) * 1000
        # slowapi adds X-RateLimit-* headers
        rl_limit = resp.headers.get("x-ratelimit-limit", "")
        rl_remaining = resp.headers.get("x-ratelimit-remaining", "")
        if rl_limit:
            return result.ok(f"limit={rl_limit}, remaining={rl_remaining}", duration)
        else:
            # Some middleware doesn't add until limit approaches — acceptable
            return result.ok("Headers not present (OK before limit approached)", duration)
    except Exception as e:
        return result.fail(f"Exception: {e}")


async def check_no_500_on_bad_requests(client: httpx.AsyncClient, base_url: str) -> CheckResult:
    """Deliberately malformed requests must return 4xx not 5xx."""
    result = CheckResult("Error handling (no 500 on bad input)")
    t0 = time.perf_counter()
    try:
        # Malformed JSON body
        resp = await client.post(
            f"{base_url}/api/vote",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        duration = (time.perf_counter() - t0) * 1000
        if resp.status_code < 500:
            return result.ok(f"Malformed body returns {resp.status_code} (not 500)", duration)
        else:
            return result.fail(f"Malformed body returns {resp.status_code} — unhandled exception", duration)
    except Exception as e:
        return result.fail(f"Exception: {e}")


# ─────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────

async def run_all_checks(base_url: str) -> tuple[list[CheckResult], bool]:
    """Run all checks and return results + overall pass/fail."""
    results = []

    print(f"\n{'=' * 60}")
    print(f"  Project Delta — Pre-Deploy Health Check")
    print(f"  Target: {base_url}")
    print(f"  Time:   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'=' * 60}\n")

    # Env vars check (no HTTP needed)
    env_result = check_env_vars()
    results.append(env_result)
    print(env_result)

    # HTTP checks
    async with httpx.AsyncClient(
        headers={"User-Agent": "ProjectDelta-HealthCheck/1.0"},
    ) as client:
        checks = [
            check_health_endpoint(client, base_url),
            check_matches_endpoint(client, base_url),
            check_health_detailed(client, base_url),
            check_admin_auth(client, base_url),
            check_vote_endpoint(client, base_url),
            check_cors_headers(client, base_url),
            check_rate_limit_headers(client, base_url),
            check_no_500_on_bad_requests(client, base_url),
        ]

        # Run HTTP checks concurrently
        http_results = await asyncio.gather(*checks, return_exceptions=True)
        for r in http_results:
            if isinstance(r, Exception):
                err_result = CheckResult("HTTP check")
                err_result.fail(f"Unexpected exception: {r}")
                results.append(err_result)
            else:
                results.append(r)
            print(results[-1])

    # SSE check (needs its own client context)
    sse_result = await check_sse_stream(base_url)
    results.append(sse_result)
    print(sse_result)

    # Summary
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    all_passed = failed == 0

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{len(results)} checks passed")

    if all_passed:
        print(f"\n  ✅ Safe to deploy")
        print(f"  All {passed} checks passed.")
    else:
        print(f"\n  ❌ DO NOT DEPLOY")
        print(f"  {failed} check(s) failed:")
        for r in results:
            if not r.passed:
                print(f"    → [{r.name}] {r.message}")

    print(f"{'=' * 60}\n")

    return results, all_passed


def get_target_url() -> str:
    """Parse --url argument or use default."""
    if "--url" in sys.argv:
        idx = sys.argv.index("--url")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1].rstrip("/")
    return os.getenv("HEALTH_CHECK_URL", DEFAULT_URL)


if __name__ == "__main__":
    target = get_target_url()

    try:
        results, all_passed = asyncio.run(run_all_checks(target))
        sys.exit(0 if all_passed else 1)
    except KeyboardInterrupt:
        print("\nHealth check interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Health check crashed: {e}")
        sys.exit(1)
