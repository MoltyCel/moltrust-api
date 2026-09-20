#!/usr/bin/env python3
"""The facilitator we name must settle every network we charge on.

We advertised Base mainnet in `accepts` while naming https://x402.org/facilitator,
whose /supported lists eip155:84532 and base-sepolia and not eip155:8453. Payments
worked anyway because MoltGuard settles through CDP mainnet — so the published
manifest described something other than what runs, and anyone building against
the document would have pointed at a facilitator that cannot settle our invoices.

    python3 scripts/x402_facilitator_check.py [--manifest .well-known/x402.json]

Exit 0 ok, 1 drift, 2 unverifiable. Cron should alert on 1 only: a facilitator
that needs an API key is not a fault, and an alarm that cannot tell the two
apart is one people mute.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def supported_networks(facilitator: str):
    """(networks, reason). networks is None when the answer is unknown.

    A facilitator that needs a key is not the same as one that drifted, and a
    weekly alarm that cannot tell them apart gets muted.
    """
    url = facilitator.rstrip("/") + "/supported"
    # A default urllib User-Agent gets a 403 from x402.org, which reads exactly
    # like an auth wall and is not one. Say who we are.
    req = urllib.request.Request(url, headers={
        "User-Agent": "moltrust-facilitator-check/1.0 (+https://moltrust.ch)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None, f"authentifiziert ({e.code}) — anonym nicht prüfbar"
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, type(e).__name__
    kinds = body.get("kinds", body)
    if not isinstance(kinds, list):
        return None, "unerwartete Antwortform"
    return {str(k.get("network")) for k in kinds if isinstance(k, dict)}, "gelesen"


def main() -> int:
    ap = argparse.ArgumentParser()
    # The manifest is a moltrust-web artifact; this check is server-side
    # operations tooling and lives here because this is the repo the box has
    # checked out. Default points at what nginx actually serves.
    ap.add_argument("--manifest", default="/var/www/html/.well-known/x402.json")
    args = ap.parse_args()

    m = json.load(open(args.manifest))
    declared = {str(e.get("network", m.get("network"))) for e in m.get("endpoints", [])}
    declared.discard("None")
    facilitator = m.get("facilitator", "")

    print(f"Manifest      : {args.manifest}")
    print(f"facilitator   : {facilitator}")
    print(f"accepts-Netze : {', '.join(sorted(declared)) or '(keine)'}")

    nets, reason = supported_networks(facilitator)
    if nets is None:
        print(f"supported     : {reason}")
        print("VERDICT: UNGEPRUEFT — kein Drift behauptet, aber auch nicht ausgeschlossen")
        return 2

    # Exact membership. A substring test passes eip155:8453 against
    # eip155:84532, which is how this went unnoticed the first time.
    missing = {d for d in declared if d not in nets}
    print(f"unterstützt   : {', '.join(sorted(nets))}")
    if missing:
        print(f"VERDICT: DRIFT — {', '.join(sorted(missing))} wird nicht unterstützt")
        return 1
    print("VERDICT: ok — jedes berechnete Netz wird vom genannten Facilitator abgewickelt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
