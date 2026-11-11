"""Register a MolTrust DID for a client that has none, on its first call.

`EnforceClient` needs an identity to send: an API key or a DID. A client built
with neither gets one here, once per installation and API URL:

1. An Ed25519 key pair is generated on this machine.
2. ``GET /identity/register-challenge`` fetches a challenge and a small
   proof-of-work seed.
3. ``POST /identity/register-pop`` sends the public key, a signature over the
   challenge, the proof-of-work nonce, a display name derived from the public
   key, the platform value ``usage-enforce`` and the source ``moltrust-enforce``.
   The private key never leaves the machine.
4. The returned DID, the public key and the private key are written to
   ``~/.moltrust/enforce-identity.json`` with file mode 0600.

Later clients find the file and send its DID. A failure is logged as one
warning and the call goes on without a DID; it is never raised.

Opt out with ``EnforceClient(..., auto_register=False)`` or the environment
variable ``MOLTRUST_NO_AUTO_REGISTER=1``. Nothing is registered when the ``CI``
environment variable is set.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("moltrust_enforce.autoregister")

ENV_OPT_OUT = "MOLTRUST_NO_AUTO_REGISTER"
ENV_IDENTITY_FILE = "MOLTRUST_IDENTITY_FILE"
ENV_PLATFORM = "MOLTRUST_REGISTER_PLATFORM"

DEFAULT_PLATFORM = "usage-enforce"
SOURCE = "moltrust-enforce"
DEFAULT_FILE = Path.home() / ".moltrust" / "enforce-identity.json"
TIMEOUT = 10.0
# A lock younger than this belongs to a process that is still registering; an
# older one is left over from a crashed run and is removed.
LOCK_STALE_SECONDS = 120
FILE_FORMAT = 1

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Identity:
    did: str
    public_key: str
    api_url: str


def disabled_reason(flag: Optional[bool] = None) -> Optional[str]:
    """Why nothing is registered, or None when registration may run."""
    if flag is False:
        return "auto_register=False"
    if os.environ.get(ENV_OPT_OUT, "").strip().lower() in _TRUE:
        return ENV_OPT_OUT
    if os.environ.get("CI", "").strip():
        return "CI"
    return None


def identity_file() -> Path:
    raw = os.environ.get(ENV_IDENTITY_FILE, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_FILE


def platform() -> str:
    return os.environ.get(ENV_PLATFORM, "").strip() or DEFAULT_PLATFORM


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(api_url: str, path: Optional[Path] = None) -> Optional[Identity]:
    """The identity stored for `api_url`, if this installation has one."""
    stored = _read(path or identity_file()).get("identities")
    entry = stored.get(api_url) if isinstance(stored, dict) else None
    if not isinstance(entry, dict) or not entry.get("did"):
        return None
    return Identity(did=entry["did"], public_key=entry.get("public_key", ""), api_url=api_url)


def _write(path: Path, api_url: str, entry: dict) -> None:
    stored = _read(path).get("identities")
    identities: dict = stored if isinstance(stored, dict) else {}
    identities[api_url] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"format": FILE_FORMAT, "identities": identities}, fh, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _acquire_lock(path: Path) -> Optional[Path]:
    lock = path.with_name(path.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink()
    except FileNotFoundError:
        pass
    try:
        os.close(os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return None
    return lock


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _leading_zero_bits(digest: bytes) -> int:
    n = 0
    for byte in digest:
        if byte == 0:
            n += 8
            continue
        return n + 8 - byte.bit_length()
    return n


def solve_pow(seed: str, bits: int) -> str:
    """Smallest hex counter whose sha256(seed + nonce) has `bits` leading zero bits."""
    i = 0
    while True:
        nonce = format(i, "x")
        if _leading_zero_bits(hashlib.sha256(f"{seed}{nonce}".encode("ascii")).digest()) >= bits:
            return nonce
        i += 1


def _register(http, version: str) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    priv = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption()).hex()

    ch = http.get("/identity/register-challenge")
    if ch.status_code >= 400:
        raise RuntimeError(f"register-challenge answered HTTP {ch.status_code}")
    doc = ch.json()
    challenge = doc["challenge"]
    pow_doc = doc.get("pow") or {}
    nonce = solve_pow(pow_doc.get("seed", challenge.split(".", 1)[0]),
                      int(pow_doc.get("difficulty_bits", 0)))
    body = {
        "public_key": pub,
        "challenge": challenge,
        "signature": _b64url(key.sign(challenge.encode("ascii"))),
        "pow_nonce": nonce,
        # The registry refuses a second agent with the same name and platform
        # within 24 hours, so the name carries part of the key.
        "display_name": f"moltrust-enforce-{pub[:12]}",
        "platform": platform(),
        "source": SOURCE,
    }
    resp = http.post("/identity/register-pop", json=body,
                     headers={"User-Agent": f"moltrust-enforce/{version}"})
    if resp.status_code >= 400:
        raise RuntimeError(f"register-pop answered HTTP {resp.status_code}")
    did = resp.json().get("did")
    if not did:
        raise RuntimeError("register-pop answered without a DID")
    return {
        "did": did,
        "public_key": pub,
        "private_key": priv,
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "package": f"moltrust-enforce/{version}",
        "platform": body["platform"],
    }


def ensure(api_url: str, version: str, *, flag: Optional[bool] = None,
           transport=None) -> Optional[Identity]:
    """Return this installation's DID for `api_url`, registering one if needed.

    Never raises. Returns None when registration is disabled, already running in
    another process, or failed.
    """
    try:
        if disabled_reason(flag):
            return None
        path = identity_file()
        found = load(api_url, path)
        if found:
            return found
        lock = _acquire_lock(path)
        if lock is None:
            return None
        try:
            found = load(api_url, path)
            if found:
                return found
            import httpx

            with httpx.Client(base_url=api_url, timeout=TIMEOUT, transport=transport) as http:
                entry = _register(http, version)
            _write(path, api_url, entry)
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
        log.warning("MolTrust: registered %s for this installation (stored in %s). "
                    "Opt out: %s=1", entry["did"], path, ENV_OPT_OUT)
        return Identity(did=entry["did"], public_key=entry["public_key"], api_url=api_url)
    except Exception as exc:  # registration must never break the caller's call
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        log.warning("MolTrust: automatic DID registration skipped (%s)", reason)
        return None
