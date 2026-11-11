#!/bin/bash
# Drift check for the root copy of the blog index self-heal (ops S6, 2026-10-12).
# Läuft als moltstack, nur lesend. Exit 0 = gleich, 1 = Abweichung,
# 2 = nicht prüfbar (eine Datei fehlt oder ist nicht lesbar).
set -u
SRC=/home/moltstack/moltstack/scripts
LIB=/usr/local/lib/moltrust/generate_blog_index.py
BIN=/usr/local/sbin/moltrust-blog-index-selfheal
CRON=/etc/cron.d/moltrust-blog-index
LOG=/var/log/moltrust-blog-index.log
SUB='s#python3 /home/moltstack/moltstack/scripts/generate_blog_index.py#python3 -I /usr/local/lib/moltrust/generate_blog_index.py#'

for f in "$SRC/generate_blog_index.py" "$SRC/blog_index_selfheal.sh" "$LIB" "$BIN" "$CRON"; do
  [ -r "$f" ] || { echo "NICHT PRUEFBAR: $f fehlt oder ist nicht lesbar"; exit 2; }
done

rc=0
cmp -s "$SRC/generate_blog_index.py" "$LIB" \
  || { echo "DRIFT Generator: Checkout und $LIB unterscheiden sich"; rc=1; }
sed "$SUB" "$SRC/blog_index_selfheal.sh" | cmp -s - "$BIN" \
  || { echo "DRIFT Skript: Checkout (umgeschrieben) und $BIN unterscheiden sich"; rc=1; }

want() {  # want <datei> <erwartet "user:group mode">
  local have; have=$(stat -c '%U:%G %a' "$1")
  [ "$have" = "$2" ] || { echo "RECHTE $1: $have, erwartet $2"; rc=1; }
}
want "$BIN" "root:root 755"
want "$LIB" "root:root 644"
want "$(dirname "$LIB")" "root:root 755"
want "$CRON" "root:root 644"
[ -L "$LOG" ] && { echo "LOG: $LOG ist ein Symlink"; rc=1; }
[ -f "$LOG" ] && want "$LOG" "root:adm 640"
[ "$(stat -c %U /var/log)" = root ] || { echo "LOG: /var/log gehört nicht root"; rc=1; }
id -nG | tr " " "\n" | grep -qx "$(stat -c %G /var/log)" && { echo "LOG: moltstack ist in der Besitzergruppe von /var/log"; rc=1; }

grep -Eq '^[^#]*[[:space:]]root[[:space:]]+/usr/local/sbin/moltrust-blog-index-selfheal([[:space:]]|$)' "$CRON" \
  || { echo "CRON: $CRON ruft $BIN nicht auf"; rc=1; }
if grep -v '^[[:space:]]*#' "$CRON" | grep -q '/home/moltstack'; then
  echo "CRON: $CRON verweist noch auf /home/moltstack"; rc=1
fi

[ "$rc" -eq 0 ] && echo "OK: root-Kopie entspricht dem Checkout"
exit "$rc"
