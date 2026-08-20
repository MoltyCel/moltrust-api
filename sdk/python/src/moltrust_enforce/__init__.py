"""moltrust-enforce — Referenz-Client fuer MolTrust `POST /enforce/check`.

Zwei Nutzungsmuster, beide ausdruecklich:

    from moltrust_enforce import EnforceClient

    client = EnforceClient("https://api.moltrust.ch", api_key=KEY)

    v = client.check(mandate, transaction)      # dem Server glauben
    if not v.permitted:
        return

    r = client.verify(v, mandate, transaction)  # selbst nachrechnen
    if not r.ok:
        raise RuntimeError(r.mismatches)

Der lokale Nachrechen-Kern (`enforce_check`, `action_digest`, `core_digest`, `recompute`) ist
eine unveraenderte Kopie von `app/enforcement/enforce_check.py` aus dem Server-Repo; einzig
die Import-Zeile fuer die JCS-Kanonisierung zeigt hier direkt auf `jcs` statt auf
`app.signature`. `tests/test_core_parity.py` haelt das nach.

Das SDK prueft Mandate, es stellt keine aus. Signieren und Ausgeben von Mandaten ist
ausdruecklich nicht Teil davon.
"""
from ._core import (
    DENY,
    ENFORCE_VERSION,
    PENDING,
    PERMIT,
    action_digest,
    core_digest,
    enforce_check,
    recompute,
)
from ._ratify_core import (
    APPROVED,
    DISAPPROVED,
    RATIFIED,
    REJECTED,
    RatifyError,
    mandate_authorities,
    ratification_statement,
    ratify,
    statement_bytes,
)
from .client import EnforceClient, Ratification, Verdict, VerifyResult
from .errors import EnforceError, EnforceProtocolError, EnforceTransportError

__version__ = "0.2.0"

__all__ = [
    "EnforceClient",
    "Verdict",
    "VerifyResult",
    "Ratification",
    "ratify",
    "mandate_authorities",
    "ratification_statement",
    "statement_bytes",
    "RatifyError",
    "APPROVED",
    "DISAPPROVED",
    "RATIFIED",
    "REJECTED",
    "EnforceError",
    "EnforceTransportError",
    "EnforceProtocolError",
    "enforce_check",
    "recompute",
    "action_digest",
    "core_digest",
    "PERMIT",
    "DENY",
    "PENDING",
    "ENFORCE_VERSION",
    "__version__",
]
