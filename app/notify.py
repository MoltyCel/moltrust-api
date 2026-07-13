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


def _resolve_flag() -> str:
    """MOLTRUST_NOTIFY from os.environ, else a fallback read of ~/.moltrust_secrets."""
    v = os.environ.get(_FLAG, "")
    if v:
        return v
    if _FLAG in _ENV_CACHE:
        return _ENV_CACHE[_FLAG]
    resolved = ""
    try:
        path = os.environ.get("MOLTRUST_SECRETS_FILE", os.path.expanduser("~/.moltrust_secrets"))
        with open(path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(_FLAG + "="):
                    resolved = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        resolved = ""
    _ENV_CACHE[_FLAG] = resolved
    return resolved


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


def send_telegram(text: str, *, parse_mode: str | None = None, chunk: bool = False,
                  timeout: int = 15) -> bool:
    """Full gated sender for simple callers. Best-effort; never raises."""
    if not telegram_allowed("notify.send_telegram"):
        return False
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        _logger.warning("notify.send_telegram: token/chat missing")
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
            _logger.warning("notify.send_telegram failed: %s", type(e).__name__)
            ok = False
    return ok
