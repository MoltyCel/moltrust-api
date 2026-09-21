"""Shared Telegram notification gate + a best-effort gated sender.

Telegram sending is controlled by its OWN flag, `MOLTRUST_NOTIFY`, decoupled
from `MOLTRUST_ENV` (which governs crypto posture: KMS-only signing in
kms_signer, PQC dev-key disable in dilithium). This split lets you turn alerts
on/off independently of the signing-key enforcement.

    send allowed  <=>  MOLTRUST_NOTIFY in {"1","true","on","yes","enabled","production"}

Resolution reads os.environ first, then falls back to the single MOLTRUST_NOTIFY
line in ~/.moltrust_secrets — so the standalone scripts that load secrets into
their own dict (not os.environ) resolve the same value instead of being wrongly
suppressed. Failure / unset => not allowed (fail-safe = do not send).

Channels
--------
Every message goes to exactly one of four chats:

    stats    periodic numbers nobody has to act on
    alerts   something is broken or needs a decision now
    money    payments, balances, payouts, budget
    worklog  what the agents did: posts, PRs, drafts, reviews, cleanups

One chat carried all four until 2026-09-21, which made the alerts unfindable
between the hourly numbers and meant a payout notice sat in the same scroll as a
draft tweet. `channel=` is a required keyword on every sender so a new call site
has to decide; `tests/test_notify_channels.py` fails the build on one that did
not.

Each channel resolves `TELEGRAM_CHAT_ID_<CHANNEL>` and falls back to the single
`TELEGRAM_CHAT_ID`. An unconfigured split therefore behaves exactly as before
rather than dropping messages, so the code can ship before the chats exist.
"""
from __future__ import annotations

import logging
import os

import requests

_logger = logging.getLogger("moltrust.notify")

_FLAG = "MOLTRUST_NOTIFY"
_TRUE = {"1", "true", "on", "yes", "enabled", "production"}
_CHUNK_LIMIT = 3900  # Telegram hard-caps at 4096; leave headroom.
_ENV_CACHE: dict[str, str] = {}

STATS = "stats"
ALERTS = "alerts"
MONEY = "money"
WORKLOG = "worklog"
CHANNELS = (STATS, ALERTS, MONEY, WORKLOG)


def _resolve(name: str) -> str:
    """`name` from os.environ, else a fallback read of ~/.moltrust_secrets.

    The fallback exists because several standalone scripts load the secrets
    file into a dict of their own and never touch os.environ. Without it they
    would resolve an empty value and suppress themselves.
    """
    v = os.environ.get(name, "")
    if v:
        return v
    if name in _ENV_CACHE:
        return _ENV_CACHE[name]
    resolved = ""
    try:
        path = os.environ.get("MOLTRUST_SECRETS_FILE", os.path.expanduser("~/.moltrust_secrets"))
        with open(path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(name + "="):
                    resolved = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        resolved = ""
    _ENV_CACHE[name] = resolved
    return resolved


def _resolve_flag() -> str:
    """MOLTRUST_NOTIFY from os.environ, else a fallback read of ~/.moltrust_secrets."""
    return _resolve(_FLAG)


def telegram_allowed(context: str = "", logger=None) -> bool:
    """THE shared Telegram gate. True iff MOLTRUST_NOTIFY is a truthy value.

    Decoupled from MOLTRUST_ENV on purpose (see module docstring). Outside an
    enabled state it logs a suppression notice and returns False so every caller
    can gate its real-send path with a single call.
    """
    if _resolve_flag().strip().lower() in _TRUE:
        return True
    (logger or _logger).info(
        "telegram suppressed (MOLTRUST_NOTIFY not enabled): %s", context
    )
    return False


def _chunk(text: str, limit: int = _CHUNK_LIMIT) -> list[str]:
    parts: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(line[:limit])
            line = line[limit:]
        if buf and len(buf) + 1 + len(line) > limit:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


def chat_id_for(channel: str) -> str:
    """The chat a channel posts to, falling back to the undivided chat.

    Falling back rather than failing is deliberate: the routing ships before
    the four chats exist, and a message that would have been delivered
    yesterday must not be dropped because its channel has no id yet.
    """
    if channel not in CHANNELS:
        raise ValueError(f"unknown telegram channel {channel!r}; expected one of {CHANNELS}")
    specific = _resolve(f"TELEGRAM_CHAT_ID_{channel.upper()}").strip()
    if specific:
        return specific
    return _resolve("TELEGRAM_CHAT_ID").strip()


def send_telegram(text: str, *, channel: str, parse_mode: str | None = None,
                  chunk: bool = False, timeout: int = 15) -> bool:
    """Full gated sender for simple callers. Best-effort; never raises."""
    if not telegram_allowed(f"notify.send_telegram[{channel}]"):
        return False
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = chat_id_for(channel)
    if not token or not chat:
        _logger.warning("notify.send_telegram: token/chat missing for channel %s", channel)
        return False
    pieces = _chunk(text) if chunk else [text]
    ok = True
    for piece in pieces:
        data = {"chat_id": chat, "text": piece, "disable_web_page_preview": "true"}
        if parse_mode:
            data["parse_mode"] = parse_mode
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data=data, timeout=timeout)
            ok = ok and (r.status_code == 200)
        except Exception as e:
            _logger.warning("notify.send_telegram[%s] failed: %s", channel, type(e).__name__)
            ok = False
    return ok


def silence_http_request_logs() -> None:
    """Stop the HTTP clients from logging request URLs at INFO.

    httpx logs `HTTP Request: POST <url> "HTTP/1.1 200 OK"` at INFO, and a
    Telegram send puts the bot token in the path. On 2026-09-20 that had written
    the token into logs/watchdog.log 220 times in clear text, because watchdog.py
    combines httpx with basicConfig(level=INFO). Any module that sends through
    this gate is about to put a secret in a URL, so it calls this first.

    Errors still surface: this lowers INFO chatter, not warnings.
    """
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
