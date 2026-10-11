#!/bin/bash
# Runde-5-Fristlauf. Ein Argument: vorwarnung | auswertung.
#
# Liegt im Repo und nicht in ~/bin. Der Kommentar an den Runde-4-Jobs nennt
# den Grund: sie hingen an drei Dingen ausserhalb des Repos — der at-Queue,
# einem Worktree und zwei Skripten darin. Faellt eines weg, faellt die
# Auszahlung aus, und zwar lautlos. Zwei davon sind damit erledigt; die
# at-Queue bleibt, und dafuer gibt es die Wache.
#
# Laeuft aus dem deployten Checkout, nicht aus einem Worktree: er traegt den
# ausgerollten Stand, wird vom Deploy aktuell gehalten, und es gibt ihn
# immer. Gelesen wird dort nur — geschrieben wird nach ~/Downloads,
# ~/selftest, ~/logs und in den moltrust-web-Checkout.
#
# Jeder Schritt prueft seinen eigenen Exitcode. Keine Pipe.
set -u

REPO=/home/moltstack/moltstack
PY=$REPO/venv/bin/python
LOG=/home/moltstack/logs/r5-expiry.log
RUNS=/home/moltstack/selftest/r5-runs.jsonl
STAGE=/home/moltstack/Downloads/r5-ergebnis
SCRIPT=$REPO/scripts/runden_auswertung.py

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

mode="${1:-}"
case "$mode" in
  # T-24h. Meldet Einreichungen und vollstaendige Gruppen und schreibt
  # nichts: eine Vorwarnung, die eine Datei anfasst, aendert den Zustand,
  # ueber den sie berichtet.
  vorwarnung) args=(--runde r5 --vorwarnung) ;;
  # T+4min. Wertet aus, schreibt ~/Downloads/r5-auswertung.md und
  # result.json mit den Ergebnissen.
  #
  # `--stand offen` und nicht ausgezahlt: zu diesem Zeitpunkt ist nichts
  # bezahlt, und `stand` wechselt nur von offen auf ausgezahlt — einmal
  # ausgezahlt geschrieben, laesst `schreibe_result` den Rueckweg nicht zu.
  # Die Datei wuerde also eine Zahlung behaupten, die nicht stattgefunden
  # hat, und sich dabei festlegen. Vorbereitet heisst: die Gewinnerliste
  # steht und ein Aufruf je Aufgabe fehlt. Die Freigabe gehoert Lars.
  #
  # `--result-nach` zielt in ein Staging-Verzeichnis und NICHT in
  # ~/moltrust-web: der Checkout gehoert seit dem 11.10. 09:55 dem Deploy,
  # seine verfolgten Dateien stehen auf 444, und ein Schreibversuch endet mit
  # PermissionError. Ein Fristlauf, der auf fremdes Schreibrecht angewiesen
  # ist, haengt an dessen Aenderung — und die kam ohne Ankuendigung.
  #
  # Veroeffentlicht wird danach in einem eigenen Schritt: Zweig ueber die
  # Git-Data-API, Merge, dann `moltstack-webinstall rounds/r5/result.json`.
  # Der Web-Deploy allein genuegt nicht, er hat fuer moltrust-web eine feste
  # Dateiliste und `rounds/` steht nicht darin (gemessen am 11.10.: Deploy
  # gruen, Web-Root alt).
  auswertung) args=(--runde r5 --stand offen
                    --result-nach "$STAGE/rounds/r5/result.json") ;;
  *) echo "$(ts) FAIL unbekannter Modus '${mode}'" >>"$LOG"; exit 2 ;;
esac

if [ ! -f "$SCRIPT" ]; then
  echo "$(ts) FAIL $mode: $SCRIPT fehlt" >>"$LOG"
  exit 2
fi

set -a
# shellcheck disable=SC1091
. /home/moltstack/.moltrust_secrets
rc=$?
set +a
if [ "$rc" -ne 0 ]; then
  echo "$(ts) FAIL $mode: secrets rc=$rc" >>"$LOG"
  exit 1
fi

export PATH="$HOME/.npm-global/bin:$PATH"

mkdir -p "$STAGE/rounds/r5"
if [ $? -ne 0 ]; then echo "$(ts) FAIL $mode: mkdir $STAGE" >>"$LOG"; exit 2; fi
# Die gueltige Fassung als Ausgangspunkt, damit `schreibe_result` die Version
# fortzaehlt und den Vorgaenger anlegt. Lesen aus dem Checkout ist erlaubt.
if [ -f /home/moltstack/moltrust-web/rounds/r5/result.json ] \
   && [ ! -f "$STAGE/rounds/r5/result.json" ]; then
  cp /home/moltstack/moltrust-web/rounds/r5/result.json \
     "$STAGE/rounds/r5/result.json"
  chmod 644 "$STAGE/rounds/r5/result.json"
fi

echo "$(ts) START $mode ($SCRIPT ${args[*]})" >>"$LOG"
cd "$REPO" || { echo "$(ts) FAIL $mode: cd $REPO" >>"$LOG"; exit 2; }
"$PY" "$SCRIPT" "${args[@]}" >>"$LOG" 2>&1
rc=$?
echo "$(ts) END $mode rc=$rc" >>"$LOG"

# Nachweis, dass der Lauf stattgefunden hat. Die Wache kann nach der
# geplanten Zeit nicht mehr in atq nachsehen — ein erledigter Job steht dort
# nicht mehr, und "nicht in der Queue" heisst dann gelaufen oder nie
# gelaufen, ohne dass man die beiden unterscheiden koennte.
#
# Geschrieben, nicht nachgetragen: die Zeile entsteht hier, im Lauf, und
# nicht hinterher von Hand. Ein Nachweis, der von Hand ergaenzt wird, ist
# keiner.
mkdir -p "$(dirname "$RUNS")"
if [ $? -ne 0 ]; then echo "$(ts) r5-runs: mkdir fehlgeschlagen" >>"$LOG"; fi
# Eine Zeile JSON, Formatzeichenkette in einfachen Anfuehrungszeichen und in
# EINER Zeile. Die r4-Fassung vom 08.10. war ueber zwei Zeilen zerbrochen und
# ohne Anfuehrungszeichen: `printf ts:%s` schrieb "ts:" nach stdout, die
# naechste Zeile versuchte den Zeitstempel als Befehl, Exitcode 127 — und der
# Nachweis hat einen Tag lang nichts belegt.
printf '{"ts":"%s","modus":"%s","rc":%s}\n' "$(ts)" "$mode" "$rc" >> "$RUNS"
if [ $? -ne 0 ]; then echo "$(ts) r5-runs: schreiben fehlgeschlagen" >>"$LOG"; fi
chmod 600 "$RUNS"
if [ $? -ne 0 ]; then echo "$(ts) r5-runs: chmod fehlgeschlagen" >>"$LOG"; fi

if [ "$rc" -ne 0 ]; then
  # Ein Fristlauf, der scheitert und schweigt, ist derselbe Fehler wie der,
  # den er auffangen soll.
  "$PY" - "$mode" "$rc" <<'PYEOF'
import sys
sys.path.insert(0, "/home/moltstack/moltstack")
from app import notify
mode, rc = sys.argv[1], sys.argv[2]
notify.send_telegram(
    f"MolTrust - Runde-5-Fristlauf '{mode}' ist mit rc={rc} gescheitert. "
    f"Log: /home/moltstack/logs/r5-expiry.log. Die Auswertung muss vor dem "
    f"Ablauf der beiden Aufgaben laufen, sonst ist der Escrow gebunden und "
    f"refund-expired verlangt null Einreichungen.", channel=notify.ALERTS)
PYEOF
  arc=$?
  echo "$(ts) alert rc=$arc" >>"$LOG"
fi

exit "$rc"
