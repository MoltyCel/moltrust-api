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
STAGE=/home/moltstack/blog-deploy-stage
OUT="$STAGE/registry-proof.json"

cd "$REPO"
python3 scripts/registry_proof_export.py --out "$OUT"

# Merkle paths only. The chain check needs 52 RPC calls and this runs daily;
# the roots are immutable once mined, and the reader's own run covers them.
python3 scripts/registry_proof.py --file "$OUT" --skip-chain

sudo /usr/bin/install -m 644 "$OUT" /var/www/html/registry-proof.json
echo "[$(date -u +%Y-%m-%dT%H:%M:%S)] published $(wc -c < "$OUT") bytes"
