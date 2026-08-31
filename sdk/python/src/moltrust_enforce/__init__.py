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

AER — Attested-Evidence Replay
------------------------------
Ab 0.3.0 kommt die Evidenz-Schicht dazu: `f_ext` entscheidet zusaetzlich ueber lebende
Vorbedingungen (Widerruf, Sanktions-/Jurisdiktionsstatus, Umrechnungskurs), die als
signierte, zeitgebundene Evidenz im Buendel liegen, und `verify_record` rechnet dasselbe
Urteil offline nach — ohne Neuabruf der Quellen und ohne den MolTrust-Server.

    from moltrust_enforce import build_bundle, f_ext, verify_record

    bundle = build_bundle(items, mandate, transaction, "2026-08-31T10:00:00Z")
    record = f_ext(mandate, transaction, bundle)
    result = verify_record(record, bundle, mandate, transaction, trust_list)

Dieselbe Trennung wie oben gilt auch hier: das SDK prueft Evidenz, es stellt keine aus.
Signierende Quell-Adapter (ESA) sind nicht Teil des Pakets.
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
from ._ext_core import (
    ext_core_digest,
    f_ext,
    is_evidence_constraint,
    recompute_ext,
)
from .client import EnforceClient, Ratification, Verdict, VerifyResult
from .errors import EnforceError, EnforceProtocolError, EnforceTransportError
from .evidence import (
    AER_VERSION,
    PAYLOAD_TYPE,
    build_bundle,
    bundle_problem,
    compute_bundle_commit,
    envelope_statement,
    evidence_payload_bytes,
    evidence_values,
    item_digest,
    make_envelope,
    make_statement,
    pae,
    parse_timestamp,
    query_key,
    statement_problem,
)
from .verify import AerVerifyResult, trust_list_problem, verify_record

__version__ = "0.3.0"

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
    # AER — Evidenz-Schicht
    "AER_VERSION",
    "PAYLOAD_TYPE",
    "make_statement",
    "make_envelope",
    # `evidence_payload_bytes` heisst nicht `statement_bytes`: der Ratifikations-Kern
    # exportiert unter dem Namen bereits etwas anderes, und zwei Bedeutungen unter einem
    # Namen im selben Paket sind eine Falle.
    "evidence_payload_bytes",
    "statement_problem",
    "envelope_statement",
    "pae",
    "query_key",
    "item_digest",
    "build_bundle",
    "bundle_problem",
    "compute_bundle_commit",
    "evidence_values",
    "parse_timestamp",
    "f_ext",
    "ext_core_digest",
    "recompute_ext",
    "is_evidence_constraint",
    "verify_record",
    "trust_list_problem",
    "AerVerifyResult",
    "__version__",
]
