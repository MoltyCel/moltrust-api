"""Write test/enforce-core-vectors.json from the Python enforce engine.

The JavaScript port in enforce-core.js has to reach the same verdict, reason,
grant_index and a byte-identical core_digest as moltrust_enforce._core. This
script records what the Python implementation says for two sets of inputs:

- the enforce_check vectors of MoltyCel/aae-conformance-vectors (vectors/enforce,
  the enforce_check vectors, those with a mandate in their input), whose expected core_digest is
  asserted here as well, so the file cannot drift from the conformance suite;
- cases the conformance suite does not cover: delegation chains (grant
  attenuation), prev_core_digest, unknown constraint types of every JSON type,
  non-ASCII field names, values that cannot be canonicalized.

Usage, from the repository root:

    pip install -e sdk/python
    python packages/x402/test/make_core_vectors.py <aae-conformance-vectors checkout> <commit>
"""
from __future__ import annotations

import glob
import json
import os
import sys

from moltrust_enforce import action_digest, enforce_check

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "enforce-core-vectors.json")


def run(inp: dict) -> dict:
    r = enforce_check(inp.get("mandate"), inp.get("transaction"),
                      prev_core_digest=inp.get("prev_core_digest"),
                      ancestors=inp.get("ancestors"))
    return {"verdict": r["verdict"], "reason": r["reason"], "grant_index": r["grant_index"],
            "core_digest": r["core_digest"]}


def grant(action: dict, disposition: str = "allow", constraints=None, type_fields=None) -> dict:
    return {
        "action_binding": action_digest(action),
        "type_fields": type_fields or sorted(action.keys()),
        "disposition": disposition,
        "constraints": constraints if constraints is not None else [],
    }


PAY = {"verb": "x402.pay", "scheme": "exact", "network": "eip155:8453",
       "asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"}
TX = {"action": PAY, "from": "0x2222222222222222222222222222222222222222",
      "to": "0x1111111111111111111111111111111111111111", "amount": 10000,
      "nonce": "0x" + "ab" * 32}
TO = {"type": "exact", "field": "to", "value": TX["to"]}
TO_ENUM = {"type": "enum", "field": "to", "values": [TX["to"], "0x" + "33" * 20]}
CAP = {"type": "range", "field": "amount", "lo": 0, "hi": 50000}
CAP_SMALL = {"type": "range", "field": "amount", "lo": 0, "hi": 20000}
CAP_TINY = {"type": "range", "field": "amount", "lo": 0, "hi": 100}

PARENT = {"mandate_version": "1.0", "grants": [grant(PAY, constraints=[TO_ENUM, CAP])]}
CHILD = {"mandate_version": "1.0", "grants": [grant(PAY, constraints=[TO, CAP_SMALL])]}
GRANDCHILD = {"mandate_version": "1.0", "grants": [grant(PAY, constraints=[TO, CAP_SMALL])]}
CHILD_BROADER = {"mandate_version": "1.0", "grants": [grant(PAY, constraints=[TO])]}
PARENT_FORBID = {"mandate_version": "1.0",
                 "grants": [grant(PAY, "forbid"), grant(PAY, constraints=[TO, CAP])]}
PARENT_HOLD_FIRST = {"mandate_version": "1.0",
                     "grants": [grant(PAY, "hold", constraints=[CAP]),
                                grant(PAY, constraints=[TO_ENUM, CAP])]}
PARENT_HOLD_DISJOINT = {"mandate_version": "1.0",
                        "grants": [grant(PAY, "hold", constraints=[
                                       {"type": "range", "field": "amount", "lo": 30000,
                                        "hi": 50000}]),
                                   grant(PAY, constraints=[TO_ENUM, CAP])]}
CHILD_HOLD_VS_ALLOW = {"mandate_version": "1.0",
                       "grants": [grant(PAY, "allow", constraints=[TO, CAP_SMALL])]}
PARENT_HOLD_ONLY = {"mandate_version": "1.0", "grants": [grant(PAY, "hold", constraints=[CAP])]}
OTHER_ACTION = dict(PAY, network="eip155:1")
CHILD_OTHER = {"mandate_version": "1.0", "grants": [grant(OTHER_ACTION, constraints=[TO])]}


def unknown_type(t):
    return {"mandate_version": "1.0",
            "grants": [grant(PAY, constraints=[{"type": t, "field": "to", "value": "x"}])]}


UNICODE_ACTION = {"verb": "x402.pay", "zz": "1", "é": "2", "\U0001F600": "3", "～": "4"}

GENERATED = [
    ("chain-one-hop-permit", {"mandate": CHILD, "transaction": TX, "ancestors": [PARENT]}),
    ("chain-two-hops-permit", {"mandate": GRANDCHILD, "transaction": TX,
                               "ancestors": [PARENT, CHILD]}),
    ("chain-child-broader-deny", {"mandate": CHILD_BROADER, "transaction": TX,
                                  "ancestors": [PARENT]}),
    ("chain-parent-forbids-deny", {"mandate": CHILD, "transaction": TX,
                                   "ancestors": [PARENT_FORBID]}),
    ("chain-parent-holds-first-deny", {"mandate": CHILD_HOLD_VS_ALLOW, "transaction": TX,
                                       "ancestors": [PARENT_HOLD_FIRST]}),
    ("chain-parent-hold-disjoint-permit", {"mandate": CHILD_HOLD_VS_ALLOW, "transaction": TX,
                                           "ancestors": [PARENT_HOLD_DISJOINT]}),
    ("chain-allow-under-hold-deny", {"mandate": CHILD_HOLD_VS_ALLOW, "transaction": TX,
                                     "ancestors": [PARENT_HOLD_ONLY]}),
    ("chain-other-action-deny", {"mandate": CHILD_OTHER, "transaction": TX,
                                 "ancestors": [PARENT]}),
    ("chain-ancestors-not-a-list-deny", {"mandate": CHILD, "transaction": TX,
                                         "ancestors": {"0": PARENT}}),
    ("chain-too-deep-deny", {"mandate": CHILD, "transaction": TX, "ancestors": [PARENT] * 9}),
    ("chain-empty-list-permit", {"mandate": CHILD, "transaction": TX, "ancestors": []}),
    ("prev-core-digest-permit", {"mandate": PARENT, "transaction": TX,
                                 "prev_core_digest": "sha256:" + "0" * 64}),
    ("prev-core-digest-not-a-string", {"mandate": PARENT, "transaction": TX,
                                       "prev_core_digest": 7}),
    ("range-outside-deny", {"mandate": {"grants": [grant(PAY, constraints=[CAP_TINY])]},
                            "transaction": TX}),
    ("hold-pending", {"mandate": PARENT_HOLD_ONLY, "transaction": TX}),
    ("unknown-type-null", {"mandate": unknown_type(None), "transaction": TX}),
    ("unknown-type-number", {"mandate": unknown_type(5), "transaction": TX}),
    ("unknown-type-bool", {"mandate": unknown_type(True), "transaction": TX}),
    ("unknown-type-list", {"mandate": unknown_type(["exact", 1]), "transaction": TX}),
    ("unknown-type-object", {"mandate": unknown_type({"a": "it's"}), "transaction": TX}),
    ("unknown-type-string", {"mandate": unknown_type("prefix"), "transaction": TX}),
    ("non-ascii-action-keys-permit", {"mandate": {"grants": [grant(UNICODE_ACTION)]},
                                      "transaction": {"action": UNICODE_ACTION}}),
    ("non-ascii-action-keys-missing-deny",
     {"mandate": {"grants": [grant(UNICODE_ACTION)]},
      "transaction": {"action": {k: v for k, v in UNICODE_ACTION.items() if k != "zz"}}}),
    ("unsafe-integer-outside-action-deny", {"mandate": PARENT,
                                            "transaction": dict(TX, extra=2 ** 53)}),
    ("lone-surrogate-in-transaction-deny", {"mandate": PARENT,
                                            "transaction": dict(TX, memo="\ud800")}),
    ("action-missing-deny", {"mandate": PARENT, "transaction": {"to": TX["to"]}}),
    ("transaction-not-object-deny", {"mandate": PARENT, "transaction": ["x"]}),
    ("mandate-missing-deny", {"mandate": None, "transaction": TX}),
]


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    acv, commit = sys.argv[1], sys.argv[2]
    cases = []
    for path in sorted(glob.glob(os.path.join(acv, "vectors", "enforce", "*.json"))):
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        if "mandate" not in v.get("input", {}):
            continue
        got = run(v["input"])
        want = v["expected"]
        if got["core_digest"] != want["core_digest"] or got["verdict"] != want["verdict"]:
            sys.exit(f"{v['id']}: Python disagrees with the conformance suite")
        cases.append({"id": v["id"], "source": f"aae-conformance-vectors@{commit}:"
                      f"vectors/enforce/{os.path.basename(path)}",
                      "input": v["input"], "expected": got})
    for cid, inp in GENERATED:
        cases.append({"id": cid, "source": "generated", "input": inp, "expected": run(inp)})
    out = {
        "description": "Expected enforce_check results from the Python implementation "
                       "(moltrust_enforce._core), replayed by enforce-core.js. "
                       "Written by test/make_core_vectors.py; do not edit by hand.",
        "enforce_version": "3.0",
        "vectors": cases,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, ensure_ascii=True)
        fh.write("\n")
    print(f"{len(cases)} vectors -> {OUT}")
    x402_recompute()


X402_VECTORS = os.path.join(HERE, "..", "..", "..", "sdk", "python", "tests", "x402-vectors.json")
X402_OUT = os.path.join(HERE, "x402-python-recompute.json")


def x402_recompute() -> None:
    """For every x402 vector with a receipt: the core_digest Python recomputes.

    The vectors carry the digest the receipt claims. For a payment the receipt
    was not made for, the recomputed digest differs from the claim, and only
    this file says what Python computed instead, so the JS test can compare it
    byte for byte.
    """
    from moltrust_enforce.x402_gate import ReceiptError, x402_transaction

    with open(X402_VECTORS, encoding="utf-8") as fh:
        vectors = json.load(fh)["vectors"]
    rows = []
    for v in vectors:
        p = v["payment_payload"]
        receipt = (p.get("extensions") or {}).get("moltrust")
        if not isinstance(receipt, dict) or not isinstance(receipt.get("mandate"), dict):
            continue
        try:
            tx = x402_transaction(p, v.get("requirements"))
        except ReceiptError:
            continue
        core = (receipt.get("record") or {}).get("core") or {}
        r = enforce_check(receipt["mandate"], tx, prev_core_digest=core.get("prev_core_digest"),
                          ancestors=receipt.get("ancestors"))
        rows.append({"id": v["id"], "transaction": tx, "verdict": r["verdict"],
                     "core_digest": r["core_digest"]})
    with open(X402_OUT, "w", encoding="utf-8") as fh:
        json.dump({"description": "core_digest recomputed by moltrust_enforce for each x402 "
                                  "vector with a receipt (sdk/python/tests/x402-vectors.json). "
                                  "Written by test/make_core_vectors.py.",
                   "vectors": rows}, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"{len(rows)} x402 recomputations -> {X402_OUT}")


if __name__ == "__main__":
    main()
