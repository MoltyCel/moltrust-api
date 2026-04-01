#!/usr/bin/env bash
#
# Smoke test for MolTrust API
# Usage: ./scripts/smoke_test.sh [base_url] [api_key]
#
set -euo pipefail

BASE="${1:-http://localhost:8000}"
KEY="${2:-dev_test_key_local}"
PASS=0
FAIL=0

check() {
    local name="$1" method="$2" path="$3" expected_status="$4"
    shift 4
    local extra_args=("$@")

    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" \
        -X "$method" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $KEY" \
        "${extra_args[@]}" \
        "$BASE$path" 2>/dev/null || echo "000")

    if [ "$status" = "$expected_status" ]; then
        echo "  PASS  $name (HTTP $status)"
        ((PASS++))
    else
        echo "  FAIL  $name (expected $expected_status, got $status)"
        ((FAIL++))
    fi
}

echo ""
echo "MolTrust API Smoke Test"
echo "======================="
echo "Target: $BASE"
echo ""

# --- Health ---
echo "Health & Info"
check "GET /health" GET "/health" 200
check "GET /info" GET "/info" 200

# --- Identity ---
echo ""
echo "Identity"
check "POST /identity/register" POST "/identity/register" 200 \
    -d '{"platform":"smoke_test","display_name":"Smoke Agent"}'

check "GET /identity/resolve (nonexistent)" GET "/identity/resolve/did:moltrust:nonexistent" 404

# --- Reputation ---
echo ""
echo "Reputation"
check "GET /reputation/query (nonexistent)" GET "/reputation/query/did:moltrust:nonexistent" 200

# --- Credentials ---
echo ""
echo "Credentials"
check "POST /credentials/verify (empty)" POST "/credentials/verify" 422 \
    -d '{}'

# --- Credits ---
echo ""
echo "Credits"
check "GET /credits/pricing" GET "/credits/pricing" 200

# --- DID Document ---
echo ""
echo "Well-Known"
check "GET /.well-known/did.json" GET "/.well-known/did.json" 200

# --- Unauthenticated ---
echo ""
echo "Auth enforcement"
check "POST /identity/register (no key)" POST "/identity/register" 403 \
    -d '{"platform":"test","display_name":"No Key"}' \
    -H "X-API-Key: invalid_key_xxx"

# --- Summary ---
echo ""
echo "======================="
echo "Results: $PASS passed, $FAIL failed"
echo ""

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
