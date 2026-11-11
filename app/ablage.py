"""The filing for messages that do not go to Telegram (ops NOTIFY-4 criterion 3).

One Markdown file per UTC day, ~/reports/<YYYY-MM-DD>.md. Each message is one
section: time, kind, channel, and the text without Telegram markup. The consoles
and the coordinator read it; Lars does not get it.

The directory can be redirected with MOLTRUST_REPORTS_DIR, and tests do so
(tests/conftest.py): a test run must not write into the real filing.

A write that fails returns False. notify then reports the message as not
handled, so a sender that retries does so, and the selftest digest does not
ping healthchecks (criterion 5: the ping follows the filing, not the send).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import html as _html
import logging
import os
import re

_logger = logging.getLogger("moltrust.ablage")

_TAG = re.compile(r"<[^>]+>")


def verzeichnis() -> str:
    """Resolved on every call: a test sets the environment late."""
    return (os.environ.get("MOLTRUST_REPORTS_DIR", "").strip()
            or os.path.expanduser("~/reports"))


def pfad(tag: dt.date) -> str:
    return os.path.join(verzeichnis(), f"{tag.isoformat()}.md")


def klartext(text: str) -> str:
    """Telegram HTML to plain text: tags removed, entities resolved."""
    return _html.unescape(_TAG.sub("", str(text or ""))).strip()


def ablegen(art: str, kanal: str, text: str, *, now: dt.datetime | None = None,
            vermerk: str = "") -> bool:
    """Append one message to today's file. True when it is on disk."""
    now = now or dt.datetime.now(dt.timezone.utc)
    p = pfad(now.date())
    kopf = f"## {now:%H:%M:%S}Z · {art} · {kanal}"
    if vermerk:
        kopf += f" · {vermerk}"
    abschnitt = f"\n{kopf}\n\n{klartext(text)}\n"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        neu = not os.path.exists(p)
        with open(p, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            if neu:
                fh.write(f"# Ablage {now:%d.%m.%Y} (UTC)\n\n"
                         "Meldungen ohne Handgriff fuer Lars, nicht nach Telegram "
                         "gesendet (ops NOTIFY-4). Kurzgefasste HANDELN-Meldungen "
                         "stehen hier mit vollem Text.\n")
            fh.write(abschnitt)
        os.chmod(p, 0o600)
    except OSError as e:
        _logger.warning("ablage: nicht schreibbar: %s", type(e).__name__)
        return False
    return True
