"""Decisions for Lars, bundled into one Telegram message a day (ops MELD-1 criterion 3).

Lars, 27.10.2026: decisions at most once a day, bundled in ONE message; per
point two sentences, a recommendation, the answer format "1A 2ja ..." and the
way to answer (the Console tab).

A sender with a decision does not send it. It queues one point here, either
directly with vormerken() or by sending a message whose art is bundled
(app/meldungsarten.py, zustellung "buendel"); notify then queues it. The
selftest digest at 08:00 UTC calls senden(): one message with every queued
point, numbered. The full texts go to the filing (app/ablage.py) under the
same numbers, so the Console tab that receives "1A" can look up what 1 was.

The queue is emptied by id after the bundle was delivered, as the daily
report inbox is (app/tagesbericht_eingang.py): a point rather comes twice than
not at all. The file lives next to the send log (MOLTRUST_NOTIFY_STATE_DIR,
otherwise ~/selftest); tests redirect it.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import logging
import os
import re
import uuid

from app import ablage

_logger = logging.getLogger("moltrust.entscheidungen")

DATEI = "entscheidungen.jsonl"
ART = "entscheidungs-buendel"
ANTWORTWEG = "Antwort im Console-Tab"

STANDARD_OPTIONEN = {"A": "ein Console-Tab übernimmt", "B": "liegen lassen"}


def pfad() -> str:
    verz = os.environ.get("MOLTRUST_NOTIFY_STATE_DIR", "").strip() \
        or os.path.expanduser("~/selftest")
    return os.path.join(verz, DATEI)


_SATZENDE = re.compile(r"(?<=[.!?])\s+")


def zwei_saetze(text: str) -> str:
    """The first two sentences of a plain text, on one line."""
    flach = " ".join(z.strip() for z in ablage.klartext(text).splitlines() if z.strip())
    saetze = [s for s in _SATZENDE.split(flach) if s]
    out = " ".join(saetze[:2]).strip()
    if out and out[-1] not in ".!?":
        out += "."
    return out


def vormerken(art: str, punkt: str, *, empfehlung: str = "A",
              optionen: dict | None = None, volltext: str = "",
              path: str | None = None, now: dt.datetime | None = None) -> bool:
    """Queue one point for the next bundle. True when it is on disk."""
    roh = punkt
    punkt = zwei_saetze(punkt)
    optionen = dict(optionen or STANDARD_OPTIONEN)
    if not punkt or empfehlung not in optionen:
        _logger.warning("entscheidungen: leerer Punkt oder Empfehlung ohne Option")
        return False
    path = path or pfad()
    now = now or dt.datetime.now(dt.timezone.utc)
    zeile = {"id": uuid.uuid4().hex, "ts": now.isoformat(timespec="seconds"),
             "art": art, "punkt": punkt, "empfehlung": empfehlung,
             "optionen": optionen, "volltext": ablage.klartext(volltext or roh)}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError as e:
        _logger.warning("entscheidungen: nicht schreibbar: %s", type(e).__name__)
        return False
    return True


def lesen(path: str | None = None) -> list[dict]:
    path = path or pfad()
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for roh in fh:
                try:
                    e = json.loads(roh)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("id") and e.get("punkt"):
                    out.append(e)
    except FileNotFoundError:
        return []
    return out


def leeren(ids, path: str | None = None) -> int:
    """Remove the entries with these ids; what was queued meanwhile stays."""
    path = path or pfad()
    ids = set(ids)
    try:
        with open(path, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            zeilen = fh.readlines()
            bleibt = []
            for roh in zeilen:
                try:
                    if json.loads(roh).get("id") in ids:
                        continue
                except ValueError:
                    pass
                bleibt.append(roh)
            fh.seek(0)
            fh.truncate()
            fh.writelines(bleibt)
    except FileNotFoundError:
        return 0
    return len(zeilen) - len(bleibt)


def _antwortformat(n: int) -> str:
    beispiel = " ".join(f"{i}A" for i in range(1, min(n, 2) + 1))
    return f"Format „{beispiel}“"


def buendel_text(eintraege: list[dict], heute: dt.date) -> str:
    """One message: a head line, then one line per point."""
    kopf = (f"Entscheidungen vom {heute:%d.%m.}: {len(eintraege)} "
            f"{'Punkt' if len(eintraege) == 1 else 'Punkte'}. "
            f"{ANTWORTWEG}, {_antwortformat(len(eintraege))}.")
    zeilen = [kopf]
    for i, e in enumerate(eintraege, 1):
        opt = ", ".join(f"{k} {v}" for k, v in e["optionen"].items())
        zeilen.append(f"{i}. {e['punkt']} Empfehlung: {e['empfehlung']}. ({opt})")
    return "\n".join(zeilen)


def senden(now: dt.datetime | None = None, *, path: str | None = None, send=None) -> bool | None:
    """Deliver the bundle. None when nothing is queued, else whether it went out."""
    from app import notify
    now = now or dt.datetime.now(dt.timezone.utc)
    eintraege = lesen(path)
    if not eintraege:
        return None
    text = buendel_text(eintraege, now.date())
    voll = [text, ""] + [f"{i}. [{e['art']}] {e['volltext']}" for i, e in enumerate(eintraege, 1)]
    ablage.ablegen(ART, notify.STATS, "\n".join(voll), now=now, vermerk="Volltexte")
    send = send or notify.send_telegram
    ok = send(text, channel=notify.STATS, art="entscheidungs-buendel")
    if ok:
        leeren({e["id"] for e in eintraege}, path)
    return bool(ok)
