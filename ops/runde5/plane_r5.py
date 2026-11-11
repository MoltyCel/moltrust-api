#!/usr/bin/env python3
"""Die zwei Einmallaeufe der Runde 5 in die at-Queue, Zeiten aus dem Ablauf.

Aufruf: plane_r5.py <ablauf-iso>

T-24h fuer die Vorwarnung, T+4min fuer die Auswertung. Die vier Minuten sind
der Puffer aus Runde 4: der Markt setzt `expiryTime`, und ein Lauf genau auf
die Sekunde traefe ein Fenster, in dem der Vertrag noch Einreichungen annimmt.

`at` wird mit UTC gefuettert und das Ergebnis danach gegengelesen: die
Server-Zeitzone ist nicht UTC, und ein um zwei Stunden verschobener
Auswertungslauf waere genau der lautlose Ausfall, den die Wache auffangen
soll.
"""
import datetime
import re
import subprocess
import sys

SKRIPT = "/home/moltstack/moltstack/ops/runde5/r5-run.sh"
LOG = "/home/moltstack/logs/r5-expiry.log"


def at(wann: datetime.datetime, modus: str) -> str:
    """Einen Job einreihen. Zurueck kommt die Job-Nummer."""
    # -t nimmt [[CC]YY]MMDDhhmm; mit TZ=UTC im Environment ist die Angabe UTC.
    stempel = wann.strftime("%Y%m%d%H%M")
    befehl = f"{SKRIPT} {modus} >> {LOG} 2>&1\n"
    p = subprocess.run(["at", "-t", stempel], input=befehl, text=True,
                       capture_output=True, env={"TZ": "UTC", "PATH": "/usr/bin:/bin",
                                                 "HOME": "/home/moltstack"})
    if p.returncode != 0:
        raise SystemExit(
            f"at {modus} fehlgeschlagen: {p.stderr.strip()[:200]}")
    m = re.search(r"job (\d+)", p.stderr)
    if not m:
        raise SystemExit(
            f"at {modus}: keine Job-Nummer in {p.stderr.strip()[:200]!r}")
    return m.group(1)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("Aufruf: plane_r5.py <ablauf-iso>", file=sys.stderr)
        return 2

    ablauf = datetime.datetime.fromisoformat(argv[0].replace("Z", "+00:00"))
    if ablauf.tzinfo is None:
        ablauf = ablauf.replace(tzinfo=datetime.timezone.utc)
    ablauf = ablauf.astimezone(datetime.timezone.utc)

    zeiten = [("vorwarnung", ablauf - datetime.timedelta(hours=24)),
              ("auswertung", ablauf + datetime.timedelta(minutes=4))]

    print(f"Ablauf  {ablauf.isoformat()}")
    jobs = []
    for modus, wann in zeiten:
        nr = at(wann, modus)
        jobs.append((nr, wann, modus))
        print(f"  Job {nr}: {modus} um {wann.isoformat()}")

    print("\n=== Gegenlesen: was steht wirklich in der Queue? ===")
    q = subprocess.run(["atq"], capture_output=True, text=True)
    print(q.stdout.rstrip() or "(leer)")

    for nr, wann, modus in jobs:
        p = subprocess.run(["at", "-c", nr], capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  Job {nr}: at -c fehlgeschlagen")
            continue
        hat = SKRIPT in p.stdout and f" {modus}" in p.stdout
        print(f"  Job {nr} ({modus}): Skript und Modus im Rumpf — "
              f"{'ja' if hat else 'NEIN'}")

    print("\n=== Fuer LAEUFE['r5'] ===")
    print('        "jobs": (' + ",\n                 ".join(
        f'("{nr}", "{wann.isoformat()}", "{modus}")'
        for nr, wann, modus in jobs) + "),")
    print(f'        "deadline": "{ablauf.isoformat()}",')
    return 0


# Der Rumpf steht in main(). Vorher lag er oben, und damit haette ein Import
# at-Jobs eingereiht — `scripts/module_effects.py --check` hat genau das
# gemeldet (zwei neue subprocess.run auf Modulebene). CLAUDE.md seit dem
# 10.10.: eine Probe fuehrt keine Modulruempfe aus, also darf ein Modulrumpf
# auch nichts tun.
if __name__ == "__main__":
    sys.exit(main())
