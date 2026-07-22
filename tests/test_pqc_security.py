"""Security hardening tests for the PQC dual-signature implementation.

These tests cover:
  - The downgrade/stripping attack that the 3-model review flagged
  - AND-logic verification (one bad leg fails the whole credential)
  - Skeleton binding (proof structure is bound into the signature)
  - Input validation (no 500 errors on malformed input)
  - Type-confusion prevention (exact-match proof type dispatch)
  - Legacy backward compatibility (sort_keys credentials still verify)

The downgrade attack is the critical one: a PQC-enabled issuer produces a
dual-signed credential, an attacker strips the Dilithium leg, and the
remaining Ed25519 proof must NOT verify. The skeleton-binding construction
ensures this: the Ed25519 signature was made over a payload that includes
the Dilithium proof in the skeleton, so stripping it changes the payload
and the Ed25519 signature becomes invalid.
"""
import copy
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(autouse=True)
def stub_oqs():
    """Stub oqs (not installed in test env) and reset dilithium stubs."""
    import importlib
    if "oqs" not in sys.modules:
        sys.modules["oqs"] = types.ModuleType("oqs")

    # Clean any leftover Dilithium env vars before reloading
    for k in ("DILITHIUM_PRIVATE_KEY_HEX", "DILITHIUM_PUBLIC_KEY_HEX",
              "DILITHIUM_PRIVATE_KEY_ENCRYPTED", "DILITHIUM_ENV_DUMMY"):
        os.environ.pop(k, None)
    # Unset production gate
    os.environ.pop("MOLTRUST_ENV", None)

    # Reload crypto modules so the stub is picked up
    import app.crypto.dilithium as dilithium
    importlib.reload(dilithium)
    import app.crypto.hybrid as hybrid_mod
    importlib.reload(hybrid_mod)
    import app.crypto.proof_utils as proof_utils_mod
    importlib.reload(proof_utils_mod)
    # Reload credentials too so it picks up the reloaded hybrid
    import app.credentials as credentials_mod
    importlib.reload(credentials_mod)

    yield


@pytest.fixture
def ed25519_key():
    """A fresh Ed25519 signing key for each test."""
    from nacl.signing import SigningKey
    return SigningKey.generate()


@pytest.fixture
def sample_credential():
    """A minimal valid VC body for testing."""
    return {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "issuer": "did:web:api.moltrust.ch",
        "validFrom": "2026-07-03T00:00:00Z",
        "validUntil": "2027-07-03T00:00:00Z",
        "credentialSubject": {"id": "did:web:test-agent"},
    }


def _setup_pqc(available=True, dil_sig=b"FAKE_DIL_SIG_3309_bytes__" + b"\x00" * 3280,
               dil_pk="deadbeef", dil_verify=True):
    """Configure dilithium stubs. dil_sig must be > 0 bytes."""
    from app.crypto import dilithium
    dilithium.is_available = lambda: available
    dilithium.public_key_configured = lambda: bool(dil_pk) if available else False
    dilithium.sign = lambda p: dil_sig if available else None
    dilithium.get_public_key_hex = lambda: dil_pk if available else None
    dilithium.verify = lambda p, s, pk: dil_verify


# ===========================================================================
# Downgrade attack (the critical review finding)
# ===========================================================================

class TestDowngradeAttack:
    """The 3-model review's blocker #1: stripping a proof leg must fail."""

    def test_dual_signed_strip_dilithium_rejected(self, ed25519_key, sample_credential):
        """PQC-enabled issuer -> dual-signed -> strip Dilithium -> rejected."""
        from app.crypto import hybrid
        _setup_pqc(available=True)

        dual = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        assert isinstance(dual["proof"], list)
        assert len(dual["proof"]) == 2

        # Attacker strips the Dilithium leg -> proof becomes single Ed25519
        attack = copy.deepcopy(dual)
        attack["proof"] = dual["proof"][0]

        result = hybrid.verify_proof(attack, ed25519_key.verify_key)
        assert result["valid"] is False, (
            "DOWNGRADE ATTACK: stripping Dilithium leg must invalidate "
            "the credential (PQC policy + skeleton binding)"
        )

    def test_strip_dilithium_breaks_ed25519_signature(self, ed25519_key, sample_credential):
        """Skeleton binding: stripping Dilithium changes the skeleton, so the
        Ed25519 signature itself breaks — independent of the PQC policy."""
        from app.crypto import hybrid
        _setup_pqc(available=True)

        dual = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        attack = copy.deepcopy(dual)
        attack["proof"] = dual["proof"][0]  # strip Dilithium

        # Verify with PQC OFF so the policy check doesn't short-circuit;
        # the Ed25519 signature must still fail because the skeleton changed.
        _setup_pqc(available=False)
        result = hybrid.verify_proof(attack, ed25519_key.verify_key)
        assert result["valid"] is False, (
            "Skeleton binding: stripping Dilithium must break the Ed25519 "
            "signature even without PQC policy enforcement"
        )
        # The Ed25519 check itself must report invalid (not just "no proof")
        ed_check = [c for c in result.get("checks", []) if c.get("type") == "Ed25519"]
        assert ed_check, "Ed25519 check must be present"
        assert ed_check[0]["valid"] is False, (
            "Ed25519 signature must be invalid after skeleton change"
        )

    def test_swap_proof_order_rejected(self, ed25519_key, sample_credential):
        """Swapping [ed, dil] -> [dil, ed] changes skeleton, signatures fail."""
        from app.crypto import hybrid
        _setup_pqc(available=True, dil_verify=True)

        dual = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        swapped = copy.deepcopy(dual)
        swapped["proof"] = [dual["proof"][1], dual["proof"][0]]

        result = hybrid.verify_proof(swapped, ed25519_key.verify_key)
        assert result["valid"] is False, "Reordering proofs must break signatures"

    def test_add_fake_dilithium_to_ed25519_only(self, ed25519_key, sample_credential):
        """Adding a fake Dilithium proof to an Ed25519-only credential fails."""
        from app.crypto import hybrid
        _setup_pqc(available=True, dil_verify=True)

        # Non-PQC issuer: single Ed25519 proof
        _setup_pqc(available=False)
        from app.crypto import dilithium
        dilithium.is_available = lambda: False
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        assert isinstance(ed_only["proof"], dict)

        # Attacker adds a fake Dilithium proof
        attack = copy.deepcopy(ed_only)
        attack["proof"] = [
            ed_only["proof"],
            {
                "type": "DilithiumSignature2026",
                "created": ed_only["proof"]["created"],
                "verificationMethod": "did:web:api.moltrust.ch#key-dilithium",
                "proofPurpose": "assertionMethod",
                "canonicalizationAlgorithm": "JCS",
                "proofValue": "bb" * 2420,
            },
        ]
        result = hybrid.verify_proof(attack, ed25519_key.verify_key)
        assert result["valid"] is False, (
            "Adding a fake leg to an Ed25519-only credential must break "
            "the original Ed25519 signature"
        )


# ===========================================================================
# AND-logic (one bad leg fails the whole credential)
# ===========================================================================

class TestAndLogic:
    """A broken leg must fail the whole credential."""

    def test_bad_dilithium_leg_fails_whole(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=True, dil_verify=True)

        dual = hybrid.dual_sign(dict(sample_credential), ed25519_key)

        # Now make Dilithium verify return False (simulated bad sig)
        _setup_pqc(available=True, dil_verify=False)

        result = hybrid.verify_proof(dual, ed25519_key.verify_key)
        assert result["valid"] is False, "Broken Dilithium leg must fail whole cred"
        # Ed25519 should still pass; only Dilithium should fail
        checks = {c["type"]: c["valid"] for c in result.get("checks", [])}
        assert checks.get("Ed25519") is True
        assert checks.get("Dilithium") is False


# ===========================================================================
# Legitimate credentials (baseline)
# ===========================================================================

class TestLegitimateCredentials:
    """Happy-path verification."""

    def test_ed25519_only_legit(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        result = hybrid.verify_proof(ed_only, ed25519_key.verify_key)
        assert result["valid"] is True
        assert result["checks"][0]["type"] == "Ed25519"
        assert result["checks"][0]["valid"] is True

    def test_dual_signed_legit(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=True, dil_verify=True)
        dual = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        result = hybrid.verify_proof(dual, ed25519_key.verify_key)
        assert result["valid"] is True
        types = {c["type"] for c in result["checks"]}
        assert "Ed25519" in types
        assert "Dilithium" in types

    def test_body_tamper_rejected(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        tampered = copy.deepcopy(ed_only)
        tampered["credentialSubject"]["id"] = "did:web:attacker"
        result = hybrid.verify_proof(tampered, ed25519_key.verify_key)
        assert result["valid"] is False

    def test_extra_field_in_proof_rejected(self, ed25519_key, sample_credential):
        """Adding a field to a proof changes the skeleton, signature fails."""
        from app.crypto import hybrid
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        attack = copy.deepcopy(ed_only)
        attack["proof"]["evil"] = "attacker"
        result = hybrid.verify_proof(attack, ed25519_key.verify_key)
        assert result["valid"] is False

    def test_legacy_sort_keys_backward_compat(self, ed25519_key, sample_credential):
        """Pre-JCS credentials (no canonicalizationAlgorithm) still verify."""
        from app.crypto import hybrid
        _setup_pqc(available=False)
        legacy = dict(sample_credential)
        sig = ed25519_key.sign(json.dumps(legacy, sort_keys=True).encode()).signature
        legacy["proof"] = {
            "type": "Ed25519Signature2020",
            "verificationMethod": "did:web:api.moltrust.ch#key-1",
            "proofPurpose": "assertionMethod",
            "proofValue": sig.hex(),
        }
        result = hybrid.verify_proof(legacy, ed25519_key.verify_key)
        assert result["valid"] is True


# ===========================================================================
# Input validation (no 500 errors on malformed input)
# ===========================================================================

class TestInputValidation:
    """Malformed inputs must return valid=False, not raise."""

    @pytest.fixture
    def valid_cred(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        return hybrid.dual_sign(dict(sample_credential), ed25519_key)

    @pytest.mark.parametrize("bad_cred", [
        None,
        "not a dict",
        42,
        3.14,
        [1, 2, 3],
        True,
        False,
    ])
    def test_non_dict_credential_rejected(self, ed25519_key, bad_cred):
        from app.crypto import hybrid
        result = hybrid.verify_proof(bad_cred, ed25519_key.verify_key)
        assert result["valid"] is False

    @pytest.mark.parametrize("bad_proof", [
        None,
        42,
        "string",
        [],
        [None, 42, "string"],
    ])
    def test_bad_proof_field_rejected(self, ed25519_key, valid_cred, bad_proof):
        from app.crypto import hybrid
        cred = copy.deepcopy(valid_cred)
        cred["proof"] = bad_proof
        result = hybrid.verify_proof(cred, ed25519_key.verify_key)
        assert result["valid"] is False

    @pytest.mark.parametrize("bad_vm", [None, 42, "", [], {}])
    def test_bad_verification_method_rejected(self, ed25519_key, valid_cred, bad_vm):
        from app.crypto import hybrid
        cred = copy.deepcopy(valid_cred)
        cred["proof"]["verificationMethod"] = bad_vm
        result = hybrid.verify_proof(cred, ed25519_key.verify_key)
        assert result["valid"] is False

    @pytest.mark.parametrize("bad_pv", [None, 42, 3.14, [], {}, b"\xaa" * 32, ""])
    def test_bad_proofvalue_rejected(self, ed25519_key, valid_cred, bad_pv):
        from app.crypto import hybrid
        cred = copy.deepcopy(valid_cred)
        cred["proof"]["proofValue"] = bad_pv
        result = hybrid.verify_proof(cred, ed25519_key.verify_key)
        assert result["valid"] is False

    def test_non_hex_proofvalue_rejected(self, ed25519_key, valid_cred):
        from app.crypto import hybrid
        cred = copy.deepcopy(valid_cred)
        cred["proof"]["proofValue"] = "not_hex_zzz"
        result = hybrid.verify_proof(cred, ed25519_key.verify_key)
        assert result["valid"] is False

    def test_short_proofvalue_rejected(self, ed25519_key, valid_cred):
        from app.crypto import hybrid
        cred = copy.deepcopy(valid_cred)
        cred["proof"]["proofValue"] = "aa" * 10  # 10 bytes, not 64
        result = hybrid.verify_proof(cred, ed25519_key.verify_key)
        assert result["valid"] is False

    def test_very_long_proofvalue_rejected(self, ed25519_key, valid_cred):
        """Multi-megabyte hex strings must be rejected before bytes.fromhex."""
        from app.crypto import hybrid
        cred = copy.deepcopy(valid_cred)
        cred["proof"]["proofValue"] = "aa" * 50_000  # 100k hex chars
        result = hybrid.verify_proof(cred, ed25519_key.verify_key)
        assert result["valid"] is False
        assert "too long" in result["checks"][0]["error"]


# ===========================================================================
# Type confusion (exact-match dispatch)
# ===========================================================================

class TestTypeConfusion:
    """Type field must be exact-match, not substring."""

    @pytest.fixture
    def valid_cred(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        return hybrid.dual_sign(dict(sample_credential), ed25519_key)

    @pytest.mark.parametrize("bad_type", [
        "EvilEd25519NotReally",
        "Ed25519",  # bare, not the full 2020 form
        "ed25519signature2020",  # case sensitivity
        "",
    ])
    def test_non_matching_type_rejected(self, ed25519_key, valid_cred, bad_type):
        from app.crypto import hybrid
        cred = copy.deepcopy(valid_cred)
        cred["proof"]["type"] = bad_type
        result = hybrid.verify_proof(cred, ed25519_key.verify_key)
        assert result["valid"] is False

    def test_exact_type_still_works(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        assert ed_only["proof"]["type"] == "Ed25519Signature2020"
        result = hybrid.verify_proof(ed_only, ed25519_key.verify_key)
        assert result["valid"] is True


# ===========================================================================
# verificationMethod binding (defense-in-depth)
# ===========================================================================

class TestVerificationMethodBinding:
    """verificationMethod must be one of the exact allowed key ids."""

    @pytest.fixture
    def valid_cred(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        return hybrid.dual_sign(dict(sample_credential), ed25519_key)

    @pytest.mark.parametrize("bad_vm", [
        "did:web:api.moltrust.ch#key-ed25519-attacker",
        "did:web:api.moltrust.ch#key-1-attacker",
        "did:web:api.moltrust.ch#key-dilithium-attacker",
        "did:web:api.moltrust.ch#key-ed25519xxxxx",
    ])
    def test_suffix_on_allowed_key_rejected(self, ed25519_key, valid_cred, bad_vm):
        from app.crypto import hybrid
        cred = copy.deepcopy(valid_cred)
        cred["proof"]["verificationMethod"] = bad_vm
        result = hybrid.verify_proof(cred, ed25519_key.verify_key)
        assert result["valid"] is False, (
            f"verificationMethod suffix/prefix attack must be rejected: {bad_vm}"
        )

    def test_exact_key_ed25519_works(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        assert ed_only["proof"]["verificationMethod"] == "did:web:api.moltrust.ch#key-ed25519"
        result = hybrid.verify_proof(ed_only, ed25519_key.verify_key)
        assert result["valid"] is True

    def test_legacy_key_1_still_works(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        legacy = dict(sample_credential)
        sig = ed25519_key.sign(json.dumps(legacy, sort_keys=True).encode()).signature
        legacy["proof"] = {
            "type": "Ed25519Signature2020",
            "verificationMethod": "did:web:api.moltrust.ch#key-1",
            "proofPurpose": "assertionMethod",
            "proofValue": sig.hex(),
        }
        result = hybrid.verify_proof(legacy, ed25519_key.verify_key)
        assert result["valid"] is True


# ===========================================================================
# verify_credential wrapper (additional validation in credentials.py)
# ===========================================================================

class TestVerifyCredentialWrapper:
    """The credentials.py wrapper must also handle malformed input."""

    @pytest.fixture(autouse=True)
    def _set_key_env(self, ed25519_key, monkeypatch):
        hex_key = ed25519_key.encode().hex()
        os.environ["DID_PRIVATE_KEY_HEX"] = hex_key
        # Since the KMS migration (ce18106) get_signing_key() loads the KMS key via
        # get_decrypted_signing_key_hex() and ignores DID_PRIVATE_KEY_HEX. Pin it to
        # the per-test key so signing and verification use the same key.
        import app.credentials as _credentials
        monkeypatch.setattr(_credentials, "get_decrypted_signing_key_hex", lambda: hex_key)
        yield
        os.environ.pop("DID_PRIVATE_KEY_HEX", None)

    @pytest.fixture
    def valid_cred(self, ed25519_key, sample_credential):
        from app.crypto import hybrid
        _setup_pqc(available=False)
        return hybrid.dual_sign(dict(sample_credential), ed25519_key)

    @pytest.mark.parametrize("bad_cred", [None, "string", 42, [1, 2]])
    def test_non_dict_credential_rejected(self, bad_cred):
        from app.credentials import verify_credential
        result = verify_credential(bad_cred)
        assert result["valid"] is False
        assert "not a dict" in result["error"]

    @pytest.mark.parametrize("bad_vm", [None, 42, "", []])
    def test_bad_verification_method_rejected(self, valid_cred, bad_vm):
        from app.credentials import verify_credential
        cred = copy.deepcopy(valid_cred)
        cred["proof"]["verificationMethod"] = bad_vm
        result = verify_credential(cred)
        assert result["valid"] is False

    def test_legitimate_credential_verifies(self, valid_cred):
        from app.credentials import verify_credential
        result = verify_credential(valid_cred)
        assert result["valid"] is True

    def test_legacy_sort_keys_verifies(self, ed25519_key, sample_credential):
        from app.credentials import verify_credential
        legacy = dict(sample_credential)
        sig = ed25519_key.sign(json.dumps(legacy, sort_keys=True).encode()).signature
        legacy["proof"] = {
            "type": "Ed25519Signature2020",
            "verificationMethod": "did:web:api.moltrust.ch#key-1",
            "proofPurpose": "assertionMethod",
            "proofValue": sig.hex(),
        }
        result = verify_credential(legacy)
        assert result["valid"] is True

    def test_pqc_advisory_default_accepts(self, ed25519_key, sample_credential, monkeypatch):
        """Default (PQC_ENFORCE off): Ed25519-only JCS from a PQC-capable
        issuer is ACCEPTED, with pqc_policy marked "would_reject"."""
        from app.credentials import verify_credential
        from app.crypto import hybrid
        monkeypatch.delenv("PQC_ENFORCE", raising=False)
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        _setup_pqc(available=True, dil_verify=True)
        result = hybrid.verify_proof(ed_only, ed25519_key.verify_key)
        assert result["valid"] is True
        assert result.get("pqc_policy") == "would_reject"

    def test_pqc_enforce_rejects(self, ed25519_key, sample_credential, monkeypatch):
        """PQC_ENFORCE on: the same credential is REJECTED, and the explicit
        error is preserved through verify_credential (the f3817d4 fix)."""
        from app.credentials import verify_credential
        from app.crypto import hybrid
        monkeypatch.setenv("PQC_ENFORCE", "true")
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        _setup_pqc(available=True, dil_verify=True)
        result = verify_credential(ed_only)
        assert result["valid"] is False
        assert result["error"], "error field must not be empty"
        assert "PQC policy" in result["error"]

    def test_verify_credential_preserves_early_error(self):
        """verify_credential must preserve non-dict / no-proof errors too."""
        from app.credentials import verify_credential
        result = verify_credential(None)
        assert result["valid"] is False
        assert result["error"], "error field must not be empty"
        assert "not a dict" in result["error"]


# ===========================================================================
# proof_utils helpers
# ===========================================================================

class TestProofUtils:
    """Helpers must handle malformed input gracefully."""

    def test_get_proofs_on_none_returns_empty(self):
        from app.crypto.proof_utils import get_proofs
        assert get_proofs(None) == []

    def test_get_proofs_on_non_dict_returns_empty(self):
        from app.crypto.proof_utils import get_proofs
        assert get_proofs("not a dict") == []
        assert get_proofs(42) == []
        assert get_proofs([1, 2]) == []

    def test_find_proof_skips_non_dict_entries(self):
        from app.crypto.proof_utils import find_proof
        cred = {"proof": [None, 42, "string", {"type": "Ed25519Signature2020"}]}
        p = find_proof(cred, "Ed25519Signature2020")
        assert p is not None
        assert p["type"] == "Ed25519Signature2020"

    def test_get_primary_proof_value_on_none_raises(self):
        """Documented behavior: raises KeyError on no-proof credential."""
        from app.crypto.proof_utils import get_primary_proof_value
        with pytest.raises(KeyError):
            get_primary_proof_value(None)

    def test_find_proof_exact_match_only(self):
        from app.crypto.proof_utils import find_proof
        cred = {"proof": [
            {"type": "EvilEd25519NotReally"},
            {"type": "Ed25519Signature2020"},
        ]}
        p = find_proof(cred, "Ed25519Signature2020")
        assert p is not None
        assert p["type"] == "Ed25519Signature2020"
        # Substring of the declared type must NOT match.
        p2 = find_proof(cred, "Ed25519")
        assert p2 is None


# ===========================================================================
# Dilithium key loading robustness
# ===========================================================================

class TestDilithiumKeyLoading:
    """Bad hex env vars must return None, not crash dual_sign."""

    def test_bad_public_key_hex_returns_none(self):
        from app.crypto import dilithium
        dilithium.clear_cache()
        os.environ["DILITHIUM_PUBLIC_KEY_HEX"] = "notvalidhex"
        os.environ["DILITHIUM_PRIVATE_KEY_HEX"] = "aa" * 32
        assert dilithium._load_keypair() is None
        assert dilithium.is_available() is False
        dilithium.clear_cache()
        os.environ.pop("DILITHIUM_PUBLIC_KEY_HEX", None)
        os.environ.pop("DILITHIUM_PRIVATE_KEY_HEX", None)

    def test_bad_private_key_hex_returns_none(self):
        from app.crypto import dilithium
        dilithium.clear_cache()
        os.environ["DILITHIUM_PUBLIC_KEY_HEX"] = "aa" * 32
        os.environ["DILITHIUM_PRIVATE_KEY_HEX"] = "notvalidhex"
        assert dilithium._load_keypair() is None
        assert dilithium.is_available() is False
        dilithium.clear_cache()
        os.environ.pop("DILITHIUM_PUBLIC_KEY_HEX", None)
        os.environ.pop("DILITHIUM_PRIVATE_KEY_HEX", None)


# ===========================================================================
# PQC verify-policy: PQC-capable issuer must dual-sign JCS credentials
# ===========================================================================

class TestPQCVerifyPolicy:
    """The 3-model review's blocker: a PQC-capable issuer must not be able to
    emit Ed25519-only JCS credentials. The verify path must reject them."""

    def test_pqc_issuer_ed25519_only_jcs_rejected(self, ed25519_key, sample_credential, monkeypatch):
        """PQC_ENFORCE on: PQC available + JCS + only Ed25519 proof -> rejected."""
        from app.crypto import hybrid
        monkeypatch.setenv("PQC_ENFORCE", "true")
        # Issue Ed25519-only (PQC not configured at sign time)
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        assert isinstance(ed_only["proof"], dict)  # single proof

        # Now verifier has PQC configured
        _setup_pqc(available=True, dil_verify=True)
        result = hybrid.verify_proof(ed_only, ed25519_key.verify_key)
        assert result["valid"] is False
        assert "PQC policy" in result["error"]

    def test_pqc_issuer_dual_signed_accepted(self, ed25519_key, sample_credential):
        """PQC available + JCS credential + dual proof → accepted."""
        from app.crypto import hybrid
        _setup_pqc(available=True, dil_verify=True)
        dual = hybrid.dual_sign(dict(sample_credential), ed25519_key)
        assert isinstance(dual["proof"], list)

        result = hybrid.verify_proof(dual, ed25519_key.verify_key)
        assert result["valid"] is True

    def test_non_pqc_verifier_ed25519_only_jcs_accepted(self, ed25519_key, sample_credential):
        """Non-PQC verifier accepts Ed25519-only JCS credential."""
        from app.crypto import hybrid
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)

        # Verifier also has no PQC
        _setup_pqc(available=False)
        result = hybrid.verify_proof(ed_only, ed25519_key.verify_key)
        assert result["valid"] is True

    def test_pqc_verifier_legacy_sort_keys_exempt(self, ed25519_key, sample_credential):
        """PQC verifier accepts legacy (non-JCS) Ed25519-only credentials."""
        from app.crypto import hybrid
        _setup_pqc(available=True, dil_verify=True)

        legacy = dict(sample_credential)
        sig = ed25519_key.sign(json.dumps(legacy, sort_keys=True).encode()).signature
        legacy["proof"] = {
            "type": "Ed25519Signature2020",
            "verificationMethod": "did:web:api.moltrust.ch#key-1",
            "proofPurpose": "assertionMethod",
            "proofValue": sig.hex(),
        }
        result = hybrid.verify_proof(legacy, ed25519_key.verify_key)
        assert result["valid"] is True

    def test_policy_fires_when_public_key_configured_but_kms_down(self, ed25519_key, sample_credential, monkeypatch):
        """Policy fires on public key config alone, independent of is_available().

        Scenario: the Dilithium public key is configured (issuer is PQC-capable)
        but the secret key / KMS is temporarily unavailable. The policy must
        still fire — the issuer is PQC-capable by declaration, and accepting
        Ed25519-only JCS credentials would be a policy downgrade.
        """
        from app.crypto import hybrid
        from app.crypto import dilithium

        monkeypatch.setenv("PQC_ENFORCE", "true")
        # Issue an Ed25519-only JCS credential
        _setup_pqc(available=False)
        ed_only = hybrid.dual_sign(dict(sample_credential), ed25519_key)

        # Simulate "KMS down" — is_available() returns False, but the public
        # key is still configured (issuer is PQC-capable by declaration).
        dilithium.is_available = lambda: False
        dilithium.public_key_configured = lambda: True

        result = hybrid.verify_proof(ed_only, ed25519_key.verify_key)
        assert result["valid"] is False
        assert "PQC policy" in result["error"]


# ===========================================================================
# dilithium.public_key_configured() — standalone env-var check
# ===========================================================================

class TestPublicKeyConfigured:
    """public_key_configured() must reflect the env var, not the full keypair."""

    def test_no_env_var_returns_false(self):
        from app.crypto import dilithium
        dilithium.clear_cache()
        os.environ.pop("DILITHIUM_PUBLIC_KEY_HEX", None)
        assert dilithium.public_key_configured() is False

    def test_env_var_set_returns_true(self):
        from app.crypto import dilithium
        dilithium.clear_cache()
        os.environ["DILITHIUM_PUBLIC_KEY_HEX"] = "aa" * 32
        try:
            assert dilithium.public_key_configured() is True
        finally:
            os.environ.pop("DILITHIUM_PUBLIC_KEY_HEX", None)

    def test_empty_env_var_returns_false(self):
        from app.crypto import dilithium
        dilithium.clear_cache()
        os.environ["DILITHIUM_PUBLIC_KEY_HEX"] = "   "
        try:
            assert dilithium.public_key_configured() is False
        finally:
            os.environ.pop("DILITHIUM_PUBLIC_KEY_HEX", None)
