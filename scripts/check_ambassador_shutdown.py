#!/usr/bin/env python3
"""Die Ablesung zum 01.11.2026 für das Abschaltkriterium des Ambassadors.

Drei Zahlen, nichts weiter:

  1. Registrierungen mit `platform = 'moltbook'` seit dem 01.10.2026
  2. davon mit gebundener Wallet
  3. davon mit mindestens einem authentifizierten Aufruf

Keine Automatik. Dieses Skript schaltet nichts ab, bewertet nichts und nennt
keine Schwelle. Es nimmt die Ablesung, schickt sie einmal nach ALERTS und
belegt danach, dass sie genommen wurde. Die Entscheidung trifft Lars.

Warum über die Plattformspalte und nicht über `agent_source`
------------------------------------------------------------

`agent_source` ist die Tabelle, die die Zurechnung tragen soll — eine Zeile je
Registrierung, einmal geschrieben und nie geändert, damit eine Zurechnung
nachprüfbar bleibt. Sie hat **null Zeilen** (geprüft 2026-10-06). Niemand
schreibt hinein; `app/sql/channel_yield.sql` liest sie und würde jede
Registrierung als `(untagged)` melden. Eine Zählung darauf stünde auf einer
leeren Tabelle und läse dauerhaft null — eine Null, die das fehlende Schreiben
misst und nicht den Kanal.

Deshalb `platform`. Und deshalb gehört dieser Satz dazu: **`platform` ist
Selbstauskunft des Aufrufers.** Es sagt, welchem Ökosystem ein Agent sich
zurechnet, nicht welche unserer Oberflächen ihn geschickt hat — genau die
Unterscheidung, die `channel_yield.sql` im Kopf führt. `platform = 'moltbook'`
heißt also „der Agent hat moltbook angegeben", nicht „der Ambassador hat ihn
gebracht". Die Zahl ist eine Untergrenze mit einer Unschärfe in beide
Richtungen, und sie ist die beste, die ohne geschriebene Zurechnung zu haben
ist.

Woher die dritte Zahl kommt
---------------------------

Aus `usage_daily_keys`, nicht aus `request_log`. `request_log` hält 30 Tage
(ältester Eintrag am 2026-10-06: 06.09.). Am 01.11. wäre ein Agent, der am
02.10. einmal anrief und nie wieder, dort als „kein Aufruf" geführt — die
Beschneidung gemessen, nicht das Verhalten. `usage_daily_keys` ist der Rollup,
der sie überlebt; er beginnt am 14.09., deckt das Fenster ab 01.10. also
vollständig. Gegenprobe am 2026-10-06 über `platform='taskmarket'` seit dem
01.10.: beide Quellen 59 von 74. Für `platform='moltbook'` sind beide 0.

Stand bei der Anlage, 2026-10-06: **1 / 0 / 0**. 13 moltbook-Registrierungen
überhaupt, eine davon seit dem 01.10., keine mit einem authentifizierten
Aufruf — nie.

Letzte Zeile ist die Zahl der fälligen, aber nicht gesendeten Ablesungen.
Exit 0 ohne, 1 mit, 2 wenn die Zahlen nicht zu lesen sind. Eine Ablesung, die
nicht ankommt, ist von einer nicht genommenen nicht zu unterscheiden.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

UTC = dt.timezone.utc

WINDOW_START = "2026-10-01"
READING_DUE = dt.datetime(2026, 11, 1, tzinfo=UTC)
PLATFORM = "moltbook"
STATE = os.path.expanduser("~/.ambassador_shutdown_reading.json")

# Keine Zeichenkette, die SQL zusammensetzt: `:'plat'` und `:'von'` werden von
# psql selbst gequotet und als Literal eingesetzt. Beide Werte sind hier
# Konstanten, aber eine Abfrage, die über eine f-Zeichenkette entsteht, bleibt
# eine Abfrage, die über eine f-Zeichenkette entsteht — bandit B608 meldet das
# zu Recht, und die Vorlage wird kopiert.
SQL = """
WITH mb AS (
  SELECT a.did, a.wallet_address
    FROM agents a
   WHERE a.revoked_at IS NULL
     AND a.platform = :'plat'
     AND a.created_at >= :'von'
)
SELECT (SELECT count(*) FROM mb),
       (SELECT count(*) FROM mb WHERE wallet_address IS NOT NULL),
       (SELECT count(*) FROM mb WHERE EXISTS (
          SELECT 1 FROM usage_daily_keys u WHERE u.did = mb.did))
"""


def psql(sql: str, **variables: str) -> list[list[str]]:
    """Abfrage über stdin, Werte als psql-Variablen.

    Über stdin und nicht über `-c`, weil psql Variablen nur in Dateien und in
    stdin ersetzt — bei `-c` bleibt `:'plat'` stehen und die Abfrage scheitert
    am Doppelpunkt. Geprüft in beiden Formen.
    """
    args = ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
            "-X", "-A", "-t", "-F", "\x1f"]
    for name, value in variables.items():
        args += ["-v", f"{name}={value}"]
    out = subprocess.run(args, input=sql, capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:300])
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def state() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - erster Lauf, oder eine Datei, die wir neu schreiben
        return {}


def save(d: dict) -> None:
    with open(STATE, "w") as f:
        json.dump(d, f)
    os.chmod(STATE, 0o600)


def reading() -> tuple[int, int, int]:
    rows = psql(SQL, plat=PLATFORM, von=WINDOW_START)
    if not rows or len(rows[0]) != 3 or not all(c.strip().isdigit() for c in rows[0]):
        raise RuntimeError(f"keine drei Zahlen: {rows!r}")
    a, b, c = (int(x) for x in rows[0])
    # Eine Teilmenge kann nicht größer sein als ihre Obermenge. Wäre sie es,
    # stimmt die Abfrage nicht mehr mit dem überein, was sie zu messen behauptet.
    if not (a >= b and a >= c):
        raise RuntimeError(f"Teilmenge größer als Grundmenge: {a}/{b}/{c}")
    return a, b, c


def main() -> int:
    now = dt.datetime.now(UTC)
    try:
        regs, wallets, calls = reading()
    except Exception as exc:  # noqa: BLE001 - keine Zahl ist besser als eine falsche
        print(f"UNREADABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(-1)
        return 2

    line = (f"{PLATFORM}-Registrierungen seit {WINDOW_START}: {regs} · "
            f"davon mit gebundener Wallet: {wallets} · "
            f"davon mit mindestens einem authentifizierten Aufruf: {calls}")
    print(line)

    st = state()
    if now < READING_DUE:
        tage = (READING_DUE - now).days
        print(f"Ablesung fällig am {READING_DUE:%Y-%m-%d}, in {tage} Tagen — "
              f"heute nur gemessen, nichts gesendet")
        print(0)
        return 0

    if st.get("sent_at"):
        print(f"Ablesung gesendet am {st['sent_at']}")
        print(0)
        return 0

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    try:
        from app import notify
        ok = notify.send_telegram(
            "Ambassador — Ablesung zum "
            f"{READING_DUE:%d.%m.%Y}\n\n{line}\n\n"
            "Keine Automatik: nichts ist abgeschaltet und nichts entschieden. "
            "Die Zurechnung läuft über die selbstberichtete Plattformspalte, "
            "weil agent_source leer ist — die Zahl ist eine Untergrenze. Die "
            "dritte Zahl kommt aus usage_daily_keys, nicht aus request_log, "
            "das nur 30 Tage hält.",
            channel=notify.ALERTS)
    except Exception as exc:  # noqa: BLE001 - eine nicht gesendete Ablesung ist der Befund
        print(f"Senden fehlgeschlagen: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(1)
        return 1
    if not ok:
        print("Senden abgelehnt oder unterdrückt", file=sys.stderr)
        print(1)
        return 1

    st["sent_at"] = now.isoformat()
    st["reading"] = {"registrations": regs, "with_wallet": wallets, "with_call": calls}
    save(st)
    print(f"Ablesung nach ALERTS gesendet, {now.isoformat()}")
    print(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
