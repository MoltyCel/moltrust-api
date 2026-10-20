"""Sign a request as an outside caller would (RFC 9421 profile of ops V6).

Built from the RFC text, not from app/enforcement/request_signature.py, so a
test that passes shows the server accepts what an independent client sends.
"""
import base64
import hashlib
import json
import time
import uuid

COMPONENTS = ("@method", "@path", "x-moltrust-did", "content-digest")


def signed_headers(priv, did, method, path, body: bytes, *, kid=None, created=None,
                   nonce=None, components=COMPONENTS, alg="ed25519"):
    kid = kid or f"{did}#key-1"
    created = int(time.time()) if created is None else created
    nonce = nonce or uuid.uuid4().hex
    digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
    values = {"@method": method.upper(), "@path": path, "x-moltrust-did": did,
              "content-digest": digest}
    params = (
        "(" + " ".join(f'"{c}"' for c in components) + ")"
        + f';created={created};nonce="{nonce}";keyid="{kid}";alg="{alg}"'
    )
    base = "\n".join([f'"{c}": {values[c]}' for c in components]
                     + [f'"@signature-params": {params}'])
    sig = base64.b64encode(priv.sign(base.encode())).decode()
    return {
        "X-MolTrust-DID": did,
        "Content-Digest": digest,
        "Signature-Input": f"sig1={params}",
        "Signature": f"sig1=:{sig}:",
        "Content-Type": "application/json",
    }


def signed_post(priv, did, path, payload, **kw):
    """(body bytes, headers) for a signed JSON POST to `path`."""
    body = json.dumps(payload).encode()
    return body, signed_headers(priv, did, "POST", path, body, **kw)
