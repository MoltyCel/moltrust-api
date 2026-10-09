#!/usr/bin/env python3
"""Moltbook-Wachhund: meldet, wenn moltbook_writers.py --check scheitert.

Ersetzt Crontab-Zeile 182. Dort stand:

    OUT=$(./venv/bin/python scripts/moltbook_writers.py --check 2>&1)
      || curl ... "text=Moltbook-Wachhund: nicht erklaerter Schreiber. $OUT"

Also: nur bei Fehlschlag melden, mit der Ausgabe im Text. Dasselbe hier, ueber
notify. Der Exitcode des Programms wird weitergegeben — die Shell-Fassung
verschluckte ihn im `||`, sodass cron einen gescheiterten Lauf nicht sah.
"""
import os
import subprocess
import sys

# Drei Ebenen: diese Datei liegt in ops/cron/, nicht in scripts/.
WURZEL = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, WURZEL)

from app import notify  # noqa: E402

# sys.executable, nicht venv/bin/python: ein venv neben der Datei
# vorauszusetzen scheitert im Worktree, und ein Unterprozess mit einem
# anderen Python hat andere Pakete als sein Aufrufer.
PROGRAMM = [sys.executable,
            os.path.join(WURZEL, "scripts", "moltbook_writers.py"), "--check"]


def main() -> int:
    try:
        p = subprocess.run(PROGRAMM, capture_output=True, text=True,
                           timeout=600, cwd=WURZEL)
    except (OSError, subprocess.SubprocessError) as e:
        notify.send_telegram(
            f"Moltbook-Wachhund: Pruefung nicht ausfuehrbar — "
            f"{type(e).__name__}: {e}", channel=notify.STATS)
        return 1

    if p.returncode == 0:
        print("Pruefung gruen — nichts zu melden")
        return 0

    # 2>&1 der alten Zeile: beides zusammen in den Text.
    ausgabe = ((p.stdout or "") + (p.stderr or "")).strip()
    text = f"Moltbook-Wachhund: nicht erklaerter Schreiber. {ausgabe}"
    if notify.send_telegram(text, channel=notify.STATS):
        print(f"gemeldet (Exitcode {p.returncode})")
    else:
        print(f"nicht gesendet: {notify.letzter_grund() or 'unbekannt'}")
    # Der Exitcode der Pruefung bleibt der Exitcode des Laufs.
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
