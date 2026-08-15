"""
MolTrust — IPFS VC Publisher
Publishes Verifiable Credentials to IPFS via Pinata.
Graceful degradation: failures log warnings but never block VC issuance.
"""
import json
import logging
import urllib.request
from pathlib import Path

log = logging.getLogger("moltrust.ipfs")

PINATA_API_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
PINATA_GATEWAY = "https://gateway.pinata.cloud/ipfs"


def _load_pinata_jwt():
    """Load Pinata JWT from secrets file."""
    secrets_file = Path.home() / ".moltrust_secrets"
    if secrets_file.exists():
        for line in secrets_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("PINATA_JWT="):
                return line.split("=", 1)[1].strip()
    return ""


def publish_to_ipfs(vc_json: dict, name: str = None) -> str | None:
    """
    Publish a VC JSON to IPFS via Pinata.
    Returns CID (e.g. 'QmXyz...') on success, None on failure.
    Never raises — all errors are logged as warnings.
    """
    jwt = _load_pinata_jwt()
    if not jwt:
        log.warning("IPFS publish skipped: PINATA_JWT not configured")
        return None

    try:
        payload = json.dumps({
            "pinataContent": vc_json,
            "pinataMetadata": {
                "name": name or "moltrust-vc-%s" % vc_json.get("id", "unknown"),
            }
        }).encode()

        if not PINATA_API_URL.startswith(("http://", "https://")):
            raise ValueError("PINATA_API_URL must use http(s)://")
        req = urllib.request.Request(
            PINATA_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % jwt,
            }
        )

        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — scheme validated above  # nosec B310 - PINATA_API_URL is a module constant; scheme checked above
            result = json.loads(r.read())
            cid = result.get("IpfsHash")
            if cid:
                log.info("VC published to IPFS: %s", cid)
                return cid
            else:
                log.warning("IPFS publish: no CID in response: %s", result)
                return None

    except Exception as e:
        log.warning("IPFS publish failed (non-blocking): %s", e)
        return None


def get_ipfs_url(cid: str) -> str:
    """Return the Pinata gateway URL for a CID."""
    return "%s/%s" % (PINATA_GATEWAY, cid)
