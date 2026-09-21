#!/usr/bin/env python3
"""Does proving a MolTrust identity change what paying agents do?

    python3 scripts/gate_measure.py --collect    # every 15 minutes, from cron
    python3 scripts/gate_measure.py --report     # totals since the gate went live

Three figures, decided before the gate was deployed so that the answer could
not be chosen afterwards:

  1. registrations with platform='gate'
  2. the share of priced requests that were discounted
  3. verification calls made by somebody other than the subject

The second cannot be read off the live endpoint alone. moltguard counts in
process memory and labels it `since_process_start`, so a restart erases the
measurement. --collect samples the endpoint and stores the increase since the
last sample; --report sums those increases.

Exit 0 on success, 1 when a figure could not be established. Nothing here
writes to moltguard or to anything on the request path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

GATE_STATS_URL = os.getenv(
    "GATE_STATS_URL", "https://api.moltrust.ch/guard/moltrust/gate-stats"
)

# The gate went live with this deploy. Everything is measured from here, and
# the constant is written down rather than derived so that a later reading
# cannot quietly move the starting line.
GATE_DEPLOYED_AT = "2026-09-21 21:02:08+00"

# Callers that are us. 57.129.23.0/24 is Harald's OVH range (expected traffic,
# not external demand), 46.225.175.x is the host that serves the site.
OUR_IP_PREFIXES = ("57.129.23.", "46.225.175.")

# Subjects that are our own, so a lookup of them is a probe rather than demand.
# The placeholder forms are what a client sends when it pastes the documented
# path without substituting anything.
# Addresses taken from BASE_ADDR / MOLTGUARD_WALLET on the host and from the
# default in moltguard's x402 middleware, not typed from a report. An earlier
# draft of this list carried 0x3802cE7B…, which is not an address we hold —
# the productive wallet is 0x38023834…, and the two differ after four
# characters. Matching is case-insensitive because the log stores what the
# caller sent.
# Only real addresses belong here. An earlier draft also listed the
# "{address}" placeholder forms, which contain a literal % — a LIKE wildcard —
# so those entries silently matched far more than themselves. They are
# redundant in any case: a placeholder does not satisfy SUBJECT_SHAPE, so the
# shape test already removes it.
OUR_SUBJECTS = (
    "0x0000000000000000000000000000000000000000",
    "0x0000000000000000000000000000000000000001",
    "0x380238347e58435f40B4da1F1A045A271D5838F5",
    "0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38",
)

# The endpoints that answer "is this agent what it claims to be". A call here
# for somebody else's subject is the demand signal the gate exists to test.
#
# /api/graph/score/ and /api/wallet/attest/ are deliberately absent. They
# answer a different question, and they are scanner magnets: the traffic on
# them is /api/graph/score/..%5C..%5C../etc/passwd and
# /api/wallet/attest/WEB-INF/web.xml. Including them put 155 probe requests
# into a figure that is supposed to mean somebody wanted an agent verified.
VERIFY_SURFACE = (
    "/api/agent/score/",
    "/skill/verify/",
    "/vc/verify-binding",
    "/travel/verify",
    "/shopping/verify",
)

# A real subject is an EVM address or a MolTrust DID. Testing the shape rather
# than listing what to exclude is the difference between a filter that holds
# and one that needs extending every time a scanner invents a new path — the
# same scan produced `1`, `WEB-INF/web.xml` and a traversal string, and no
# denylist would have caught all three.
SUBJECT_SHAPE = r"(0x[0-9a-fA-F]{40}|did:moltrust:[0-9a-f]{16})"


def fetch_stats() -> dict:
    if not GATE_STATS_URL.startswith("https://"):
        raise ValueError("GATE_STATS_URL must be https")
    req = urllib.request.Request(
        GATE_STATS_URL, headers={"User-Agent": "moltrust-gate-measure/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310 - scheme checked above
        body = json.loads(response.read())
    for key in ("priced_requests", "discounted_requests"):
        if not isinstance(body.get(key), int):
            raise ValueError(f"gate-stats has no integer {key}: {body!r}")
    return body


def connect(dsn: str):
    import psycopg2  # imported late so --help works without the driver

    return psycopg2.connect(dsn)


def collect(dsn: str) -> int:
    stats = fetch_stats()
    priced = stats["priced_requests"]
    discounted = stats["discounted_requests"]
    reasons = stats.get("denied_by_reason") or {}

    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT priced_raw, discounted_raw FROM gate_measurement "
            "ORDER BY measured_at DESC, id DESC LIMIT 1"
        )
        row = cur.fetchone()

        if row is None:
            # First sample. Everything the process has served counts, because
            # the process started at the deploy this measurement dates from.
            priced_delta, discounted_delta, restarted = priced, discounted, False
        elif priced < row[0] or discounted < row[1]:
            # Counter went backwards, so the process restarted. The current
            # value is the whole of what the new process has served; whatever
            # the old one served after the last sample is gone.
            priced_delta, discounted_delta, restarted = priced, discounted, True
        else:
            priced_delta = priced - row[0]
            discounted_delta = discounted - row[1]
            restarted = False

        cur.execute(
            "INSERT INTO gate_measurement (priced_raw, discounted_raw, priced_delta, "
            "discounted_delta, denied_by_reason, restarted) VALUES (%s,%s,%s,%s,%s,%s)",
            (priced, discounted, priced_delta, discounted_delta,
             json.dumps(reasons), restarted),
        )
    print(f"sampled: priced {priced} (+{priced_delta}), "
          f"discounted {discounted} (+{discounted_delta})"
          + (", process restarted" if restarted else ""))
    return 0


def report(dsn: str) -> int:
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(priced_delta),0), COALESCE(SUM(discounted_delta),0), "
            "       COUNT(*) FILTER (WHERE restarted), MIN(measured_at), MAX(measured_at) "
            "  FROM gate_measurement WHERE measured_at >= %s::timestamptz",
            (GATE_DEPLOYED_AT,),
        )
        samples, priced, discounted, restarts, first, last = cur.fetchone()

        cur.execute(
            "SELECT COUNT(*) FROM agents WHERE platform = 'gate' "
            "  AND created_at >= %s::timestamptz",
            (GATE_DEPLOYED_AT,),
        )
        gate_registrations = cur.fetchone()[0]

        # Static SQL with array parameters rather than a built-up list of
        # LIKE clauses: the patterns are module constants, but a query
        # assembled by string concatenation reads as an injection risk to
        # every reader and to bandit, and LIKE ANY / NOT LIKE ALL says the
        # same thing without the assembly.
        # Answered and refused are counted apart. A 402 on a verification
        # endpoint is somebody asking to have an agent checked and declining
        # to pay for it, which is the demand this gate exists to measure —
        # `status_code < 400` drops it, and on /api/agent/score/ that is 843
        # of 863 requests. Reporting only the 2xx would have said six.
        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE r.status_code < 400), "
            "       COUNT(*) FILTER (WHERE r.status_code = 402), "
            "       COUNT(DISTINCT r.ip) FROM request_log r "
            " WHERE r.source = 'moltguard' "
            "   AND r.ts >= %s::timestamptz "
            "   AND r.endpoint LIKE ANY(%s) "
            "   AND r.endpoint ~ %s "
            "   AND lower(r.endpoint) NOT LIKE ALL(%s) "
            "   AND COALESCE(r.ip, '') NOT LIKE ALL(%s)",
            (GATE_DEPLOYED_AT,
             [f"%{p}%" for p in VERIFY_SURFACE],
             SUBJECT_SHAPE,
             [f"%{p.lower()}%" for p in OUR_SUBJECTS],
             [f"{p}%" for p in OUR_IP_PREFIXES]),
        )
        foreign_answered, foreign_refused, foreign_callers = cur.fetchone()

        cur.execute(
            "SELECT denied_by_reason FROM gate_measurement "
            " WHERE measured_at >= %s::timestamptz ORDER BY measured_at DESC, id DESC LIMIT 1",
            (GATE_DEPLOYED_AT,),
        )
        latest_reasons = (cur.fetchone() or [{}])[0] or {}

    if samples == 0:
        print(f"Keine Messpunkte seit {GATE_DEPLOYED_AT}. --collect laeuft nicht.",
              file=sys.stderr)
        return 1

    share = (discounted / priced * 100) if priced else 0.0
    print(f"MolTrust-Gate, Messung seit {GATE_DEPLOYED_AT}")
    print(f"  Messpunkte           {samples} ({first:%d.%m %H:%M} bis {last:%d.%m %H:%M} UTC)"
          + (f", davon {restarts} nach Neustart" if restarts else ""))
    print(f"  Registrierungen gate {gate_registrations}")
    print(f"  Bepreiste Requests   {priced}")
    print(f"  Davon rabattiert     {discounted}  ({share:.1f} %)")
    print(f"  Verify fremd         {foreign_answered} beantwortet, {foreign_refused} mit 402 "
          f"abgewiesen, von {foreign_callers} Aufrufern")
    if latest_reasons:
        mix = ", ".join(f"{k} {v}" for k, v in sorted(latest_reasons.items()))
        print(f"  Ablehnungsgruende    {mix}")
    if restarts:
        print("  Ein Neustart zwischen zwei Messpunkten verliert, was das alte "
              "Verfahren danach noch bedient hat. Die Zahl ist dann eine Untergrenze.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--collect", action="store_true")
    group.add_argument("--report", action="store_true")
    ap.add_argument("--dsn", default=os.environ.get(
        "DATABASE_URL", "postgresql://moltstack@localhost/moltstack"))
    args = ap.parse_args()

    try:
        return collect(args.dsn) if args.collect else report(args.dsn)
    except Exception as exc:  # noqa: BLE001 - the message is the output
        print(f"gate_measure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
