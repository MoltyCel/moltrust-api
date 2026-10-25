#!/usr/bin/env python3
"""watch-probe-dids — 30 days of every request three probing agents make to /credentials and the gates.

On 2026-10-24 three a2a agents, registered minutes earlier from one cloud
address, had POST /credentials/issue sign AdminTrustCredential,
OperatorCredential and RootCredential for themselves (ops CRED-2410). The route
is closed since #938 and the nine credentials are revoked. Lars decided on
2026-10-25 not to block the three, but to watch them for 30 days.

Every hour this reads, for the three DIDs and since the last run:

- request_log rows on /credentials/* and /canary/*
- gate_decisions rows (the MoltGuard gate)

and appends each row to data/watch_probe_dids.jsonl, the protocol. A message
goes to ALERTS only for a new bypass attempt:

- /credentials/* or /canary/* answered 400, 403 or 422: a type, a subject or a
  canary the route refused
- a gate decision attestation_invalid or proof_replayed: a forged, altered or
  replayed attestation

Any other row is recorded and not sent. The watch ends by itself after
2026-11-24 and writes status `expired`; the cron line is then removed together
with the expectations block.

  python scripts/watch_probe_dids.py            the hourly run
  python scripts/watch_probe_dids.py --dry-run  read and print, no state, no message
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DIDS = (
    "did:moltrust:bafa2b561e49f7ab",   # recheck-agent-23042b
    "did:moltrust:f9956ffeeec5a110",   # recheck-agent-23160a
    "did:moltrust:c765c2ec2670ff49",   # revcheck-23173-14156
)
# 30 days from Lars' decision on 2026-10-25.
WATCH_FROM = dt.datetime(2026, 10, 25, 0, 0, tzinfo=dt.timezone.utc)
WATCH_END = dt.datetime(2026, 11, 24, 23, 59, 59, tzinfo=dt.timezone.utc)

REFUSED = {"400", "403", "422"}
FORGED = {"attestation_invalid", "proof_replayed"}

STATE = ROOT / "data" / "watch_probe_dids_state.json"
HEARTBEAT = ROOT / "data" / "watch_probe_dids_heartbeat.json"
PROTOCOL = ROOT / "data" / "watch_probe_dids.jsonl"

_DID_LIST = ",".join(f"'{d}'" for d in DIDS)

REQUESTS_SQL = f"""
SELECT 'request', ts, agent_did, method || ' ' || endpoint, status_code::text, ''
  FROM request_log
 WHERE agent_did IN ({_DID_LIST})
   AND (endpoint LIKE '/credentials/%' OR endpoint LIKE '/canary/%')
   AND ts > :'since'::timestamptz
"""

GATES_SQL = f"""
SELECT 'gate', ts, did, coalesce(path, ''), coalesce(reason, ''), left(coalesce(detail, ''), 160)
  FROM gate_decisions
 WHERE did IN ({_DID_LIST})
   AND ts > :'since'::timestamptz
"""


def psql(sql: str, **params) -> list[list[str]]:
    binds: list[str] = []
    for name, value in params.items():
        binds += ["-v", f"{name}={value}"]
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-F", "\t", "-t", *binds, "-f", "-"],
        input=sql, capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(f"psql: {out.stderr[:300]}")
    # Only the newline goes: an empty last column is still a column.
    return [l.split("\t") for l in out.stdout.split("\n") if l.strip()]


def rows_since(since: str, query=psql) -> list[dict]:
    rows = []
    for sql in (REQUESTS_SQL, GATES_SQL):
        for kind, ts, did, what, code, detail in query(sql, since=since):
            rows.append({"kind": kind, "ts": ts, "did": did, "what": what,
                         "code": code, "detail": detail})
    rows.sort(key=lambda r: r["ts"])
    return rows


def is_attempt(row: dict) -> bool:
    if row["kind"] == "request":
        return row["code"] in REFUSED
    return row["code"] in FORGED


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def heartbeat(status: str, detail: str, now: dt.datetime) -> None:
    write_json(HEARTBEAT, {"timestamp": now.isoformat(timespec="seconds"),
                           "status": status, "detail": detail})
    print(f"{status}: {detail}")


def message(attempts: list[dict]) -> str:
    lines = [f"{r['ts'][:19]}Z …{r['did'][-8:]} {r['what']} -> {r['code']}"
             + (f" ({r['detail']})" if r["detail"] else "") for r in attempts[:20]]
    more = f"\n… und {len(attempts) - 20} weitere" if len(attempts) > 20 else ""
    return (f"Beobachtete Agenten (CRED-2410): {len(attempts)} neue abgewiesene Anfrage(n) "
            "an /credentials, /canary oder ein Gate.\n" + "\n".join(lines) + more
            + "\nDie Anfragen wurden abgewiesen. Protokoll: data/watch_probe_dids.jsonl")


def main(argv=None, now: dt.datetime | None = None, query=psql) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="read and print, no state, no message")
    args = ap.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    if now > WATCH_END:
        heartbeat("expired", f"30-day watch ended {WATCH_END:%Y-%m-%d}; remove the cron line "
                  "and the expectations block", now)
        return 0
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    since = state.get("since") or WATCH_FROM.isoformat()
    try:
        rows = rows_since(since, query)
    except Exception as exc:  # noqa: BLE001 - reported as status, never a quiet zero
        heartbeat("error", f"not read: {type(exc).__name__}: {str(exc)[:160]}", now)
        return 1
    attempts = [r for r in rows if is_attempt(r)]
    detail = f"{len(rows)} new row(s) since {since[:19]}, {len(attempts)} bypass attempt(s)"
    if args.dry_run:
        for r in rows:
            print(("ATTEMPT " if is_attempt(r) else "        ") + json.dumps(r))
        print(detail)
        return 0
    if rows:
        PROTOCOL.parent.mkdir(parents=True, exist_ok=True)
        with PROTOCOL.open("a") as fh:
            for r in rows:
                fh.write(json.dumps({**r, "attempt": is_attempt(r)}) + "\n")
        since = rows[-1]["ts"]
    if attempts:
        from app import notify
        notify.send_telegram(message(attempts), channel=notify.ALERTS)
    write_json(STATE, {"since": since})
    heartbeat("finding" if attempts else "ok", detail, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
