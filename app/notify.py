"""Shared Telegram production-gate + a best-effort gated sender.

THE single source of truth for "may a Telegram message leave this process?".
Every Telegram sender in the codebase (agents, scripts, monitors, workers,
app.budget) routes its real-send path through `telegram_allowed()` so nothing
fires outside production — local dev, CI and the test suite only ever log.

    active gate:  os.environ["MOLTRUST_ENV"].lower() == "production"

`send_telegram()` is a full, self-contained gated sender for simple callers
that just want to push a string. Callers with their own bespoke send/format/
chunk logic keep it and only prepend the `telegram_allowed()` gate.
"""
from __future__ import annotations

import logging
import os

import requests

_logger = logging.getLogger("moltrust.notify")

# Telegram hard-caps a message at 4096 chars; 3900 leaves headroom for any
# per-chunk prefix a caller might add.
_CHUNK_LIMIT = 3900


_ENV_CACHE: dict[str, str] = {}


def _resolve_env() -> str:
    """Resolve MOLTRUST_ENV robustly across the codebase's loader styles.

    The API service gets it via systemd EnvironmentFile and the sourcing crons
    via `set -a; source ~/.moltrust_secrets` — both land it in os.environ. But
    several standalone scripts load secrets into a *dict* (their own
    `load_secrets()`), never touching os.environ, so a plain os.environ read
    would wrongly suppress their prod alerts. Fall back to reading the single
    MOLTRUST_ENV line from the secrets file so every process resolves the same
    value. Cached; failure is treated as non-production (fail-safe = suppress).
    """
    v = os.environ.get("MOLTRUST_ENV", "")
    if v:
        return v
    if "v" in _ENV_CACHE:
        return _ENV_CACHE["v"]
    resolved = ""
    try:
        secrets_path = os.environ.get("MOLTRUST_SECRETS_FILE", os.path.expanduser("~/.moltrust_secrets"))
        with open(secrets_path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("MOLTRUST_ENV="):
                    resolved = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        resolved = ""
    _ENV_CACHE["v"] = resolved
    return resolved


def telegram_allowed(context: str = "", logger=None) -> bool:
    """THE shared production-gate. Returns True iff MOLTRUST_ENV == production.

    Outside production it logs a suppression notice (via the passed logger,
    else the module logger) and returns False, so every caller can gate its
    real-send path with a single call. Resolution is os.environ first, then a
    fallback read of ~/.moltrust_secrets (see _resolve_env) so dict-loader
    scripts aren't wrongly suppressed.
    """
    if _resolve_env().lower() == "production":
        return True
    (logger or _logger).info(
        "telegram suppressed (MOLTRUST_ENV != production): %s", context
    )
    return False


def _chunk(text: str, limit: int = _CHUNK_LIMIT) -> list[str]:
    """Split on line boundaries into <=limit chunks; hard-split over-long lines."""
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
    return parts or [""]


def send_telegram(
    text: str,
    *,
    parse_mode: str | None = None,
    chunk: bool = False,
    timeout: int = 15,
) -> bool:
    """Best-effort gated Telegram send for simple callers.

    Gated by `telegram_allowed()` first (returns False outside production).
    Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the env (returns False if
    either is missing). Chunks at 3900 chars on line boundaries when
    `chunk=True`. Never raises — returns True only if every part sent 200.
    """
    if not telegram_allowed("notify.send_telegram"):
        return False

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False

    parts = _chunk(text) if chunk else [text]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for part in parts:
        payload = {"chat_id": chat_id, "text": part}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            ok = ok and resp.status_code == 200
        except Exception as e:  # best-effort: never raise
            _logger.warning("notify.send_telegram failed: %s", e)
            ok = False
    return ok
