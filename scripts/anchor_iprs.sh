#!/bin/bash
set -a && source ~/.moltrust_secrets && set +a
# Anchor pending IPRs in batches of 100
while true; do
    RESULT=$(curl -s -X POST http://localhost:8000/vc/ipr/admin/anchor \
        -H "x-admin-key: $ADMIN_KEY" \
        -H "Content-Type: application/json")
    BATCHED=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"batched\",0))" 2>/dev/null)
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $RESULT"
    if [ "$BATCHED" = "0" ]; then
        break
    fi
    sleep 2
done

# --- Credentials -------------------------------------------------------------
# Same two-hour slot, same batching argument: one transaction for the batch
# rather than one per credential. Paid from BASE_ANCHOR_KEY, not the productive
# wallet, so running anchoring dry cannot stop what people paid for.
while true; do
    RESULT=$(curl -s -X POST http://localhost:8000/credentials/admin/anchor \
        -H "x-admin-key: $ADMIN_KEY" \
        -H "Content-Type: application/json")
    BATCHED=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"batched\",0))" 2>/dev/null)
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VC $RESULT"
    if [ "$BATCHED" = "0" ]; then
        break
    fi
    sleep 2
done
