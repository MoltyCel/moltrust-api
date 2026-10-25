#!/usr/bin/env bash
# Weekly: empty the npm and pip download caches of the moltstack user.
#
# Why (ops SEC-1025-SRV, 25.10.2026): the root partition stood at 89 % on
# 25.10. and 79 % after a first pip cleanup. ~/.npm alone held 2.4 GB of
# download cache (_cacache 2.1 GB, _npx 349 MB), ~/.cache/pip another 310 MB.
# Neither cache has a size limit, both are rebuilt on the next install, and
# nothing on this host reads them otherwise.
set -u
echo "[$(date -u +%FT%TZ)] before: $(df -h / | awk 'NR==2 {print $5}') used on /"
npm cache clean --force 2>&1 | grep -v 'Recommended protections disabled' || true
rm -rf "$HOME/.npm/_npx"
/home/moltstack/moltstack/venv/bin/pip cache purge 2>&1 | tail -1
echo "[$(date -u +%FT%TZ)] after:  $(df -h / | awk 'NR==2 {print $5}') used on /"
