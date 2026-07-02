#!/usr/bin/env bash
# Read-only A2A registration-funnel monitor. No secrets. Re-runnable.
#   funnel_monitor.sh                -> report (headline = ACTIVE, weighted)
#   funnel_monitor.sh --set-baseline -> (re)set baseline to now
#
# Headline is ACTIVE agents, NEVER the raw DID count. Keyless PoP mints cost 0
# credits, so a raw count is inflatable by anyone (including us) and must not be
# cited externally. "active" = an external agent with >=1 endorsement (graph) OR
# >=1 metered call (a credit_transactions api_call debit). Columns verified
# against the live schema (agents / endorsements / credit_transactions), not
# guessed.
set -euo pipefail

BASE_DIR="$HOME/moltstack/agents/workspace"
BASELINE="$BASE_DIR/funnel_baseline.json"
EXCL="('moltrust','test','system')"
Q() { psql -h localhost -U moltstack -d moltstack -tAc "$1"; }

# An external agent counts as active if it is in the endorsement graph or has
# spent on a metered call.
ACTIVE_PRED="a.agent_type='external' AND a.platform NOT IN $EXCL AND (
  EXISTS (SELECT 1 FROM endorsements e WHERE e.endorser_did=a.did OR e.endorsed_did=a.did)
  OR EXISTS (SELECT 1 FROM credit_transactions ct WHERE ct.from_did=a.did AND ct.tx_type='api_call')
)"

raw=$(Q "SELECT COUNT(*) FROM agents a WHERE a.agent_type='external' AND a.platform NOT IN $EXCL")
active=$(Q "SELECT COUNT(*) FROM agents a WHERE $ACTIVE_PRED")

if [ "${1:-}" = "--set-baseline" ]; then
  now=$(Q "SELECT to_char(now(),'YYYY-MM-DD\"T\"HH24:MI:SSOF')")
  mkdir -p "$BASE_DIR"
  printf '{"baseline_ts":"%s","baseline_active":%s,"baseline_raw":%s}\n' "$now" "$active" "$raw" > "$BASELINE"
  echo "baseline set: active=$active raw=$raw at $now"
  exit 0
fi

b_ts="(none)"; b_active="?"; new_active="?"
if [ -f "$BASELINE" ]; then
  b_ts=$(sed -E 's/.*"baseline_ts":"([^"]*)".*/\1/' "$BASELINE")
  b_active=$(sed -E 's/.*"baseline_active":([0-9]+).*/\1/' "$BASELINE")
  new_active=$(Q "SELECT COUNT(*) FROM agents a WHERE $ACTIVE_PRED AND a.created_at > '${b_ts}'")
fi

echo "=== A2A Registration Funnel Monitor ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ==="
echo "ACTIVE agents (>=1 endorsement OR >=1 metered call):  ${active}"
echo "  new active since baseline (${b_ts}):  ${new_active}   [baseline active=${b_active}]"
echo "  raw ceiling: ${raw} external DIDs — includes 0-credit keyless mints; raw ceiling, do NOT cite externally"
echo "--- active breakdown ---"
Q "SELECT '  via endorsement: '||COUNT(DISTINCT a.did)::text FROM agents a JOIN endorsements e ON (e.endorser_did=a.did OR e.endorsed_did=a.did) WHERE a.agent_type='external' AND a.platform NOT IN $EXCL"
Q "SELECT '  via metered call: '||COUNT(DISTINCT a.did)::text FROM agents a JOIN credit_transactions ct ON (ct.from_did=a.did AND ct.tx_type='api_call') WHERE a.agent_type='external' AND a.platform NOT IN $EXCL"
echo "--- last 10 external registrations (raw) ---"
Q "SELECT '  '||to_char(created_at,'MM-DD HH24:MI')||'  '||platform||'  '||display_name FROM agents WHERE agent_type='external' AND platform NOT IN $EXCL ORDER BY created_at DESC LIMIT 10"
echo "--- cross-check: public GET /agents/recent ---"
curl -s --max-time 12 https://api.moltrust.ch/agents/recent 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);a=d if isinstance(d,list) else d.get('agents',d);print('  /agents/recent returned',len(a),'agents')" 2>/dev/null || echo "  (recent endpoint unavailable)"
