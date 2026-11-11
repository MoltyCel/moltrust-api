#!/usr/bin/env python3
"""Die Wochenzahlen von moltbook_stats.py melden.

Ersetzt Crontab-Zeile 177. Dort stand:

    LINE=$(./venv/bin/python scripts/moltbook_stats.py --telegram 2>>log)
      && curl ... --data-urlencode "text=$LINE"

Also: Programm laufen lassen, seine Ausgabe als Nachricht schicken, und nur
wenn es gelang (`&&`). Dasselbe hier, nur ueber notify — und mit zwei
Unterschieden, die die Shell-Fassung nicht hatte: eine leere Ausgabe wird
nicht als Nachricht verschickt, und ein Fehlschlag des Programms bleibt
sichtbar statt in einem `&&` zu verschwinden.

Seit 27.10.2026 (ops NOTIFY-3c) geht die Ausgabe in den Eingang des
Tagesberichts statt nach Telegram: Wochenzahlen sind ein Messbericht.
"""
import os
import subprocess
import sys

# Drei Ebenen: diese Datei liegt in ops/cron/, nicht in scripts/.
WURZEL = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, WURZEL)

from app import tagesbericht_eingang  # noqa: E402 - ops NOTIFY-3c

# sys.executable, nicht venv/bin/python: ein venv neben der Datei
# vorauszusetzen scheitert im Worktree, und ein Unterprozess mit einem
# anderen Python hat andere Pakete als sein Aufrufer.
PROGRAMM = [sys.executable,
            os.path.join(WURZEL, "scripts", "moltbook_stats.py"), "--telegram"]
LOG = os.path.join(WURZEL, "logs", "moltbook_stats.log")


def main() -> int:
    try:
        p = subprocess.run(PROGRAMM, capture_output=True, text=True,
                           timeout=600, cwd=WURZEL)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"moltbook_stats nicht ausfuehrbar: {type(e).__name__}: {e}")
        return 1

    if p.stderr:
        # Die Shell-Fassung hat stderr an das Log gehaengt. Unveraendert.
        try:
            # exist_ok, und im Rumpf: eine Datei, die beim Import ein
            # Verzeichnis anlegt, handelt beim Import.
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(p.stderr)
        except OSError as e:
            print(f"Log nicht schreibbar: {type(e).__name__}")

    if p.returncode != 0:
        print(f"moltbook_stats endete mit {p.returncode} — nichts gemeldet")
        return p.returncode

    text = (p.stdout or "").strip()
    if not text:
        # `--data-urlencode "text="` waere eine leere Nachricht gewesen; die
        # alte Zeile hat sie verschickt, Telegram haette sie abgewiesen.
        print("keine Ausgabe — nichts zu melden")
        return 0

    # A measurement: daily report, not Telegram (ops NOTIFY-3c).
    if tagesbericht_eingang.ablegen("moltbook_stats", text):
        print(f"abgelegt: {len(text)} Zeichen")
        return 0
    print("nicht abgelegt")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
