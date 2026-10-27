#!/usr/bin/env bash
# For the invariant c-offsite-backup-fresh: is the newest snapshot in the
# off-site repository younger than 26 hours?
#
# It asks the repository itself, not the stamp file offsite_backup.sh writes:
# a stamp says a run believed it succeeded, the repository says what is there.
# The stamp is only used to name the last known good run when the repository
# cannot be reached.
#
# Prints "offsite-backup=ok" plus the age on success. Otherwise one plain
# sentence with what to do, which the selftest report carries to Telegram.
set -uo pipefail

ENV_FILE="${OFFSITE_ENV:-${HOME}/.config/restic/offsite.env}"
RESTIC="${RESTIC_BIN:-${HOME}/bin/restic}"
STATE="${OFFSITE_STATE_DIR:-${HOME}/.local/state/offsite-backup}"
MAX_H="${OFFSITE_MAX_AGE_H:-26}"
LOG=/home/moltstack/moltstack/logs/offsite_backup.log

last_known() {
  [ -r "$STATE/last-success" ] && printf ' Letzter bekannter Erfolg: %s.' "$(cat "$STATE/last-success")"
}

if [ ! -r "$ENV_FILE" ] || [ ! -x "$RESTIC" ]; then
  echo "Die Sicherung ausserhalb des Servers ist nicht eingerichtet: $ENV_FILE oder $RESTIC fehlt. Bitte die Console bitten, BAK-1025 Kriterium 3 nachzusehen."
  exit 0
fi
set -a; . "$ENV_FILE"; set +a

if ! json=$(timeout 120 "$RESTIC" snapshots --no-lock --tag daily --host moltstack --latest 1 --json 2>/dev/null); then
  echo "Der Sicherungsspeicher ausserhalb des Servers antwortet nicht.$(last_known) Bitte in der Hetzner Console nachsehen, ob die Storage Box laeuft, sonst die Console bitten, die Verbindung zu pruefen."
  exit 0
fi

age_h=$(printf '%s' "$json" | python3 -I -c '
import json, sys, datetime as d
s = json.load(sys.stdin)
if not s:
    print(-1); sys.exit()
t = d.datetime.fromisoformat(s[-1]["time"])
print(int((d.datetime.now(d.timezone.utc) - t).total_seconds() // 3600))') || age_h=-2

case "$age_h" in
  -1) echo "Im Sicherungsspeicher liegt noch keine taegliche Sicherung. Bitte die Console bitten, $LOG zu lesen." ;;
  -2) echo "Die Liste der Sicherungen war nicht lesbar. Bitte die Console bitten, $LOG zu lesen." ;;
  *)
    if [ "$age_h" -lt "$MAX_H" ]; then
      echo "offsite-backup=ok alter=${age_h}h"
    else
      echo "Die letzte Sicherung ausserhalb des Servers ist ${age_h} Stunden alt, erwartet ist hoechstens ${MAX_H}. Die naechtliche Sicherung ist also ausgefallen. Bitte die Console bitten, $LOG zu lesen und die Sicherung neu zu starten."
    fi ;;
esac
exit 0
