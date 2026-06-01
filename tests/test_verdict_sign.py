"""Tests — AAE Verdict Signing (D3 Komponente 2, Schritt 2). Reine Unit-Tests (kein DB)."""
import copy
import uuid

from app.enforcement import verdict_sign
from app.enforcement.verdict_sign import sign_verdict, verify_verdict


def _record():
    return {
        "eval_id": "eval_" + uuid.uuid4().hex,
        "aae_ref": "sha256:" + "a" * 64,
        "agent_did": "did:moltrust:test_agent",
        "action_context": {"value": 1, "currency": "USD", "domain": "x.example"},
        "evaluations": [{"type": "max_transaction_value", "verdict": "ALLOW"}],
        "verdict": "ALLOW",
        "value_source": "self_asserted",
        "evaluator_version": "1.0",
        "timestamp": "2026-06-01T12:00:00Z",
        "nonce": uuid.uuid4().hex,
    }


def test_sign_verify_roundtrip():
    rec = _record()
    sig, kid = sign_verdict(rec)
    assert kid == "moltrust-registry-2026-v1"
    assert verify_verdict(rec, sig) is True


def test_tamper_action_context_value_breaks_signature():
    # DIE v3-Audit-Forge-Luecke: action_context.value an gespeichertem ALLOW aendern.
    rec = _record()
    sig, _ = sign_verdict(rec)
    tampered = copy.deepcopy(rec)
    tampered["action_context"]["value"] = 10000  # 1 -> 10000
    assert verify_verdict(tampered, sig) is False


def test_tamper_evaluations_breaks_signature():
    rec = _record()
    sig, _ = sign_verdict(rec)
    t = copy.deepcopy(rec)
    t["evaluations"][0]["verdict"] = "DENY"
    assert verify_verdict(t, sig) is False


def test_domain_separation():
    # Signatur OHNE Domain-Tag verifiziert NICHT gegen unsere verify (Cross-Protocol-Schutz).
    from app.registry_keys import get_private_key
    from app.signature import canonicalize, _b64url_encode
    rec = _record()
    subset = {k: rec[k] for k in verdict_sign._SIGNED_FIELDS}
    sig_no_tag = _b64url_encode(get_private_key().sign(canonicalize(subset)))  # ohne DOMAIN_TAG
    assert verify_verdict(rec, sig_no_tag) is False


def test_kid_present():
    _, kid = sign_verdict(_record())
    assert kid == "moltrust-registry-2026-v1"


def test_jcs_idempotence_key_order():
    # gleiche Werte, andere key-order im action_context -> identische Signatur (JCS).
    rec = _record()
    sig1, _ = sign_verdict(rec)
    rec2 = copy.deepcopy(rec)
    rec2["action_context"] = {"domain": "x.example", "currency": "USD", "value": 1}
    sig2, _ = sign_verdict(rec2)
    assert sig1 == sig2


def test_missing_signed_field_raises():
    rec = _record()
    del rec["nonce"]
    try:
        sign_verdict(rec)
        assert False, "expected ValueError"
    except ValueError:
        pass
