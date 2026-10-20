#!/usr/bin/env python3
"""Live guard for ops V6: a DID header alone is refused, a signed request goes through.

Two directions against production, both on /vc/aae/evaluate and both for the
same DID, our own test DID did:moltrust:a2f9415d27094890 (key registered for V6
criterion 13 on 18.10.2026). Never did:moltrust:bcd56532a992f0ac: V6 criterion
(6) counts that no new row appears for it after the deploy, and a guard writing
into its record would make that unmeasurable after the first run.

  1. only X-MolTrust-DID, no key and no signature  -> must be 401
  2. the same request, signed with the DID's key as an RFC 9421 message
     signature                                       -> must be 2xx and ALLOW

Direction 1 alone would call a deploy healthy that refuses everything. Direction
2 alone would call a deploy healthy that still takes the bare header. Only both
together say the door is shut and the key opens it.

Direction 2 is a real evaluation: the server writes one aae_evaluations row per
run. It names the stored envelope moltrust:aae:v6-guard-probe, whose single
constraint is an allowlist holding the API host, and it sends exactly that host,
so the verdict is ALLOW and no run writes a violation row. A verdict other than
ALLOW is therefore red here: it means the envelope is gone, expired or no longer
matches, and every further run would log a violation against the probe DID. The
envelope's validity ends on 2030-01-01; after that the guard turns red on
"expired (not_after)" and a new envelope has to be stored.

The signature is built here from the RFC text, not imported from
app/enforcement/request_signature.py, so the guard tests what an outside client
sends.

Prints one line per direction and a closing line "v6-guard=ok" or
"v6-guard=FAIL <which direction> ...". Exit 0 ok, 1 a direction failed,
2 the guard could not run (no key file).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

API = "https://api.moltrust.ch"
PATH = "/vc/aae/evaluate"
PROBE_DID = "did:moltrust:a2f9415d27094890"
KEY_FILE = os.path.expanduser("~/.moltrust_testdids/a2f9415d27094890.ed25519")
COMPONENTS = ("@method", "@path", "x-moltrust-did", "content-digest")
# The stored envelope this probe evaluates against, and the host its allowlist
# holds. Sending that host is what turns the evaluation into an ALLOW.
AAE_REF = "sha256:d2e7b99d884a741ae04d908a3c046429d3da8c7d2a0596d542e4dd7940c291e2"
AAE_ID = "moltrust:aae:v6-guard-probe"
PROBE_DOMAIN = "api.moltrust.ch"


def body_for(did: str, nonce: str, *, domain: str = PROBE_DOMAIN) -> bytes:
    return json.dumps({"aae_ref": AAE_REF,
                       "action_context": {"agent_did": did, "nonce": nonce,
                                          "vc_id": AAE_ID, "domain": domain}}).encode()


def signed_headers(priv, did: str, path: str, body: bytes, *, created=None, nonce=None) -> dict:
    """RFC 9421 section 2.5 signature base over the four components of the V6 profile."""
    created = int(time.time()) if created is None else created
    nonce = nonce or "v6g-" + uuid.uuid4().hex
    digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
    values = {"@method": "POST", "@path": path, "x-moltrust-did": did, "content-digest": digest}
    params = ("(" + " ".join(f'"{c}"' for c in COMPONENTS) + ")"
              + f';created={created};nonce="{nonce}";keyid="{did}#key-1";alg="ed25519"')
    base = "\n".join([f'"{c}": {values[c]}' for c in COMPONENTS]
                     + [f'"@signature-params": {params}'])
    sig = base64.b64encode(priv.sign(base.encode())).decode()
    return {"X-MolTrust-DID": did, "Content-Digest": digest,
            "Signature-Input": f"sig1={params}", "Signature": f"sig1=:{sig}:"}


def verdict_of(raw: bytes) -> str:
    """The verdict an answer carries, "" when it carries none."""
    try:
        v = json.loads(raw).get("verdict")
    except Exception:
        return ""
    return v if isinstance(v, str) else ""


def post(url: str, body: bytes, headers: dict) -> tuple[int, str]:
    """(status, verdict); (0, "") when no HTTP answer came back."""
    if not url.startswith("https://"):
        raise ValueError("the guard talks https only")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "moltrust-v6-guard/1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 — scheme checked above  # nosec B310 - https only, checked above; host is the --base API
            return r.status, verdict_of(r.read())
    except urllib.error.HTTPError as e:
        return e.code, verdict_of(e.read())
    except (urllib.error.URLError, OSError):
        return 0, ""


def judge(header_only: int, signed: int, verdict: str) -> tuple[bool, list[str]]:
    """(ok, lines). Each direction is judged on its own, so a red names which one."""
    r1 = header_only == 401
    status_ok = 200 <= signed < 300
    r2 = status_ok and verdict == "ALLOW"
    lines = [
        f"richtung1 header-only status={header_only} erwartet=401 {'ok' if r1 else 'FAIL'}",
        f"richtung2 signiert status={signed} verdikt={verdict or 'keins'} "
        f"erwartet=2xx+ALLOW {'ok' if r2 else 'FAIL'}",
    ]
    if r1 and r2:
        lines.append("v6-guard=ok")
    else:
        failed = []
        if not r1:
            failed.append(f"richtung1 (nur X-MolTrust-DID ergab {header_only or 'keine Antwort'}, "
                          "erwartet 401: der Header allein wird angenommen)")
        if not status_ok:
            failed.append(f"richtung2 (signierte Anfrage ergab {signed or 'keine Antwort'}, "
                          "erwartet 2xx: der Signaturweg ist zu)")
        elif verdict != "ALLOW":
            failed.append(f"richtung2 (Verdikt {verdict or 'keins'}, erwartet ALLOW: das "
                          f"gespeicherte Envelope {AAE_ID} greift nicht mehr, und jeder "
                          "weitere Lauf schreibt einen Verstoss auf die Probe-DID)")
        lines.append("v6-guard=FAIL " + "; ".join(failed))
    return r1 and r2, lines


def run(base: str, priv, *, send=post, domain: str = PROBE_DOMAIN) -> tuple[bool, list[str]]:
    url = base.rstrip("/") + PATH
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    b1 = body_for(PROBE_DID, f"v6-guard-r1-{stamp}-{uuid.uuid4().hex[:8]}", domain=domain)
    s1, _ = send(url, b1, {"X-MolTrust-DID": PROBE_DID})
    b2 = body_for(PROBE_DID, f"v6-guard-r2-{stamp}-{uuid.uuid4().hex[:8]}", domain=domain)
    s2, v2 = send(url, b2, signed_headers(priv, PROBE_DID, PATH, b2))
    return judge(s1, s2, v2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=API)
    ap.add_argument("--key-file", default=KEY_FILE)
    ap.add_argument("--fault-domain", metavar="HOST",
                    help=f"Fehlerprobe: HOST statt {PROBE_DOMAIN} senden. Der Lauf muss rot "
                         "werden, und er schreibt einen Verstoss auf die Probe-DID.")
    a = ap.parse_args(argv)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        with open(a.key_file) as f:
            priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(f.read().strip()))
    except (OSError, ValueError) as e:
        print(f"v6-guard=ERROR Schluessel nicht lesbar ({type(e).__name__})")
        return 2
    ok, lines = run(a.base, priv, domain=a.fault_domain or PROBE_DOMAIN)
    if a.fault_domain:
        print(f"fehlerprobe domain={a.fault_domain} statt {PROBE_DOMAIN}")
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
