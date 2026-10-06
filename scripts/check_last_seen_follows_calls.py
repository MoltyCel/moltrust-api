#!/usr/bin/env python3
"""Ein Agent, der gerade mit uns spricht, darf nicht als inaktiv gelten.

`request_log.agent_did` ist nur gesetzt, wenn die Credit-Middleware den
Aufrufer über seinen API-Schlüssel aufgelöst hat. Eine solche Zeile ist also
der Beleg eines authentifizierten Aufrufs — und danach muss `agents.last_seen`
mindestens so neu sein wie dieser Aufruf.

Bis zum 06.10.2026 war das nicht so. `last_seen` wanderte nur dort, wo ein
Handler `update_last_seen` von Hand aufrief; acht Stellen taten das, der Rest
nicht, und die beiden Authentifizierungstüren `verify_api_key` und
`verify_api_key_or_did` ebenfalls nicht. Gemessen an diesem Tag: von 265
Agenten mit zuordenbaren Aufrufen in sieben Tagen lagen 23 bis zu einem Tag,
15 zwischen einem und sieben Tagen und 4 mehr als sieben Tage zurück. Seitdem
pflegt die Middleware das Feld an der einen Stelle, an der jeder Aufruf mit
Schlüssel vorbeikommt.

Diese Prüfung hält das fest. Gelb, nicht rot: ein Rückstand ist ein Zeichen
dafür, dass ein Pfad das Feld nicht fortschreibt, und das ist ein Befund —
aber kein Grund, den Dienst anzuhalten.

Letzte Zeile ist die Zahl der Agenten mit Rückstand. Exit 0 ohne, 1 mit,
2 wenn die Frage nicht beantwortbar ist.
"""
from __future__ import annotations

import os
import subprocess
import sys

# Die Middleware schreibt gedrosselt, einmal je Agent und fünf Minuten. Die
# Toleranz muss darüber liegen, sonst meldet die Prüfung die Drosselung statt
# eines fehlenden Pfades.
TOLERANCE_MINUTES = 30

# Fenster. Kürzer als die 30-Tage-Aufbewahrung von request_log, damit die
# Prüfung die Beschneidung nicht als Rückstand liest.
WINDOW_HOURS = 24

# Keine f-Zeichenkette: psql setzt :win und :tol selbst ein. Die Werte sind
# Konstanten, aber die Vorlage wird kopiert, und beim naechsten Mal ist der
# Wert keine Konstante mehr. bandit B608 meldet die Form, nicht den Wert.
SQL = """
WITH auth AS (
  SELECT agent_did AS did, max(ts) AS letzter_aufruf, count(*) AS aufrufe
    FROM request_log
   WHERE agent_did IS NOT NULL
     AND ts > now() - make_interval(hours => :win)
   GROUP BY 1
)
SELECT a.did,
       coalesce(ag.display_name, '(ohne Namen)'),
       coalesce(ag.platform, '?'),
       a.aufrufe::text,
       to_char(a.letzter_aufruf, 'MM-DD HH24:MI:SS'),
       coalesce(to_char(ag.last_seen, 'MM-DD HH24:MI:SS'), 'nie'),
       round(extract(epoch from (a.letzter_aufruf - ag.last_seen))/60)::text
  FROM auth a
  JOIN agents ag ON ag.did = a.did
 WHERE ag.last_seen IS NULL
    OR ag.last_seen < a.letzter_aufruf - make_interval(mins => :tol)
 ORDER BY a.aufrufe DESC
"""


def psql(sql: str, **variables: object) -> list[list[str]]:
    """Abfrage über stdin, Werte als psql-Variablen.

    Über stdin und nicht über `-c`: psql ersetzt Variablen in Dateien und auf
    stdin, bei `-c` bleibt der Doppelpunkt stehen und die Abfrage scheitert.
    """
    args = ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
            "-X", "-A", "-t", "-F", "\x1f"]
    for name, value in variables.items():
        args += ["-v", f"{name}={value}"]
    out = subprocess.run(args, input=sql, capture_output=True, text=True,
                         timeout=120, env=dict(os.environ))
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:300])
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def main() -> int:
    try:
        rows = psql(SQL, win=WINDOW_HOURS, tol=TOLERANCE_MINUTES)
    except Exception as exc:  # noqa: BLE001 - keine Antwort ist nicht gruen
        print(f"UNREADABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(-1)
        return 2

    # Kein Verkehr heißt nicht in Ordnung, sondern unbeantwortet: ohne eine
    # einzige authentifizierte Zeile im Fenster prüft diese Abfrage nichts.
    try:
        total = psql(
            "SELECT count(DISTINCT agent_did) FROM request_log "
            "WHERE agent_did IS NOT NULL AND ts > now() - make_interval(hours => :win)",
            win=WINDOW_HOURS)
        n_auth = int(total[0][0]) if total else 0
    except Exception as exc:  # noqa: BLE001
        print(f"UNREADABLE: Grundgesamtheit nicht lesbar: {type(exc).__name__}", file=sys.stderr)
        print(-1)
        return 2

    if n_auth == 0:
        print(f"UNREADABLE: kein authentifizierter Aufruf in {WINDOW_HOURS} h — "
              "die Prüfung hat nichts geprüft", file=sys.stderr)
        print(-1)
        return 2

    print(f"{n_auth} Agenten mit authentifiziertem Aufruf in {WINDOW_HOURS} h, "
          f"Toleranz {TOLERANCE_MINUTES} min")
    for did, name, platform, calls, last_call, last_seen, lag in rows:
        wie = "nie gesetzt" if last_seen == "nie" else f"{lag} min hinterher"
        print(f"  RUECKSTAND {did} ({name}, {platform}) — {calls} Aufrufe, "
              f"letzter {last_call}, last_seen {last_seen}, {wie}")
    if not rows:
        print("  alle fortgeschrieben")

    print(len(rows))
    return 1 if rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
