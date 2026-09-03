"""AAE Verdict Signing — D3 MANDATE-Enforcement, Komponente 2 (Schritt 2).

Ed25519-signiert das VOLLE kanonische Eval-Record (inkl. action_context + evaluations,
nicht nur Metadata) mit Domain-Separation. Damit bricht jede nachtraegliche Manipulation
an einem gespeicherten Verdict (z.B. action_context.value 1->10000) die Signatur —
schliesst die v3-Audit-Forge-Luecke.

  signing_input    = DOMAIN_TAG_BYTES || JCS(record-subset)   (Byte-Ebene, nach Serialisierung)
  verdict_signature = base64url( Ed25519_sign(signing_input) )
  verdict_kid       = REGISTRY_KID

Wiederverwendung der app/signature.py + registry_keys-Bausteine (kein Krypto-Duplikat).
"""
from __future__ import annotations

import base64
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.signature import canonicalize, _b64url_encode
from app.registry_keys import REGISTRY_KID, get_private_key, get_public_key_bytes

# Domain-Separation: fester Praefix + Trenner, auf Byte-Ebene vor JCS(record).
#
# ★ Dieser Tag bleibt bewusst auf `moltrust:`, waehrend der enforce-Kern und der
# Ratifikations-Kern auf `aae:` umgestellt sind (AAE -02). Drei Gruende, bevor jemand die
# Inkonsistenz "aufraeumt":
#   1. Er gehoert zum AAE-Evaluator, nicht zum enforce-Kern. Die beiden Maschinen teilen
#      keinen Code und keinen Pfad (siehe enforce_check.py, Abgrenzung im Modulkopf).
#   2. Kein Draft-Satz legt ihn fest. -02 normiert nur die enforce-/ratify-Tags.
#   3. Die damit erzeugten Signaturen liegen in der Datenbank (evaluator.py schreibt
#      verdict_signature in aae_evaluations). Ein Wechsel wuerde jede gespeicherte Signatur
#      ruecklaufend entwerten — anders als beim enforce-Kern, der nichts persistiert.
# Wenn er je wechseln soll, braucht das eine Migration, keinen Sed-Befehl.
DOMAIN_TAG_BYTES = b"moltrust:aae-verdict:v1\x00"

# Geschlossenes Feld-Set (Evaluator-Brief v4). NUR diese Felder werden signiert.
_SIGNED_FIELDS = (
    "eval_id", "aae_ref", "agent_did", "action_context", "evaluations",
    "verdict", "value_source", "evaluator_version", "timestamp", "nonce",
)


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _signing_input(record: dict) -> bytes:
    # Nur das geschlossene Feld-Set (kein Schema-Splicing offener Felder),
    # deterministisch via JCS; Domain-Tag auf Byte-Ebene vorangestellt.
    subset = {k: record[k] for k in _SIGNED_FIELDS}
    return DOMAIN_TAG_BYTES + canonicalize(subset)


def sign_verdict(record: dict) -> Tuple[str, str]:
    """Signiert den Eval-Record. Gibt (verdict_signature_b64url, verdict_kid) zurueck."""
    missing = [k for k in _SIGNED_FIELDS if k not in record]
    if missing:
        raise ValueError(f"record missing signed field(s): {', '.join(missing)}")
    sig = get_private_key().sign(_signing_input(record))
    return _b64url_encode(sig), REGISTRY_KID


def verify_verdict(record: dict, verdict_signature: str) -> bool:
    """Verifiziert die Signatur gegen den (rekonstruierten) Record. Fail-closed -> False."""
    try:
        sig = _b64url_decode(verdict_signature)
        pub = Ed25519PublicKey.from_public_bytes(get_public_key_bytes())
        pub.verify(sig, _signing_input(record))
        return True
    except Exception:  # fail-closed: jede Verify-/Decode-Stoerung -> False
        return False
