#!/usr/bin/env python3
"""Ask Coinbase whether our paid endpoints are still indexable, hourly.

CDP runs a read-only validator for the x402 Bazaar. It fetches a resource
without paying and reports 26 preflight checks plus the verdict the Bazaar
crawler would reach. It is the only authority on that question: the crawler's
rules are not published in full, and a manifest that looks right to us has
already been wrong in ways only the crawler could see.

    POST https://api.cdp.coinbase.com/platform/v2/x402/validate
    {"resource": "<https url>"}

Two failure shapes this catches, both found on 2026-09-21 by running it:

  * a bare resource URL that answers 307 instead of 402. FastAPI redirects
    `/x` to `/x/`, the crawler probes the bare URL and does not follow, so the
    endpoint is rejected while a browser sees it working perfectly.
  * an endpoint the manifest prices that answers 404 to a GET probe, because
    it only accepts POST. The invoice is advertised; the resource is not
    reachable the way the crawler reaches it.

Three endpoints are checked rather than all eleven: one per distinct handler
shape and price point. They share nginx, the middleware and the manifest, so a
regression in any of those shows up here, while three calls an hour stay a
courteous load on someone else's validator.

    python3 scripts/x402_validator_check.py [--json]

Exit 0 ok, 1 drift, 2 unverifiable. Unverifiable is not drift: CDP being down
says nothing about our endpoints, and an alarm that cannot tell the two apart
is one people mute.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

VALIDATE_URL = "https://api.cdp.coinbase.com/platform/v2/x402/validate"

# A wallet that exists, so the handler runs its real path instead of erroring
# out early on a malformed address. The validator never pays, so the response
# body is irrelevant — but a 400 from argument parsing would be read as "not a
# 402 endpoint" and reported as drift.
SAMPLE_ADDRESS = "0x3802cE7B2Ff8500D9dBFDE4dF69fE2C0F86238F5"
SAMPLE_MARKET = "0x1"

ENDPOINTS = (
    ("agent-score", f"https://api.moltrust.ch/guard/api/agent/score/{SAMPLE_ADDRESS}"),
    ("sybil-scan", f"https://api.moltrust.ch/guard/api/sybil/scan/{SAMPLE_ADDRESS}"),
    ("market-check", f"https://api.moltrust.ch/guard/api/market/check/{SAMPLE_MARKET}"),
)

STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state", "x402_validator.json",
)


def validate(resource: str, timeout: float = 40.0) -> dict:
    """Return the validator's verdict, or {"unverifiable": reason}."""
    req = urllib.request.Request(
        VALIDATE_URL,
        data=json.dumps({"resource": resource}).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "User-Agent": "moltrust-x402-driftcheck/1.0 (+https://moltrust.ch)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — constant https URL  # nosec B310
            body = json.load(r)
    except urllib.error.HTTPError as e:
        # 4xx/5xx from CDP is CDP's problem, not a statement about us.
        return {"unverifiable": f"validator answered HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001 — network, DNS, TLS, malformed JSON
        return {"unverifiable": f"validator unreachable: {type(e).__name__}"}

    preflight = body.get("preflight") or []
    failed = [c for c in preflight if not c.get("passed")]
    return {
        "checks": len(preflight),
        "failed": [
            {"check": c.get("check"), "severity": c.get("severity"), "detail": c.get("detail")}
            for c in failed
        ],
        "valid": bool(body.get("valid")),
        "outcome": (body.get("simulation") or {}).get("outcome"),
        "reason": (body.get("simulation") or {}).get("reason"),
        "indexed": body.get("index") is not None,
    }


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — a missing or corrupt state file is a first run
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception as e:  # noqa: BLE001 — losing the state file must not fail the check
        print(f"warn: could not write {STATE_PATH}: {type(e).__name__}", file=sys.stderr)


def run_checks() -> list:
    """Validate every endpoint and classify each against the last known state.

    Each result carries ``ok`` (False means alert), ``changed`` (the state
    differs from the previous run) and a one-line ``detail``.
    """
    previous = _load_state()
    state = {}
    results = []

    for name, url in ENDPOINTS:
        res = validate(url)
        if "unverifiable" in res:
            results.append({
                "name": name, "ok": True, "unverifiable": True, "changed": False,
                "detail": res["unverifiable"],
            })
            # Keep the previous state: an unreachable validator must not erase
            # the baseline that the next real answer is compared against.
            if name in previous:
                state[name] = previous[name]
            continue

        blocking = [c for c in res["failed"] if c["severity"] == "required"]
        now = {"valid": res["valid"], "outcome": res["outcome"], "indexed": res["indexed"]}
        state[name] = now
        before = previous.get(name)
        changed = before is not None and before != now

        if blocking:
            first = blocking[0]
            detail = (
                f"{len(blocking)} of {res['checks']} required checks fail — "
                f"{first['check']}: {first['detail']}"
            )
            if res.get("reason"):
                detail += f" (crawler: {res['reason']})"
            results.append({"name": name, "ok": False, "changed": changed, "detail": detail})
            continue

        detail = f"{res['checks']} checks pass, crawler would {res['outcome']}"
        detail += ", indexed in the Bazaar" if res["indexed"] else ", not yet indexed"
        results.append({"name": name, "ok": True, "changed": changed, "detail": detail})

    _save_state(state)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    results = run_checks()
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            if r.get("unverifiable"):
                mark = "??"
            else:
                mark = "ok" if r["ok"] else "DRIFT"
            print(f"[{mark:5}] {r['name']:13} {r['detail']}")

    if any(not r["ok"] for r in results):
        return 1
    if all(r.get("unverifiable") for r in results):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
