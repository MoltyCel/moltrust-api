"""Which ops job owns a standing finding (ops NOTIFY-1 criterion 3).

One place, config/befund_jobs.json, read by the selftest daily report and by
the web-root watch's message text (WRW-3). A finding that matches no entry has
no owner, and the report says so instead of leaving it unnamed.

An entry may carry "lars": the one thing Lars has to do for the finding, with
its link. The report puts it in its first line, "Lars muss: ..." (Lars,
2026-10-24); a finding without it needs nothing from him.
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
        if e.get("lars") and "https://" not in e["lars"]:
            raise ValueError(f"befund_jobs: Handgriff fuer Lars ohne Link: {e}")
        out.append({**e, "_re": re.compile(e["muster"])})
    return out


def eintrag(text: str, path: pathlib.Path | None = None) -> dict | None:
    """The entry that owns the finding with this message text, or None."""
    for e in entries(path):
        if e["_re"].search(text or ""):
            return e
    return None


def owner(text: str, path: pathlib.Path | None = None) -> str | None:
    """The job that owns the finding with this message text, or None."""
    e = eintrag(text, path)
    return e["job"] if e else None
