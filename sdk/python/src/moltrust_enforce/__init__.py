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
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:  # pragma: no cover - nur fuer Typpruefer und IDEs
    from .client import EnforceClient, Ratification, Verdict, VerifyResult

__version__ = "0.3.0"

# Der HTTP-Client wird erst beim Zugriff geladen. Ohne das zoege ein
# `import moltrust_enforce.cli` httpx, socket und ssl in einen Prozess, der nichts davon
# benutzt — und die Zusage, dass der Verifizierer netzfrei laeuft, waere nur noch eine
# Aussage ueber das Verhalten statt ueber den geladenen Code.
_CLIENT_EXPORTS = frozenset({"EnforceClient", "Verdict", "VerifyResult", "Ratification"})


def __getattr__(name: str):
    if name in _CLIENT_EXPORTS:
        try:
            from . import client
        except ModuleNotFoundError as exc:
            if exc.name not in ("httpx", "httpcore", "h11", "anyio", "certifi"):
                raise
            # Ohne diesen Zweig faellt hier ein nacktes `No module named 'httpx'` heraus,
            # und der Leser sucht den Fehler bei sich. Ab 0.3.0 steht der Client im Extra.
            #
            # Genannt wird der ganze Satz, weil alle vier Namen in `client.py` stehen und
            # ohne httpx scheitern — gemessen, nicht angenommen (`test_aer_verify.py`).
            # Wer nach `Verdict` greift, soll nicht einzeln herausfinden muessen, dass
            # `VerifyResult` gleich danach genauso bricht.
            others = ", ".join(sorted(_CLIENT_EXPORTS - {name}))
            raise ImportError(
                f"{name} needs the HTTP client, which is not installed. It moved into an "
                "extra in 0.3.0: pip install 'moltrust-enforce[client]'. The same holds "
                f"for {others} — all four live in the client module. Recomputing and "
                "verifying work without them."
            ) from exc
        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)

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
