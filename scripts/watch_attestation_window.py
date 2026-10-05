#!/usr/bin/env python3
"""Watch the disabled attestation route until the last one it issued expires.

/guard/governance/validate-capabilities was taken out of service on 2026-10-05
after it turned out to issue signed authorization attestations to anyone, for
any DID named in the body. 28 were issued between 2026-09-20 and 2026-10-05.

27 of them are past their maximum seven-day window. One is not: issued
2026-10-01 16:41 UTC, so at worst valid until 2026-10-08 16:41 UTC. The real
window is unknowable because `validity_hours` came from the request body and
the body was never logged, so this watch assumes the cap.

No key rotation. The one attestation expires on its own, and until it does this
reports two things to ALERTS:

  - every call against the disabled route, because somebody still calling it is
    somebody who had been relying on it
  - every gate decision that failed on attestation shape or signature, which is
    the closest thing to an attempt to redeem one. Nothing in either codebase
    accepts this attestation type — the gate rejects it on `payload.v`, checked
    by running it — so a redemption attempt can only show up as a rejection.

After the window closes it confirms once that nothing is left, then says so and
stops alerting.

Silent when nothing happened, so it can run often.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import urllib.parse
import urllib.request

WINDOW_END = dt.datetime(2026, 10, 8, 16, 41, tzinfo=dt.timezone.utc)
ROUTE = "%validate-capabilities%"
STATE = os.path.expanduser("~/.attestation_watch_state.json")


def telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID_ALERTS") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("no telegram config; would have sent:\n" + text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        urllib.request.urlopen(req, timeout=20).read()  # noqa: S310  # nosec B310 - literal host
    except Exception as exc:  # noqa: BLE001 - a failed alert must not fail the check
        print(f"telegram failed: {exc}")


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack", "-X", "-A", "-t",
         "-F", "\x1f", "-c", sql],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def state() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - first run, or a file we will rewrite anyway
        return {}


def save(d: dict) -> None:
    with open(STATE, "w") as f:
        json.dump(d, f)
    os.chmod(STATE, 0o600)


def main() -> int:
    st = state()
    since = st.get("since") or (dt.datetime.now(dt.timezone.utc)
                                - dt.timedelta(hours=1)).isoformat()
    now = dt.datetime.now(dt.timezone.utc)
    lines: list[str] = []

    calls = psql(
        "SELECT ts, status_code, coalesce(ip_org,'(ohne)'), coalesce(ip_country,'?') "
        f"FROM request_log WHERE endpoint ILIKE '{ROUTE}' AND ts > '{since}' ORDER BY ts")
    for ts, code, org, land in calls:
        lines.append(f"  call {ts} HTTP {code} from {org} / {land}")

    # The gate is the only verifier we ship. A governance attestation fails it on
    # payload shape, so a redemption attempt reads as one of these reasons.
    try:
        gate = psql(
            "SELECT ts, coalesce(did,'?'), path, reason FROM gate_decisions "
            f"WHERE ts > '{since}' AND reason IN "
            "('attestation_invalid','attestation_missing','attestation_expired') ORDER BY ts")
        for ts, did, path, reason in gate:
            lines.append(f"  gate  {ts} {reason} did={did} path={path}")
    except Exception as exc:  # noqa: BLE001 - the table may not exist on every host
        print(f"gate_decisions unreadable: {exc}")

    if lines:
        telegram("ALERTS — disabled attestation route\n"
                 f"since {since}\n" + "\n".join(lines[:25])
                 + ("\n  …" if len(lines) > 25 else "")
                 + "\n\nThe route answers 503. Nothing it signed is being renewed.")

    if now > WINDOW_END and not st.get("closed"):
        telegram(
            "ALERTS — attestation window closed\n"
            f"The last attestation /guard/governance/validate-capabilities could have "
            f"issued expired at {WINDOW_END:%Y-%m-%d %H:%M}Z at the latest "
            "(2026-10-01 16:41 + the 7-day cap).\n"
            "No key rotation was needed. Nothing it signed is valid any more.\n"
            "This watch stops alerting now.")
        st["closed"] = True

    st["since"] = now.isoformat()
    save(st)
    if not lines:
        print(f"quiet · window {'closed' if now > WINDOW_END else 'open until ' + WINDOW_END.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
