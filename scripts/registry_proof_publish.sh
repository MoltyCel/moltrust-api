#!/bin/bash
# Refresh moltrust.ch/registry-proof.json. Daily, 05:40 UTC, before the
# milestone trigger at 06:00 so the trigger's URL probe sees today's file.
#
# Two steps and a gate between them: the exporter writes into the staging dir,
# the reader checks what it wrote, and only a file that passes gets installed.
# Publishing a proof file that does not replay would be worse than publishing
# nothing, because the whole claim of the page is that a stranger can check it.
#
# The exporter refuses to write a short file and refuses to run at all if
# public_count.sql cannot prove completeness, so a failure here means the file
# in the web root stays as it was — yesterday's numbers, not wrong ones.
set -eo pipefail

REPO=/home/moltstack/moltstack
# The web root is written through /usr/local/sbin/moltstack-webinstall, which
# copies from the moltrust-web checkout and nowhere else. The broad
# "install -m 644 from the staging dir" sudoers rule this script used was
# withdrawn on 2026-10-02, and the script failed silently into a password
# prompt until the first run after that. The generated file therefore lands in
# the checkout, which is also what keeps one writer per artefact.
REPO_WEB=/home/moltstack/moltrust-web
OUT="$REPO_WEB/registry-proof.json"

cd "$REPO"
python3 scripts/registry_proof_export.py --out "$OUT"

# Merkle paths only. The chain check needs 52 RPC calls and this runs daily;
# the roots are immutable once mined, and the reader's own run covers them.
python3 scripts/registry_proof.py --file "$OUT" --skip-chain

sudo -n /usr/local/sbin/moltstack-webinstall registry-proof.json
echo "[$(date -u +%Y-%m-%dT%H:%M:%S)] published $(wc -c < "$OUT") bytes"
