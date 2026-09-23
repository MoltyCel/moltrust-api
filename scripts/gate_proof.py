#!/usr/bin/env python3
"""One agent, from nothing to a discounted x402 request, written down at every step.

The MolTrust discount had never been granted. This walks the whole path an
outside agent would walk — register, bind a wallet, issue a track record, wait
for the anchor, present it at the gate — and records what came back at each
step, so the claim that the path works is a transcript rather than an assertion.

Only 0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38 signs here. That is the one
address the console may use unattended. 0x3802 is the productive signer and is
never touched.

    python3 gate_proof.py --probe-only     # everything except the payment
    python3 gate_proof.py                  # includes one 0.04 USDC payment
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

API = "https://api.moltrust.ch"
GUARD = "https://api.moltrust.ch/guard"
TEST_WALLET = "0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
CHAIN_ID = 8453
MAX_USDC = 0.05
UA = {"User-Agent": "moltrust-gate-proof/1.0", "Accept": "application/json"}

LOG: list[dict] = []


def step(name: str, **fields):
    entry = {"step": name, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}
    LOG.append(entry)
    shown = {k: v for k, v in fields.items() if k != "body"}
    print(f"[{entry['at']}] {name}: {json.dumps(shown, default=str)[:300]}")
    return entry


def http(method: str, url: str, body=None, headers=None, timeout=60):
    h = dict(UA)
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # nosec B310 - API/GUARD are module constants
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {"raw": raw[:400].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:600].decode("utf-8", "replace")}


def solve_pow(seed: str, bits: int) -> str:
    """Find a nonce whose sha256(seed + nonce) has `bits` leading zero bits."""
    need_bytes, rem = divmod(bits, 8)
    n = 0
    while True:
        cand = str(n)
        d = hashlib.sha256((seed + cand).encode()).digest()
        if d[:need_bytes] == b"\x00" * need_bytes and (rem == 0 or d[need_bytes] >> (8 - rem) == 0):
            return cand
        n += 1


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-only", action="store_true",
                    help="Stop before the payment. The 402 already shows the discounted price.")
    ap.add_argument("--out", default=os.path.expanduser("~/gate-proof.json"))
    ap.add_argument("--name", default="gate-proof",
                    help="display_name; the registry refuses the same name twice in 24h.")
    args = ap.parse_args()

    from nacl.signing import SigningKey
    from eth_account import Account
    from eth_account.messages import encode_defunct, encode_typed_data

    key = os.environ.get("BASE_ANCHOR_KEY", "").strip()
    if not key:
        print("BASE_ANCHOR_KEY fehlt."); return 1
    acct = Account.from_key(key if key.startswith("0x") else "0x" + key)
    if acct.address.lower() != TEST_WALLET.lower():
        print(f"BASE_ANCHOR_KEY ergibt {acct.address}, erwartet {TEST_WALLET}. Abbruch.")
        return 1
    admin_key = os.environ.get("ADMIN_KEY", "").strip()

    # --- 1. register a DID -------------------------------------------------
    st, ch = http("GET", f"{API}/identity/register-challenge")
    if st != 200:
        step("register-challenge", status=st, body=ch); return 1
    sk = SigningKey.generate()
    pub_hex = sk.verify_key.encode().hex()
    # Written before the DID exists, so a run that dies after registration can
    # still be finished instead of stranding a bound wallet on a key nobody has.
    #
    # Opened at 0600 rather than chmod-ed afterwards: between the two there is
    # a window in which the key sits at whatever the umask allowed, which here
    # was 0644 on a host with other accounts on it. Throwaway identity or not,
    # a private key is not world-readable.
    key_path = os.path.expanduser("~/gate-proof-key.txt")
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as kf:
        kf.write(bytes(sk).hex())
    nonce = solve_pow(ch["pow"]["seed"], ch["pow"]["difficulty_bits"])
    sig = b64u(sk.sign(ch["challenge"].encode()).signature)
    st, reg = http("POST", f"{API}/identity/register-pop", {
        "public_key": pub_hex, "challenge": ch["challenge"], "signature": sig,
        "pow_nonce": nonce, "display_name": args.name, "platform": "test",
        "description": "End-to-end proof of the track-record discount path.",
        "capabilities": ["gate-proof"], "framework": "none",
    })
    if st not in (200, 201):
        step("register-pop", status=st, body=reg); return 1
    did = reg.get("did") or reg.get("agent", {}).get("did")
    step("register-pop", status=st, did=did, public_key=pub_hex)

    # --- 2. bind an API key to it -----------------------------------------
    st, ch2 = http("GET", f"{API}/identity/register-challenge")
    nonce2 = solve_pow(ch2["pow"]["seed"], ch2["pow"]["difficulty_bits"])
    sig2 = b64u(sk.sign(ch2["challenge"].encode()).signature)
    st, sup = http("POST", f"{API}/auth/signup-did", {
        "public_key": pub_hex, "challenge": ch2["challenge"],
        "signature": sig2, "pow_nonce": nonce2,
    })
    api_key = sup.get("api_key") or sup.get("key")
    step("signup-did", status=st, api_key_present=bool(api_key))
    if not api_key:
        LOG.append({"step": "signup-did-body", "body": sup}); return 1
    AUTH = {"X-API-Key": api_key}

    # --- 3. bind the wallet ------------------------------------------------
    st, nz = http("GET", f"{API}/identity/nonce?did={did}&chain=base")
    if st != 200:
        step("identity/nonce", status=st, body=nz); return 1
    msg = (f"MolTrust DID Binding\nDID: {did}\nWallet: {TEST_WALLET}\n"
           f"Nonce: {nz['nonce']}\nChain: base")
    wsig = Account.sign_message(encode_defunct(text=msg), acct.key).signature.hex()
    if not wsig.startswith("0x"):
        wsig = "0x" + wsig
    st, bind = http("POST", f"{API}/identity/bind", {
        "did": did, "wallet_address": TEST_WALLET, "wallet_chain": "base",
        "wallet_signature": wsig, "nonce": nz["nonce"],
    }, AUTH)
    step("identity/bind", status=st, wallet=TEST_WALLET, message=msg, body=bind)
    if st != 200:
        return 1

    # --- 4. issue the track record ----------------------------------------
    st, tr = http("POST", f"{API}/credentials/track-record", {"did": did}, AUTH)
    step("credentials/track-record", status=st, measured=tr.get("measured"),
         anchor=tr.get("anchor"), credential_id=(tr.get("credential") or {}).get("id"))
    LOG.append({"step": "track-record-credential", "body": tr.get("credential")})
    if st != 200:
        LOG.append({"step": "track-record-error", "body": tr}); return 1

    # --- 5. anchor it ------------------------------------------------------
    if admin_key:
        st, anc = http("POST", f"{API}/credentials/admin/anchor", {},
                       {"x-admin-key": admin_key}, timeout=180)
        step("credentials/admin/anchor", status=st, body=anc)
    else:
        step("credentials/admin/anchor", status="skipped", reason="ADMIN_KEY not set")

    # --- 6. read the attestation back --------------------------------------
    st, score = http("GET", f"{API}/skill/trust-score/{did}")
    att = score.get("gate_attestation")
    payload = None
    if att:
        p = att.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    step("skill/trust-score", status=st, has_gate_attestation=bool(att),
         trust_score=score.get("trust_score"), withheld=score.get("withheld"),
         track_record=(payload or {}).get("track_record"))
    LOG.append({"step": "gate-attestation", "jws": att, "payload": payload})
    if not payload or not payload.get("track_record"):
        print("Kein track_record im Payload — Anker noch nicht da. Abbruch vor der Zahlung.")
        return 2

    # --- 7. present it at the gate ----------------------------------------
    path = f"/api/agent/score/{TEST_WALLET}"
    ts = str(int(time.time()))
    binding = "\n".join(("moltrust-gate/v1", "GET", path, did, ts)).encode()
    proof = b64u(sk.sign(binding).signature)
    gate_headers = {
        "X-MolTrust-Attestation": att,
        "X-MolTrust-Timestamp": ts,
        "X-MolTrust-Proof": proof,
    }
    st, chal = http("GET", GUARD + path, headers=gate_headers)
    offer = ((chal.get("x402") or {}).get("accepts") or [{}])[0]
    amount_units = int(offer.get("amount", 0) or 0)
    step("gate 402 challenge", status=st, binding_path=path,
         amount_units=amount_units, amount_usdc=amount_units / 1e6,
         payTo=offer.get("payTo"))
    LOG.append({"step": "gate-402-body", "body": chal})

    st_undisc, chal_u = http("GET", GUARD + path)
    off_u = ((chal_u.get("x402") or {}).get("accepts") or [{}])[0]
    undisc = int(off_u.get("amount", 0) or 0)
    step("gate 402 without headers", status=st_undisc, amount_units=undisc,
         amount_usdc=undisc / 1e6)

    if st != 402 or amount_units == 0:
        print("Keine verwertbare 402-Challenge mit Headern."); return 1
    if amount_units >= undisc:
        print(f"Kein Rabatt: {amount_units} mit Headern gegen {undisc} ohne. Abbruch vor der Zahlung.")
        return 3

    if args.probe_only:
        step("payment", status="skipped", reason="--probe-only")
        return 0

    amount_usdc = amount_units / 1e6
    if amount_usdc > MAX_USDC:
        print(f"{amount_usdc} USDC ueber der Obergrenze {MAX_USDC}."); return 1

    # --- 8. pay it ---------------------------------------------------------
    now = int(time.time())
    authorization = {
        "from": acct.address, "to": offer["payTo"], "value": str(amount_units),
        "validAfter": str(now - 60),
        "validBefore": str(now + int(offer.get("maxTimeoutSeconds") or 300) + 60),
        "nonce": "0x" + secrets.token_hex(32),
    }
    extra = offer.get("extra") or {}
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": extra.get("name", "USD Coin"), "version": extra.get("version", "2"),
                   "chainId": CHAIN_ID, "verifyingContract": offer["asset"]},
        "message": {"from": authorization["from"], "to": authorization["to"],
                    "value": int(authorization["value"]),
                    "validAfter": int(authorization["validAfter"]),
                    "validBefore": int(authorization["validBefore"]),
                    "nonce": bytes.fromhex(authorization["nonce"][2:])},
    }
    signed = Account.sign_message(encode_typed_data(full_message=typed), acct.key)
    sighex = signed.signature.hex()
    payload_x402 = {
        "x402Version": 2, "resource": (chal.get("x402") or {}).get("resource"),
        "accepted": offer,
        "payload": {"signature": sighex if sighex.startswith("0x") else "0x" + sighex,
                    "authorization": authorization},
    }
    pay_header = "x402 " + base64.b64encode(json.dumps(payload_x402).encode()).decode()

    # The proof is bound to a moment, so a fresh one for the paid request.
    ts2 = str(int(time.time()))
    proof2 = b64u(sk.sign("\n".join(("moltrust-gate/v1", "GET", path, did, ts2)).encode()).signature)
    paid_headers = dict(gate_headers)
    paid_headers.update({"X-MolTrust-Timestamp": ts2, "X-MolTrust-Proof": proof2,
                         "PAYMENT-SIGNATURE": pay_header})
    st, resp = http("GET", GUARD + path, headers=paid_headers, timeout=180)
    step("gate paid request", status=st, amount_usdc=amount_usdc,
         nonce=authorization["nonce"])
    LOG.append({"step": "paid-response", "body": resp})

    if st == 200:
        try:
            import psycopg2
            dsn = os.environ.get("DATABASE_URL", "postgresql://moltstack@localhost/moltstack")
            with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pool_spend (pool, usdc, tx_hash, purpose, spent_at, recorded_at) "
                    "VALUES (%s,%s,%s,%s,now(),now()) ON CONFLICT DO NOTHING",
                    ("test-wallet", amount_usdc, f"nonce:{authorization['nonce']}", "gate-proof"),
                )
            step("pool_spend", status="booked", usdc=amount_usdc, purpose="gate-proof")
        except Exception as exc:
            step("pool_spend", status="FAILED", error=str(exc), usdc=amount_usdc)

    # --- 9. the counter ----------------------------------------------------
    st, stats = http("GET", f"{GUARD}/moltrust/gate-stats")
    step("gate-stats", status=st, body=stats)

    with open(args.out, "w") as f:
        json.dump({"did": did, "wallet": TEST_WALLET, "log": LOG}, f, indent=2, default=str)
    print(f"\nProtokoll: {args.out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        try:
            with open(os.path.expanduser("~/gate-proof.json"), "w") as f:
                json.dump({"log": LOG}, f, indent=2, default=str)
        except Exception:
            pass
