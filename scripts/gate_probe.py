#!/usr/bin/env python3
"""Ask a paid endpoint what it would charge you, with and without your MolTrust identity.

Nothing is paid and nothing is registered. The script asks one URL for its price
twice — once plain, once presenting your attestation — and prints both answers.
The difference is the discount.

You need a DID and its Ed25519 private key. No wallet key is used here and none
is asked for; the wallet only matters earlier, when the track record is issued.

    # You already have a DID:
    python3 gate_probe.py --did did:moltrust:... --key <64-hex-chars>
    python3 gate_probe.py --did did:moltrust:... --key-file ./agent.key

    # You do not:
    python3 gate_probe.py --register

Requires `pynacl` for Ed25519. Everything else is the standard library.

    pip install pynacl

Nothing in this file is specific to us. Point --api and --endpoint at any host
running @moltrust/x402 or moltrust-x402 and it behaves the same way.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.error
import urllib.request

DEFAULT_API = "https://api.moltrust.ch"
DEFAULT_GUARD = "https://api.moltrust.ch/guard"
DEFAULT_ENDPOINT = "/api/agent/score/0x0000000000000000000000000000000000000000"
BINDING_VERSION = "moltrust-gate/v1"
UA = {"User-Agent": "moltrust-gate-probe/1.0", "Accept": "application/json"}


def http(method, url, body=None, headers=None, timeout=30):
    h = dict(UA)
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # nosec B310 - host comes from --api/--guard
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, {"raw": raw[:400].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:400].decode("utf-8", "replace")}


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def quoted_amount(body) -> int | None:
    """The price out of a 402 body, or None when there is not one."""
    accepts = ((body or {}).get("x402") or {}).get("accepts") or []
    if not accepts:
        return None
    try:
        return int(accepts[0]["amount"])
    except (KeyError, TypeError, ValueError):
        return None


def register(api: str) -> int:
    """Mint a DID and print its key. Two calls, no account, no payment."""
    from nacl.signing import SigningKey

    status, ch = http("GET", f"{api}/identity/register-challenge")
    if status != 200:
        print(f"register-challenge failed: HTTP {status}\n{json.dumps(ch)[:300]}")
        return 1

    sk = SigningKey.generate()
    pub = sk.verify_key.encode().hex()

    # Proof of work: find a nonce whose sha256(seed + nonce) starts with
    # `difficulty_bits` zero bits. A few seconds of CPU at 18 bits.
    seed, bits = ch["pow"]["seed"], ch["pow"]["difficulty_bits"]
    whole, rest = divmod(bits, 8)
    n = 0
    while True:
        cand = str(n)
        d = hashlib.sha256((seed + cand).encode()).digest()
        if d[:whole] == b"\x00" * whole and (rest == 0 or d[whole] >> (8 - rest) == 0):
            break
        n += 1

    status, reg = http("POST", f"{api}/identity/register-pop", {
        "public_key": pub,
        "challenge": ch["challenge"],
        "signature": b64u(sk.sign(ch["challenge"].encode()).signature),
        "pow_nonce": cand,
        "display_name": "gate-probe",
        "platform": "gate",
    })
    if status not in (200, 201):
        print(f"register-pop failed: HTTP {status}\n{json.dumps(reg)[:300]}")
        return 1

    did = reg.get("did") or (reg.get("agent") or {}).get("did")
    print(f"DID         {did}")
    print(f"private key {bytes(sk).hex()}")
    print(f"public key  {pub}")
    print("\nKeep the private key. It is the only thing that proves the DID is yours,")
    print("and nobody can reissue it for you.\n")
    print("A fresh DID has no track record, so the probe below will show the same price")
    print("twice. To earn the lower one, bind a wallet on Base and issue a track record")
    print("over it: https://moltrust.ch/developers.html#track-record\n")
    print(f"  python3 {sys.argv[0]} --did {did} --key {bytes(sk).hex()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--did", help="Your did:moltrust identifier.")
    ap.add_argument("--key", help="Its Ed25519 private key, 64 hex characters.")
    ap.add_argument("--key-file", help="A file holding that key instead.")
    ap.add_argument("--register", action="store_true",
                    help="Mint a DID and print its key, then stop.")
    ap.add_argument("--api", default=DEFAULT_API, help="Registry base URL.")
    ap.add_argument("--guard", default=DEFAULT_GUARD, help="Gated host base URL.")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                    help="Path below --guard to ask for a price.")
    args = ap.parse_args()

    if args.register:
        return register(args.api)

    if not args.did:
        ap.error("--did is required (or use --register)")
    key_hex = args.key
    if args.key_file:
        with open(args.key_file) as f:
            key_hex = f.read().strip()
    if not key_hex:
        ap.error("--key or --key-file is required")

    from nacl.signing import SigningKey

    try:
        sk = SigningKey(bytes.fromhex(key_hex.removeprefix("0x")))
    except (ValueError, TypeError) as exc:
        print(f"That key is not 32 bytes of hex: {exc}")
        return 1

    # --- the plain ask ----------------------------------------------------
    url = args.guard.rstrip("/") + args.endpoint
    status_plain, body_plain = http("GET", url)
    plain = quoted_amount(body_plain)
    if plain is None:
        print(f"{url} did not answer with a price (HTTP {status_plain}).")
        print("Either it is free, or x402 is switched off there.")
        print(json.dumps(body_plain)[:300])
        return 1

    # --- the attestation --------------------------------------------------
    status, score = http("GET", f"{args.api}/skill/trust-score/{args.did}")
    att = (score or {}).get("gate_attestation")
    if not att:
        print(f"No gate_attestation for {args.did} (HTTP {status}).")
        print("The registry emits one only for a DID it holds a public key for.")
        return 1

    payload = json.loads(base64.urlsafe_b64decode(
        att.split(".")[1] + "=" * (-len(att.split(".")[1]) % 4)))

    # --- the ask, with proof ----------------------------------------------
    import time
    ts = str(int(time.time()))
    binding = "\n".join((BINDING_VERSION, "GET", args.endpoint, args.did, ts)).encode()
    status_proof, body_proof = http("GET", url, headers={
        "X-MolTrust-Attestation": att,
        "X-MolTrust-Timestamp": ts,
        "X-MolTrust-Proof": b64u(sk.sign(binding).signature),
    })
    with_proof = quoted_amount(body_proof)

    # --- what came back ----------------------------------------------------
    print(f"endpoint        {url}")
    print(f"did             {args.did}")
    print(f"trust_score     {payload.get('trust_score')}  (withheld: {payload.get('withheld')})")
    tr = payload.get("track_record")
    if tr:
        print(f"track_record    anchored {tr.get('anchor_tx')}")
    else:
        print("track_record    none — the attestation carries no track record")
    print()
    print(f"  without proof {plain}   ({plain / 1e6:.6f} USDC)")
    if with_proof is None:
        print(f"  with proof    no price quoted (HTTP {status_proof})")
        print(json.dumps(body_proof)[:300])
        return 1
    print(f"  with proof    {with_proof}   ({with_proof / 1e6:.6f} USDC)")
    print()

    if with_proof < plain:
        off = (plain - with_proof) / plain * 100
        print(f"The proof is worth {off:.0f} % here. Nothing has been paid.")
        return 0

    print("Same price either way.")
    if not tr:
        print("The attestation carries no track_record, so there is nothing for a gate")
        print("to accept in place of a score. If you have just issued one, the anchoring")
        print("batch may not have run yet — it runs every two hours, and the field stays")
        print("absent until the anchoring transaction exists.")
    elif payload.get("withheld"):
        print("The attestation carries a track record, so this host has probably not")
        print("switched allowTrackRecord on. That is its decision and the library")
        print("default; ask the operator.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
