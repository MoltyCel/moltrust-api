"""Inspect and set the Telegram webhook. Setting it is a deliberate act.

Registering a webhook silently disables getUpdates for the token, so this is
not something a deploy should do on its own. The order that works:

    python scripts/telegram_webhook_admin.py info        # what is set today
    python scripts/telegram_webhook_admin.py test        # fake update -> our endpoint
    python scripts/telegram_webhook_admin.py set         # hand the token over
    python scripts/telegram_webhook_admin.py delete      # back to polling

`test` posts a synthetic update straight at our own endpoint with the real
secret header. It proves the route, the secret check and the insert without
involving Telegram at all, which is the point: if `set` is the first thing that
ever exercises the path, a mistake costs live updates.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from app import notify
from app.telegram_inbox import ALLOWED_UPDATES, webhook_secret

notify.silence_http_request_logs()

WEBHOOK_URL = os.environ.get("TELEGRAM_WEBHOOK_URL",
                             "https://api.moltrust.ch/telegram/webhook")
API = "https://api.telegram.org/bot{token}/{method}"

# Far outside Telegram's own numbering, so a test row is obvious in the table
# and can never collide with a real update_id.
TEST_UPDATE_ID_BASE = 900_000_000


def token() -> str:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        print("TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        raise SystemExit(2)
    return tok


def call(method: str, **params):
    r = requests.post(API.format(token=token(), method=method), data=params, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:300]}


def cmd_info() -> int:
    code, body = call("getWebhookInfo")
    print(f"getWebhookInfo {code}")
    print(json.dumps(body, indent=1))
    secret = webhook_secret()
    print(f"\nlocal secret configured: {'yes, %d chars' % len(secret) if secret else 'NO'}")
    print(f"target url             : {WEBHOOK_URL}")
    print(f"allowed_updates        : {ALLOWED_UPDATES}")
    return 0


def cmd_test() -> int:
    """Post a synthetic update at our own endpoint, with and without the secret."""
    secret = webhook_secret()
    if not secret:
        print("no TELEGRAM_WEBHOOK_SECRET — the endpoint will answer 503", file=sys.stderr)
        return 2
    update_id = TEST_UPDATE_ID_BASE + int(time.time()) % 100_000
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 1, "date": int(time.time()),
            "chat": {"id": 0, "type": "private"},
            "from": {"id": 0, "is_bot": False, "first_name": "webhook-test"},
            "text": "/webhook_selftest",
        },
    }
    print(f"POST {WEBHOOK_URL}  update_id={update_id}")

    r = requests.post(WEBHOOK_URL, json=payload, timeout=30,
                      headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-on-purpose"})
    print(f"  with a wrong secret : {r.status_code} {r.text[:120]}   (403 expected)")
    ok_wrong = r.status_code == 403

    r = requests.post(WEBHOOK_URL, json=payload, timeout=30,
                      headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    print(f"  with the real secret: {r.status_code} {r.text[:120]}   (200 expected)")
    ok_right = r.status_code == 200

    r = requests.post(WEBHOOK_URL, json=payload, timeout=30,
                      headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    print(f"  the same update again: {r.status_code} {r.text[:120]}   (200, stored once)")

    print()
    print("row in telegram_inbox:")
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get("DATABASE_URL",
                                               "dbname=moltstack user=moltstack"))
        with conn, conn.cursor() as cur:
            cur.execute("SELECT update_id, consumed_by, ts FROM telegram_inbox "
                        "WHERE update_id = %s", (update_id,))
            print(" ", cur.fetchone())
            cur.execute("DELETE FROM telegram_inbox WHERE update_id >= %s",
                        (TEST_UPDATE_ID_BASE,))
            print(f"  cleaned up {cur.rowcount} test row(s)")
        conn.close()
    except Exception as e:
        print(f"  could not read the table: {e}")
        return 1
    return 0 if (ok_wrong and ok_right) else 1


def cmd_set() -> int:
    secret = webhook_secret()
    if not secret:
        print("refusing: TELEGRAM_WEBHOOK_SECRET is not set", file=sys.stderr)
        return 2
    code, body = call("setWebhook", url=WEBHOOK_URL, secret_token=secret,
                      allowed_updates=json.dumps(ALLOWED_UPDATES),
                      drop_pending_updates="false", max_connections=10)
    print(f"setWebhook {code}: {json.dumps(body)}")
    print("\ngetUpdates is now disabled for this token. Every consumer reads "
          "telegram_inbox.")
    return 0 if body.get("ok") else 1


def cmd_delete() -> int:
    code, body = call("deleteWebhook", drop_pending_updates="false")
    print(f"deleteWebhook {code}: {json.dumps(body)}")
    return 0 if body.get("ok") else 1


COMMANDS = {"info": cmd_info, "test": cmd_test, "set": cmd_set, "delete": cmd_delete}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "info"
    if which not in COMMANDS:
        print(f"usage: {sys.argv[0]} [{'|'.join(COMMANDS)}]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(COMMANDS[which]())
