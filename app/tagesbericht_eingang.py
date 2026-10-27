"""Eingang des Tagesberichts: Messberichte warten hier auf die Sammelmeldung.

Lars, 23.10.2026: "Messberichte gehen nicht nach Telegram, auch nicht als
'Bericht'. Die Digest-Messung ist eine Messung und gehoert in den
Tagesbericht." (ops NOTIFY-3)

Ein Messbericht ruft deshalb nicht notify.send_telegram auf, sondern legt
seinen Text hier ab. Die Sammelmeldung des Selbsttests (scripts/selftest.py,
send_digest, 08:00 und 20:00 UTC) liest den Eingang, haengt die Zeilen an und
leert ihn erst, wenn Telegram die Meldung angenommen hat. Wird sie nicht
zugestellt, bleibt alles stehen und kommt mit der naechsten (NOTIFY-3
Kriterium 4). Ein Eintrag geht dabei eher zweimal raus als keinmal: scheitert
das Leeren nach der Zustellung, steht er im naechsten Bericht noch einmal.

Geleert wird nach Kennung, nicht der ganze Eingang. Was ein Messbericht
ablegt, waehrend die Sammelmeldung unterwegs ist, war nicht in ihr und bleibt
fuer die naechste liegen.

Die Datei liegt neben dem Sendeprotokoll (MOLTRUST_NOTIFY_STATE_DIR, sonst
~/selftest). Tests lenken das Verzeichnis um und fassen den echten Eingang
nie an.

Aus der Shell:

    echo "..." | python -m app.tagesbericht_eingang --quelle <name> --stdin

Die Sammelmeldung geht ohne parse_mode raus. Ein Messbericht, der fuer
Telegram in HTML gebaut ist, legt mit html=True (Shell: --html) ab; dann
stehen im Eingang Text und Zeilen, keine Tags (ops NOTIFY-3c).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import html as _html
import json
import re
import logging
import os
import tempfile
import uuid

_logger = logging.getLogger("moltrust.tagesbericht_eingang")

DATEI = "tagesbericht-eingang.jsonl"


def pfad() -> str:
    """Der Eingang, jedes Mal neu aufgeloest: ein Test setzt die Umgebung spaet."""
    verz = os.environ.get("MOLTRUST_NOTIFY_STATE_DIR", "").strip() \
        or os.path.expanduser("~/selftest")
    return os.path.join(verz, DATEI)


def _schloss(path: str):
    """Sperrdatei neben dem Eingang. Ablegen und Leeren schliessen sich aus."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path + ".lock", "a+")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


_TAG = re.compile(r"<[^>]+>")


def ohne_html(text: str) -> str:
    """Telegram-HTML zu Klartext: Tags weg, Entitaeten aufgeloest."""
    return _html.unescape(_TAG.sub("", str(text or "")))


def ablegen(quelle: str, text: str, *, path: str | None = None,
            now: dt.datetime | None = None, html: bool = False) -> bool:
    """Einen Messbericht fuer den naechsten Tagesbericht ablegen.

    True, wenn er in der Datei steht. False bei leerem Text, fehlender Quelle
    oder einem Schreibfehler; der Aufrufer behandelt das wie eine gescheiterte
    Zustellung und versucht es beim naechsten Lauf wieder.
    """
    quelle = str(quelle or "").strip()
    text = (ohne_html(text) if html else str(text or "")).strip()
    if not quelle or not text:
        _logger.warning("tagesbericht_eingang: leerer Text oder keine Quelle")
        return False
    path = pfad() if path is None else path
    now = now or dt.datetime.now(dt.timezone.utc)
    zeile = {"id": uuid.uuid4().hex, "ts": now.isoformat(timespec="seconds"),
             "quelle": quelle, "text": text}
    try:
        with _schloss(path):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
            os.chmod(path, 0o600)
    except OSError as e:
        _logger.warning("tagesbericht_eingang: nicht schreibbar: %s", type(e).__name__)
        return False
    return True


def _eintrag(roh: str) -> dict:
    try:
        d = json.loads(roh)
    except ValueError:
        d = None
    if isinstance(d, dict) and d.get("id") and d.get("text"):
        return d
    # Eine kaputte Zeile geht einmal mit raus und wird dann geleert. Still
    # liegen lassen hiesse, dass sie in keinem Bericht steht; ohne Kennung
    # stuende sie in jedem.
    return {"id": "roh-" + hashlib.sha256(roh.encode()).hexdigest()[:16],
            "ts": "", "quelle": "?", "text": "unlesbare Zeile im Eingang: " + roh[:200]}


def lesen(*, path: str | None = None) -> list[dict]:
    """Alle wartenden Eintraege, aelteste zuerst. Fehlt die Datei: keine."""
    path = pfad() if path is None else path
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [_eintrag(z.rstrip("\n")) for z in fh if z.strip()]


def leeren(ids, *, path: str | None = None) -> int:
    """Die zugestellten Eintraege entfernen, alle anderen behalten.

    Gibt die Zahl der entfernten Eintraege zurueck. Die Datei wird ueber eine
    temporaere Datei ersetzt, damit ein Abbruch mitten im Schreiben keinen
    halben Eingang hinterlaesst.
    """
    ids = set(ids)
    path = pfad() if path is None else path
    if not ids or not os.path.exists(path):
        return 0
    with _schloss(path):
        with open(path, encoding="utf-8") as fh:
            zeilen = [z for z in fh if z.strip()]
        bleibt = [z for z in zeilen if _eintrag(z.rstrip("\n"))["id"] not in ids]
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".eingang-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.writelines(bleibt)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return len(zeilen) - len(bleibt)


def zeilen(eintraege: list[dict]) -> list[str]:
    """Die Zeilen fuer den Tagesbericht. Kein Eintrag: keine Zeile."""
    if not eintraege:
        return []
    out = [f"Messberichte seit dem letzten Bericht ({len(eintraege)}):"]
    for e in eintraege:
        try:
            wann = dt.datetime.fromisoformat(str(e.get("ts"))).strftime("%d.%m. %H:%MZ")
        except ValueError:
            wann = "?"
        out.append(f"— {e.get('quelle') or '?'}, {wann}:")
        out.extend(str(e.get("text") or "").splitlines())
    return out


def _cli(argv=None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="python -m app.tagesbericht_eingang",
        description="Einen Messbericht in den Eingang des Tagesberichts legen.")
    p.add_argument("--quelle", required=True, help="Name des Senders, etwa das Skript.")
    p.add_argument("--text")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--html", action="store_true",
                   help="Der Text ist Telegram-HTML; Tags werden entfernt.")
    a = p.parse_args(argv)
    if a.stdin == (a.text is not None):
        print("eingang: entweder --text oder --stdin", file=sys.stderr)
        return 2
    text = sys.stdin.read() if a.stdin else a.text
    return 0 if ablegen(a.quelle, text, html=a.html) else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
