#!/usr/bin/env python3
"""
MolTrust Endpoint Probe — monitors critical API endpoints every 5 minutes.

Alerts via Telegram on 2 consecutive failures (deduped).
Sends recovery alert when endpoint comes back.
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app import notify  # shared gate: app/notify.telegram_allowed

# ─── Configuration ────────────────────────────────────────────────────────────

STATE_FILE = Path.home() / "moltstack" / "state" / "endpoint_probe.json"
LOG_FILE = Path.home() / "moltstack" / "logs" / "endpoint_probe.log"
SECRETS_FILE = Path.home() / ".moltrust_secrets"
TIMEOUT = 10
CONSECUTIVE_FAILURES_BEFORE_ALERT = 2
DRY_RUN = "--dry-run" in sys.argv

ENDPOINTS = [
    {
        "path": "/health",
        "url": "https://api.moltrust.ch/health",
        "body_contains": None,
        "body_json_key": None,
    },
    {
        "path": "/.well-known/did.json",
        "url": "https://api.moltrust.ch/.well-known/did.json",
        "body_contains": "did:web:api.moltrust.ch",
        "body_json_key": None,
    },
    {
        "path": "/.well-known/jwks.json",
        "url": "https://api.moltrust.ch/.well-known/jwks.json",
        "body_contains": None,
        "body_json_key": "keys",
    },
    {
        "path": "/.well-known/agent-card.json",
        "url": "https://api.moltrust.ch/.well-known/agent-card.json",
        "body_contains": None,
        "body_json_key": None,
    },
    {
        "path": "/skill/trust-score/TrustScout",
        "url": "https://api.moltrust.ch/skill/trust-score/did:moltrust:d34ed796a4dc4698",
        "body_contains": None,
        "body_json_key": "trust_score",
    },
]

# ─── Secrets ──────────────────────────────────────────────────────────────────

def load_secrets():
    secrets = {}
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                secrets[k.strip()] = v.strip().strip("\"'")
    return secrets

SECRETS = load_secrets()
TG_TOKEN = SECRETS.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = SECRETS.get("TELEGRAM_CHAT_ID", "")

# ─── Logging ──────────────────────────────────────────────────────────────────

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("probe")

# ─── State ────────────────────────────────────────────────────────────────────

def load_state():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(msg):
    if DRY_RUN:
        log.info("[DRY-RUN] Would send Telegram: %s", msg)
        return
    if not notify.telegram_allowed("endpoint_probe.send_telegram", logger=log):
        return
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("Telegram not configured — skipping alert")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        log.error("Telegram send failed: %s", e)

# ─── Probe ────────────────────────────────────────────────────────────────────

def check_endpoint(ep):
    """Returns (ok: bool, error_msg: str|None)."""
    try:
        r = requests.get(ep["url"], timeout=TIMEOUT, allow_redirects=False)
    except requests.exceptions.Timeout:
        return False, "timeout (10s)"
    except requests.exceptions.ConnectionError as e:
        return False, f"connection error: {str(e)[:80]}"
    except Exception as e:
        return False, f"request error: {str(e)[:80]}"

    body = r.text

    # Paranoid: DNS cache overflow in body
    if "DNS cache overflow" in body:
        return False, f"HTTP {r.status_code} but body contains 'DNS cache overflow'"

    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"

    # Body string check
    if ep["body_contains"] and ep["body_contains"] not in body:
        return False, f"body missing '{ep['body_contains']}'"

    # Body JSON key check
    if ep["body_json_key"]:
        try:
            data = r.json()
            if ep["body_json_key"] not in data:
                return False, f"JSON missing key '{ep['body_json_key']}'"
        except Exception:
            return False, "response is not valid JSON"

    return True, None

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    state = load_state()

    log.info("Probe run started (%s endpoints)%s", len(ENDPOINTS), " [DRY-RUN]" if DRY_RUN else "")

    for ep in ENDPOINTS:
        path = ep["path"]
        ok, error = check_endpoint(ep)

        ep_state = state.get(path, {
            "last_status": "unknown",
            "last_success_ts": None,
            "last_failure_ts": None,
            "consecutive_failures": 0,
            "alerted_down": False,
        })

        if ok:
            log.info("  %-45s OK", path)

            if ep_state["last_status"] == "down" and ep_state["alerted_down"]:
                # Recovery
                down_since = ep_state.get("last_failure_ts", now_iso)
                try:
                    down_dt = datetime.fromisoformat(down_since)
                    minutes = int((now - down_dt).total_seconds() / 60)
                except Exception:
                    minutes = "?"
                send_telegram(f"✅ endpoint {path} recovered after {minutes}min")
                log.info("  %-45s RECOVERED after %smin", path, minutes)

            ep_state["last_status"] = "up"
            ep_state["last_success_ts"] = now_iso
            ep_state["consecutive_failures"] = 0
            ep_state["alerted_down"] = False

        else:
            log.warning("  %-45s FAIL: %s", path, error)

            ep_state["consecutive_failures"] = ep_state.get("consecutive_failures", 0) + 1
            ep_state["last_status"] = "down"
            ep_state["last_failure_ts"] = now_iso
            ep_state["last_error"] = error

            if (ep_state["consecutive_failures"] >= CONSECUTIVE_FAILURES_BEFORE_ALERT
                    and not ep_state.get("alerted_down", False)):
                send_telegram(
                    f"🚨 endpoint {path} down — {error} — {now_iso}"
                )
                ep_state["alerted_down"] = True
                log.warning("  %-45s ALERTED (consecutive=%d)", path, ep_state["consecutive_failures"])

        state[path] = ep_state

    save_state(state)
    log.info("Probe run complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("Probe script error: %s", e)
        sys.exit(0)  # Always exit 0 for cron
