#!/usr/bin/env python3
"""Woechentlicher Fehlerpfad-Test; meldet, wenn ein erklaerter Alarm nicht feuert.

Ersetzt Crontab-Zeile 213. Dort stand:

    python -m pytest tests/failure_paths.py -q >> log 2>&1
      || (set -a; . secrets; set +a; curl ... "text=ALARM: Fehlerpfad-Test rot…")

Dasselbe hier, ueber notify. Der Testlauf schreibt weiter in dasselbe Log, und
sein Exitcode wird weitergegeben.
"""
import os
import subprocess
import sys

# Drei Ebenen: diese Datei liegt in ops/cron/, nicht in scripts/.
WURZEL = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, WURZEL)

from app import notify  # noqa: E402

LOG = os.path.join(WURZEL, "logs", "failure_paths.log")
# sys.executable, nicht venv/bin/python: ein venv neben der Datei
# vorauszusetzen scheitert im Worktree, und ein Unterprozess mit einem
# anderen Python hat andere Pakete als sein Aufrufer.
PROGRAMM = [sys.executable, "-m", "pytest",
            "tests/failure_paths.py", "-q"]
TEXT = ("ALARM: Fehlerpfad-Test rot. Ein deklarierter Alarm feuert nicht. "
        "logs/failure_paths.log")


def main() -> int:
    umgebung = dict(os.environ)
    umgebung["PYTHONPATH"] = WURZEL
    try:
        p = subprocess.run(PROGRAMM, capture_output=True, text=True,
                           timeout=1800, cwd=WURZEL, env=umgebung)
    except (OSError, subprocess.SubprocessError) as e:
        notify.send_telegram(
            f"ALARM: Fehlerpfad-Test nicht ausfuehrbar — "
            f"{type(e).__name__}: {e}", channel=notify.ALERTS)
        return 1

    try:
        # exist_ok, und im Rumpf: eine Datei, die beim Import ein Verzeichnis
        # anlegt, handelt beim Import.
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write((p.stdout or "") + (p.stderr or ""))
    except OSError as e:
        print(f"Log nicht schreibbar: {type(e).__name__}")

    if p.returncode == 0:
        print("Fehlerpfade gruen")
        return 0

    if notify.send_telegram(TEXT, channel=notify.ALERTS):
        print(f"Alarm gemeldet (pytest {p.returncode})")
    else:
        print(f"nicht gesendet: {notify.letzter_grund() or 'unbekannt'}")
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
