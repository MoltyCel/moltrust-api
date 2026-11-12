#!/usr/bin/env python3
"""MELD-BERICHT criterion 5: deliver once what was filed instead of sent.

From 07.11.2026 11:26:44Z (deploy of NOTIFY-4, #1032) to the deploy of
MELD-BERICHT, News Scout and Content-Scout went to the daily filing
~/reports/<date>.md (app/ablage.py) instead of Telegram. This reads those
sections and sends them as ONE message, with date and time per run, through
app/notify.py (art scout-bericht, chunked by Telegram's length limit).

One shot: a delivered catch-up writes MARKE, and a second run refuses. Without
--senden it prints the message and sends nothing.

    venv/bin/python scripts/meld_bericht_nachreichung.py           # show
    venv/bin/python scripts/meld_bericht_nachreichung.py --senden  # deliver once
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import ablage, notify  # noqa: E402

AB = dt.datetime(2026, 11, 7, 11, 26, 44, tzinfo=dt.timezone.utc)
ART = "scout-bericht"
# report name: words in the first line of its filed text
BERICHTE = {"News Scout": "MolTrust News Scout", "Content-Scout": "Content-Scout:"}
MARKE = Path(os.path.expanduser("~/selftest/meld-bericht-nachreichung.json"))

_KOPF = re.compile(r"^## (\d{2}):(\d{2}):(\d{2})Z · ([\w-]+) · (\w+)")
_DATEI = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


def laeufe(verzeichnis: str, bis: dt.datetime) -> list[dict]:
    """Filed sections of the ordered reports with AB <= time < bis, oldest first."""
    out = []
    for datei in sorted(Path(verzeichnis).glob("*.md")):
        m = _DATEI.match(datei.name)
        if not m:
            continue
        tag = dt.date(*map(int, m.groups()))
        kopf, zeilen = None, []

        def abschliessen():
            if kopf is None:
                return
            h, mi, s, art, _kanal = kopf
            ts = dt.datetime(tag.year, tag.month, tag.day, int(h), int(mi), int(s),
                             tzinfo=dt.timezone.utc)
            text = "\n".join(zeilen).strip()
            erste = next((z for z in text.splitlines() if z.strip()), "")
            name = next((n for n, w in BERICHTE.items() if w in erste), None)
            if art == ART and name and AB <= ts < bis:
                out.append({"ts": ts, "name": name, "text": text})

        for zeile in datei.read_text(encoding="utf-8").splitlines():
            k = _KOPF.match(zeile)
            if k:
                abschliessen()
                kopf, zeilen = k.groups(), []
            elif kopf is not None:
                zeilen.append(zeile)
        abschliessen()
    return sorted(out, key=lambda r: r["ts"])


def nachricht(rs: list[dict]) -> str:
    namen = " und ".join(sorted({r["name"] for r in rs}))
    teile = [f"Nachreichung: {len(rs)} Läufe von {namen}, seit 07.11. 11:26Z "
             f"abgelegt statt gesendet (ops MELD-BERICHT)."]
    for r in rs:
        teile.append(f"\n— {r['ts']:%d.%m. %H:%M}Z · {r['name']}\n{r['text']}")
    return "\n".join(teile)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--senden", action="store_true")
    ap.add_argument("--bis", help="ISO time, default now (UTC)")
    a = ap.parse_args(argv)
    bis = dt.datetime.fromisoformat(a.bis) if a.bis else dt.datetime.now(dt.timezone.utc)
    rs = laeufe(ablage.verzeichnis(), bis)
    for r in rs:
        print(f"{r['ts']:%Y-%m-%d %H:%M:%S}Z {r['name']}")
    print(f"{len(rs)} Läufe")
    if not rs:
        return 1
    text = nachricht(rs)
    if not a.senden:
        print("\n" + text)
        return 0
    if MARKE.exists():
        print(f"schon nachgereicht: {MARKE.read_text(encoding='utf-8').strip()}")
        return 3
    ok = notify.send_telegram(text, channel=notify.WORKLOG, chunk=True, art="scout-bericht")
    print(f"gesendet: {ok}, Grund: {notify.letzter_grund() or '-'}")
    if not ok:
        return 1
    MARKE.parent.mkdir(parents=True, exist_ok=True)
    MARKE.write_text(json.dumps({
        "gesendet": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "laeufe": [f"{r['ts']:%Y-%m-%dT%H:%M:%S}Z {r['name']}" for r in rs],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
