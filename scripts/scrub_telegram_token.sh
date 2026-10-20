#!/usr/bin/env bash
# Replace Telegram bot tokens in agent logs with a placeholder.
#
# httpx logs every request URL at INFO, so `POST https://api.telegram.org/bot
# <id>:<secret>/sendMessage` ended up in the log files in clear text. The code
# fix stops new ones; this removes the ones already written.
#
#   scripts/scrub_telegram_token.sh --dry-run   # count, change nothing
#   scripts/scrub_telegram_token.sh --apply     # back up, then rewrite
#
# The backup still contains the token, by design — it is the only way back if a
# sed goes wrong. It is written 0600 and should be deleted once the token has
# been rotated.
set -euo pipefail

LOG_DIR="${LOG_DIR:-/home/moltstack/moltstack/logs}"
BACKUP_DIR="${BACKUP_DIR:-/home/moltstack/log-scrub-backup}"
PATTERN='(api\.telegram\.org/bot)[0-9]{6,}:[A-Za-z0-9_-]{20,}'
MODE="${1:---dry-run}"

mapfile -t FILES < <(grep -rlE "$PATTERN" "$LOG_DIR" 2>/dev/null || true)

if [ "${#FILES[@]}" -eq 0 ]; then
    echo "No token in any log under $LOG_DIR."
    exit 0
fi

total=0
for f in "${FILES[@]}"; do
    n=$(grep -cE "$PATTERN" "$f")
    total=$((total + n))
    printf '%6d lines  %s\n' "$n" "$f"
done
echo "-----"
echo "$total lines across ${#FILES[@]} files"

if [ "$MODE" != "--apply" ]; then
    echo "DRY RUN — nothing written. Re-run with --apply."
    exit 0
fi

stamp=$(date -u +%Y%m%d-%H%M%S)
dest="$BACKUP_DIR/$stamp"
mkdir -p "$dest"
chmod 700 "$BACKUP_DIR" "$dest"

for f in "${FILES[@]}"; do
    cp -p "$f" "$dest/$(basename "$f")"
    chmod 600 "$dest/$(basename "$f")"
    # In place, so the inode the running agents hold on to stays the same.
    sed -i -E "s#${PATTERN}#\1<REDACTED>#g" "$f"
    chmod 640 "$f"
done

echo "Backup (still contains the token, 0600): $dest"
remaining=$(grep -rcE "$PATTERN" "$LOG_DIR" 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')
echo "Remaining matches under $LOG_DIR: $remaining"
[ "$remaining" -eq 0 ] || { echo "ERROR: scrub incomplete"; exit 1; }
echo "Delete the backup once the token is rotated: rm -rf $dest"
