#!/usr/bin/env python3
"""MolTrust Operator Agent - Lightweight health monitoring (no agent creation)"""

import json
import httpx
import datetime

API_BASE = "http://localhost:8000"

def check_health():
    try:
        r = httpx.get(f"{API_BASE}/health", timeout=5)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def check_stats():
    try:
        r = httpx.get(f"{API_BASE}/stats", timeout=5)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def main():
    ts = datetime.datetime.utcnow().isoformat()

    health_code, health = check_health()
    stats_code, stats = check_stats()

    db_ok = health.get("database") == "connected"
    api_ok = health_code == 200 and health.get("status") == "ok"

    status = "OK" if (api_ok and db_ok) else "DEGRADED"
    if health_code == 0:
        status = "DOWN"

    print(f"[{ts}] {status} | api={health_code} db={'up' if db_ok else 'DOWN'} | agents={stats.get('agents', '?')} ratings={stats.get('ratings', '?')} creds={stats.get('credentials', '?')}")

    if status != "OK":
        print(f"[{ts}] ALERT: health={json.dumps(health)} stats={json.dumps(stats)}")

if __name__ == "__main__":
    main()
