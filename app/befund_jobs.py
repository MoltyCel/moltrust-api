"""Which ops job owns a standing finding (ops NOTIFY-1 criterion 3).

One place, config/befund_jobs.json, read by the selftest daily report and by
the web-root watch's message text (WRW-3). A finding that matches no entry has
no owner, and the report says so instead of leaving it unnamed.
"""
from __future__ import annotations

import json
import pathlib
import re

PATH = pathlib.Path(__file__).resolve().parent.parent / "config" / "befund_jobs.json"


def entries(path: pathlib.Path | None = None) -> list[dict]:
    data = json.loads((path or PATH).read_text(encoding="utf-8"))
    out = []
    for e in data.get("eintraege") or []:
        if not e.get("muster") or not e.get("job"):
            raise ValueError(f"befund_jobs: Eintrag ohne muster oder job: {e}")
        out.append({**e, "_re": re.compile(e["muster"])})
    return out


def owner(text: str, path: pathlib.Path | None = None) -> str | None:
    """The job that owns the finding with this message text, or None."""
    for e in entries(path):
        if e["_re"].search(text or ""):
            return e["job"]
    return None
