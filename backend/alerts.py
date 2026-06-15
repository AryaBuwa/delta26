"""
alerts.py — Telegram Bot Alert System
Three levels: INFO (log only), WARNING (one message), CRITICAL (every 10s until STOP)
"""

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import telegram
from telegram import Bot
from telegram.error import TelegramError
from loguru import logger

import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "") # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# ─────────────────────────────────────────────
# Alert levels
# ─────────────────────────────────────────────

class AlertLevel(str, Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


# ─────────────────────────────────────────────
# Internal state
# ─────────────────────────────────────────────

_bot: Optional[Bot] = None
_critical_tasks: dict[str, asyncio.Task] = {}   # key → repeating task
_silenced_keys: set[str] = set()                 # keys where user replied STOP


def _get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


# ─────────────────────────────────────────────
# Core send (fire-and-forget safe)
# ─────────────────────────────────────────────

async def _send(text: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    try:
        bot = _get_bot()
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="HTML",
        )
        return True
    except TelegramError as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

async def send_info(message: str, context: Optional[dict] = None) -> None:
    """
    INFO level — log to file + Google Sheets only. No Telegram ping.
    Use for: single source failed (9+ still working), minor issues.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_line = f"[INFO] {ts} | {message}"
    if context:
        log_line += f" | {context}"
    logger.info(log_line)
    # sheets.py will read from logger or be called explicitly by pipeline.py


async def send_warning(
    message: str,
    match_info: Optional[str] = None,
    context: Optional[dict] = None,
) -> None:
    """
    WARNING level — one Telegram message.
    Use for: 3+ sources failing, Groq at 80%, Render CPU >80%,
             retrain >10min, Google Sheets write failed.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = f"⚠️ <b>WARNING</b>\n{message}"
    if match_info:
        body += f"\nMatch: {match_info}"
    if context:
        for k, v in context.items():
            body += f"\n{k}: {v}"
    body += f"\n<i>{ts}</i>"

    logger.warning(f"WARNING | {message} | match={match_info} | ctx={context}")
    await _send(body)


async def send_critical(
    key: str,
    message: str,
    match_info: Optional[str] = None,
    last_good_state: Optional[str] = None,
    down_since: Optional[datetime] = None,
    action: Optional[str] = None,
) -> None:
    """
    CRITICAL level — repeats every 10 seconds until silenced.
    Use for: all sources failing, all Groq keys exhausted,
             DB write failing, app returning 500s.
    Call silence_critical(key) or have user reply STOP to stop.
    """
    if key in _silenced_keys:
        logger.info(f"CRITICAL {key} suppressed (silenced)")
        return

    # Cancel any existing task for this key before starting fresh
    await _cancel_critical(key)

    async def _repeat():
        while key not in _silenced_keys:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            down_for = ""
            if down_since:
                secs = int((datetime.now(timezone.utc) - down_since).total_seconds())
                mins, s = divmod(secs, 60)
                down_for = f"\nDown for: {mins}m {s}s"

            body = (
                f"🔴 <b>CRITICAL</b>: {message}"
                f"{f'{chr(10)}Match: {match_info}' if match_info else ''}"
                f"{f'{chr(10)}Last good state: {last_good_state}' if last_good_state else ''}"
                f"{down_for}"
                f"{f'{chr(10)}Action needed: {action}' if action else ''}"
                f"\nReply <b>STOP</b> to silence"
                f"\n<i>{ts}</i>"
            )
            logger.critical(f"CRITICAL | key={key} | {message}")
            await _send(body)
            await asyncio.sleep(10)

    task = asyncio.create_task(_repeat())
    _critical_tasks[key] = task
    logger.critical(f"CRITICAL alert started: key={key} | {message}")


async def silence_critical(key: str) -> None:
    """Silence a repeating CRITICAL alert by key."""
    _silenced_keys.add(key)
    await _cancel_critical(key)
    await _send(f"✅ CRITICAL alert <b>{key}</b> silenced.")
    logger.info(f"CRITICAL alert silenced: {key}")


async def _cancel_critical(key: str) -> None:
    task = _critical_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def clear_silence(key: str) -> None:
    """Re-enable a previously silenced critical alert key."""
    _silenced_keys.discard(key)


# ─────────────────────────────────────────────
# Telegram update polling (STOP command handler)
# ─────────────────────────────────────────────

_last_update_id: int = 0


async def poll_stop_commands() -> None:
    """
    Long-running coroutine. Polls Telegram for incoming messages.
    If user replies STOP, silences all active critical alerts.
    Run as a background task in main.py via asyncio.create_task().
    """
    global _last_update_id
    bot = _get_bot()
    logger.info("Telegram STOP command poller started.")

    while True:
        try:
            updates = await bot.get_updates(
                offset=_last_update_id + 1,
                timeout=30,
                allowed_updates=["message"],
            )
            for update in updates:
                _last_update_id = update.update_id
                if update.message and update.message.text:
                    text = update.message.text.strip().upper()
                    if text == "STOP":
                        # Silence all active critical alerts
                        keys = list(_critical_tasks.keys())
                        for k in keys:
                            await silence_critical(k)
                        if not keys:
                            await _send("ℹ️ No active CRITICAL alerts to silence.")
                        logger.info("STOP received via Telegram — all criticals silenced.")
        except TelegramError as e:
            logger.warning(f"Telegram poll error: {e}")
            await asyncio.sleep(15)
        except Exception as e:
            logger.error(f"Unexpected poll error: {e}")
            await asyncio.sleep(15)
        else:
            await asyncio.sleep(2)


# ─────────────────────────────────────────────
# Specific pre-built alert helpers
# (called by pipeline.py for common scenarios)
# ─────────────────────────────────────────────

async def alert_sources_failing(
    failing_count: int,
    total: int,
    match_info: str,
    failed_sources: list[str],
) -> None:
    """3+ sources failing simultaneously → WARNING."""
    await send_warning(
        message=f"{failing_count}/{total} sources failing",
        match_info=match_info,
        context={"Failed": ", ".join(failed_sources)},
    )


async def alert_all_sources_down(
    match_info: str,
    last_good_state: str,
    down_since: datetime,
) -> None:
    """All sources failing → CRITICAL."""
    await send_critical(
        key="all_sources_down",
        message="All sources failing",
        match_info=match_info,
        last_good_state=last_good_state,
        down_since=down_since,
        action="check fetcher.py",
    )


async def alert_groq_limit(key_name: str, pct_used: float) -> None:
    """Groq key at 80%+ daily limit → WARNING."""
    await send_warning(
        message=f"Groq {key_name} at {pct_used:.0f}% daily token limit",
        context={"Remaining": f"{100 - pct_used:.0f}%"},
    )


async def alert_groq_exhausted(match_info: str, down_since: datetime) -> None:
    """All Groq keys exhausted → CRITICAL."""
    await send_critical(
        key="groq_exhausted",
        message="All Groq keys exhausted",
        match_info=match_info,
        down_since=down_since,
        action="check Groq quotas or wait for reset",
    )


async def alert_db_failing(error: str, down_since: datetime) -> None:
    """Database write failing → CRITICAL."""
    await send_critical(
        key="db_failing",
        message="Database write failing",
        last_good_state=f"Error: {error}",
        down_since=down_since,
        action="check Supabase dashboard",
    )


async def alert_render_cpu(cpu_pct: float) -> None:
    """Render CPU above 80% → WARNING."""
    await send_warning(
        message=f"Render CPU at {cpu_pct:.0f}%",
        context={"Threshold": "80%", "Action": "monitor for degradation"},
    )


async def alert_app_500s(match_info: str, down_since: datetime) -> None:
    """App returning 500 errors → CRITICAL."""
    await send_critical(
        key="app_500s",
        message="App returning 500 errors",
        match_info=match_info,
        down_since=down_since,
        action="check main.py logs on Render",
    )


async def alert_retrain_slow(duration_s: float, match_id: str) -> None:
    """Retraining job took >10 minutes → WARNING."""
    await send_warning(
        message=f"Retraining took {duration_s/60:.1f} minutes (threshold: 10min)",
        context={"match_id": match_id},
    )


async def alert_sheets_failed(error: str) -> None:
    """Google Sheets write failed (queued for retry) → WARNING."""
    await send_warning(
        message="Google Sheets write failed — queued for retry",
        context={"Error": error},
    )


async def alert_retrain_result(
    match_id: str,
    version: int,
    accuracy_before: float,
    accuracy_after: float,
    improvement: float,
    deployed: bool,
    threshold: float,
    duration_s: float,
) -> None:
    """Send retraining result notification."""
    emoji = "✅" if deployed else "⏭️"
    status = "DEPLOYED" if deployed else f"SKIPPED (< {threshold:.0%} threshold)"
    text = (
        f"{emoji} <b>Retrain complete</b>\n"
        f"Match: {match_id}\n"
        f"Model v{version}\n"
        f"Accuracy: {accuracy_before:.1%} → {accuracy_after:.1%} "
        f"({'+' if improvement >= 0 else ''}{improvement:.1%})\n"
        f"Status: {status}\n"
        f"Duration: {duration_s:.0f}s"
    )
    await _send(text)


async def alert_deploy_window(safe: bool, reason: str) -> None:
    """Deployment window suggestion."""
    if safe:
        text = f"✅ Safe to deploy now.\n{reason}"
    else:
        text = f"⏳ Wait. {reason}"
    await _send(text)


async def alert_daily_post(linkedin_draft: str, x_draft: str) -> None:
    """Send today's social media drafts to Telegram."""
    text = (
        "📝 <b>Today's post drafts</b>\n\n"
        "─── LinkedIn ───\n"
        f"{linkedin_draft[:800]}{'...' if len(linkedin_draft) > 800 else ''}\n\n"
        "─── X ───\n"
        f"{x_draft[:500]}{'...' if len(x_draft) > 500 else ''}"
    )
    await _send(text)


async def alert_bmac(amount: float, supporter: str) -> None:
    """Buy Me A Coffee notification."""
    await _send(f"☕ <b>New supporter!</b>\n{supporter} — ${amount:.2f}")


async def alert_tally_submission(data: dict) -> None:
    """Tally form submission webhook."""
    text = "📋 <b>New Tally submission</b>\n"
    for k, v in list(data.items())[:8]:
        text += f"{k}: {v}\n"
    await _send(text)


async def alert_system_down(down_since: datetime) -> None:
    """System marked DOWN for users → CRITICAL."""
    await send_critical(
        key="system_down",
        message="System marked DOWN for users",
        down_since=down_since,
        action="check all layers immediately",
    )


async def alert_system_restored() -> None:
    """System back up — cancel CRITICAL and notify."""
    for key in ["system_down", "all_sources_down", "app_500s", "db_failing"]:
        if key in _critical_tasks:
            await silence_critical(key)
            clear_silence(key)
    await _send("🟢 <b>System restored</b> — all services operational.")
