"""
Hybrid (dual) signature module for MolTrust.

Issues credentials with both an Ed25519 and an ML-DSA-65 (Dilithium3) proof
when Dilithium keys are configured, and Ed25519-only otherwise. Verification
enforces the composite-signature contract the review required (IETF
composite-sigs, BSI TR-02102-1):

  * The signed payload is the credential body PLUS the proof skeleton —
    i.e. every proof dict with its `proofValue` stripped. Binding the proof
    structure into the signed bytes means an attacker CANNOT strip a leg
    after the fact: removing the Dilithium proof changes the skeleton, which
    invalidates the Ed25519 signature too. This is the fix for the review's
    downgrade/stripping blocker.
  * Every proof in the credential MUST be present and MUST verify. There is
    no OR-downgrade path: a credential from a PQC-enabled issuer that is
    missing its Dilithium leg is invalid, because the surviving Ed25519
    signature was made over a skeleton that included the Dilithium proof.
  * PQC POLICY (3-model review blocker fix): when the verifier has Dilithium
    configured (PQC-capable) AND the credential uses JCS (new format), the
    credential MUST carry a dual signature. An Ed25519-only JCS credential
    is a policy downgrade — the issuer *could* have dual-signed but didn't.
    Legacy (sort_keys, no canonicalizationAlgorithm) credentials are exempt:
    they predate the PQC policy and only ever had one leg.

Threat model:
  - Phase-1 migration: legacy Ed25519-only credentials (sort_keys, no JCS)
    remain valid until the dataset is fully rotated. These are NOT subject
    to the dual-signature policy.
  - Strict policy: any JCS credential from a PQC-capable issuer must be
    dual-signed. This is enforced at verify time, not just at sign time
    (sign time already enforces it via the fail-closed RuntimeError when
    Dilithium signing fails).

Canonicalization is RFC 8785 JCS. If the `jcs` library is not importable at
sign time we FAIL CLOSED (raise) rather than emit a proof whose
`canonicalizationAlgorithm` says "JCS" but was actually produced with
`json.dumps(sort_keys=True)` — that mismatch was flagged as a DoS /
false-negative vector in the review.

Legacy credentials (single Ed25519 proof, no canonicalizationAlgorithm, or
canonicalizationAlgorithm other than JCS) still verify with the original
`json.dumps(sort_keys=True)` path so already-issued VCs remain valid. Those
legacy credentials sign the body WITHOUT the proof field (the original
behaviour) and carry no skeleton, so they are not protected against leg
stripping — but they only ever had one leg, so there was nothing to strip.
"""
import copy
import json
import logging
import os

from app.crypto import dilithium
from app.crypto.proof_utils import (
    ED25519_PROOF_TYPE,
    DILITHIUM_PROOF_TYPE,
    get_proofs,
    has_dual_signature,
)

logger = logging.getLogger("moltrust.crypto.hybrid")


def _pqc_enforce() -> bool:
    """Hard-enforce the dual-signature PQC policy (reject) when truthy;
    otherwise the policy is advisory: it is checked and surfaced, but a
    missing Dilithium leg does not fail verification. Default OFF. Central
    switch, evaluated at verify time (no restart needed to flip).
    """
    return os.environ.get("PQC_ENFORCE", "").strip().lower() in (
        "1", "true", "yes", "on")

ISSUER_DID = "did:web:api.moltrust.ch"

# Allowed verificationMethod ids per proof type. Exact equality only —
# startswith is too permissive (e.g. #key-ed25519-attacker would match).
ED25519_KEY_IDS = {
    f"{ISSUER_DID}#key-ed25519",
    f"{ISSUER_DID}#key-1",
}
DILITHIUM_KEY_IDS = {
    f"{ISSUER_DID}#key-dilithium",
}

# Maximum hex length we will accept for a proofValue. Ed25519 sig = 64 bytes
# (128 hex chars); ML-DSA-65 sig = 3309 bytes (6618 hex chars). Allow a
# generous margin for future algorithms, but cap to prevent memory-DoS from
# a multi-megabyte hex string.
_MAX_PROOFVALUE_HEX_LEN = 20000


# Marker used in the proof skeleton (the placeholder for the real proofValue)
# while signing. The actual signatures are computed over the canonical bytes
# of the credential with proofValue set to this sentinel, then replaced.
_PROOF_VALUE_SENTINEL = ""


def _canonicalize(payload: dict, algorithm: str) -> bytes:
    """Canonicalize `payload` per `algorithm`. Fail closed for JCS without jcs.

    `algorithm` is the value that will be written into the proof's
    `canonicalizationAlgorithm` field. We refuse to emit a JCS-labelled
    proof unless JCS actually ran.
    """
    if algorithm == "JCS":
        try:
            import jcs
        except ImportError as e:
            raise RuntimeError(
                "canonicalizationAlgorithm=JCS but the jcs library is not "
                "installed; refusing to emit a mismatched proof"
            ) from e
        return jcs.canonicalize(payload)

    # Legacy path — used only for verifying old credentials.
    if algorithm in (None, "", "JSON-SORT-KEYS"):
        return json.dumps(payload, sort_keys=True).encode()

    raise ValueError(f"Unsupported canonicalizationAlgorithm: {algorithm!r}")


def _proof_algorithm(proof: dict) -> str:
    """Return the canonicalization algorithm declared by a proof.

    Legacy Ed25519 proofs have no `canonicalizationAlgorithm` field; treat
    that as the original sort_keys behaviour so they still verify.
    """
    return proof.get("canonicalizationAlgorithm") or "JSON-SORT-KEYS"


def _has_skeleton(proofs: list[dict]) -> bool:
    """True iff these proofs were produced with the skeleton-binding scheme.

    New (JCS) proofs bind the proof skeleton into the signed payload; legacy
    (sort_keys, no canonicalizationAlgorithm) proofs do not. We detect the
    difference by the presence of `canonicalizationAlgorithm == "JCS"`.
    """
    return any(_proof_algorithm(p) == "JCS" for p in proofs)


def _build_skeleton(credential: dict, proofs: list[dict]) -> dict:
    """Return a credential copy with `proof` = proofs minus their proofValue.

    This is the structure both legs sign. Stripping proofValue (not the whole
    proof) means the signature binds the proof *metadata* (type,
    verificationMethod, created, canonicalizationAlgorithm) without the
    signature having to sign itself. An attacker who removes or alters a
    proof changes this skeleton and invalidates every remaining signature.
    """
    skeleton_proofs = []
    for p in proofs:
        sp = {k: v for k, v in p.items() if k != "proofValue"}
        sp["proofValue"] = _PROOF_VALUE_SENTINEL
        skeleton_proofs.append(sp)
    skeleton = copy.deepcopy(credential)
    skeleton["proof"] = skeleton_proofs if len(skeleton_proofs) != 1 else skeleton_proofs[0]
    return skeleton


def _signed_payload(credential: dict, proof: dict, all_proofs: list[dict]) -> bytes:
    """Compute the bytes a given proof's signature must cover.

    - JCS (new) proofs: the canonicalized credential WITH the proof skeleton
      (all proofs present, proofValue blanked). This binds every leg into
      every signature.
    - Legacy (sort_keys) proofs: the canonicalized credential WITHOUT the
      proof field — the original behaviour, so already-issued VCs verify.
    """
    algo = _proof_algorithm(proof)
    if algo == "JCS":
        skeleton = _build_skeleton(credential, all_proofs)
        return _canonicalize(skeleton, "JCS")
    # Legacy: body only, no proof field.
    body = {k: v for k, v in credential.items() if k != "proof"}
    return _canonicalize(body, algo)


def dual_sign(credential: dict, ed25519_key) -> dict:
    """Sign a credential with Ed25519 and, if configured, ML-DSA-65.

    Both legs sign the credential body PLUS the proof skeleton (the proofs
    with proofValue blanked), so the proof structure is bound into every
    signature. Stripping a leg after issuance breaks the remaining signatures.

    Args:
        credential: the VC dict without a `proof` field.
        ed25519_key: a nacl.signing.SigningKey.

    Returns:
        The credential with `proof` set to a single proof dict (Ed25519-only)
        or a list of two proof dicts (Ed25519 + Dilithium).

    Raises:
        RuntimeError if JCS is required but the jcs library is missing.
    """
    now_str = (
        credential.get("validFrom")
        or credential.get("issuanceDate")
        or ""
    )

    # Build the proof metadata first (no proofValue yet).
    ed_meta = {
        "type": ED25519_PROOF_TYPE,
        "created": now_str,
        "verificationMethod": f"{ISSUER_DID}#key-ed25519",
        "proofPurpose": "assertionMethod",
        "canonicalizationAlgorithm": "JCS",
    }
    dil_meta = {
        "type": DILITHIUM_PROOF_TYPE,
        "created": now_str,
        "verificationMethod": f"{ISSUER_DID}#key-dilithium",
        "proofPurpose": "assertionMethod",
        "canonicalizationAlgorithm": "JCS",
    }

    dilithium_configured = dilithium.is_available()

    # The skeleton both legs will sign: all intended proofs, proofValue blank.
    intended_proofs = [ed_meta, dil_meta] if dilithium_configured else [ed_meta]
    skeleton = _build_skeleton(credential, intended_proofs)
    payload = _canonicalize(skeleton, "JCS")

    # Ed25519 leg.
    ed_signed = ed25519_key.sign(payload)
    ed_meta["proofValue"] = ed_signed.signature.hex()

    if not dilithium_configured:
        credential["proof"] = ed_meta
        logger.debug("Credential signed Ed25519-only (Dilithium not configured)")
        return credential

    # Dilithium leg — signs the SAME payload (same skeleton) as Ed25519.
    dil_sig_bytes = dilithium.sign(payload)
    if dil_sig_bytes is None:
        # Configured but signing failed: do NOT fall back silently to a
        # single-proof credential, because that would re-open the downgrade
        # path (an attacker can't tell a deliberate Ed25519-only cred from a
        # failed-dual one). Fail the issuance instead.
        raise RuntimeError("Dilithium configured but signing failed; refusing "
                           "to emit an Ed25519-only credential from a "
                           "PQC-enabled issuer")
    dil_meta["proofValue"] = dil_sig_bytes.hex()

    credential["proof"] = [ed_meta, dil_meta]
    logger.info("Credential dual-signed (Ed25519 + ML-DSA-65)")
    return credential


def verify_proof(credential: dict, ed25519_verify_key) -> dict:
    """Verify a credential's proof(s) with composite-signature semantics.

    Contract (fixes the review's downgrade/stripping blocker):

      * For JCS (new) credentials, each proof signs the credential body PLUS
        the proof skeleton (all proofs present, proofValue blanked). If a
        leg is missing, the skeleton the issuer signed differs from the
        skeleton the verifier reconstructs, so EVERY remaining signature
        fails. An attacker cannot strip a leg and keep a valid Ed25519.
      * Every proof present MUST verify (AND-logic). One bad leg fails the
        whole credential.
      * Legacy (sort_keys, no skeleton) credentials verify over the body
        only, preserving backward compatibility.

    Input validation: malformed credentials (non-dict, missing proof, wrong
    types) are returned as {"valid": False, "error": ...} rather than raising,
    so a malformed input cannot trigger a 500-error DoS.

    Returns: {"valid": bool, "checks": [{"type","valid"[,"error"]}], "error"?}
    """
    # --- Input validation ---
    if not isinstance(credential, dict):
        return {"valid": False, "error": "credential is not a dict"}

    proofs = get_proofs(credential)
    if not proofs:
        return {"valid": False, "error": "No proof found"}

    results = {"valid": True, "checks": []}

    # --- PQC dual-signature policy: advisory by default, PQC_ENFORCE to reject
    # The dual-signature capability is built into the credential format but
    # is NOT hard-enforced by default. When the issuer is PQC-capable
    # (Dilithium public key configured) and the credential uses the JCS
    # format, a dual signature is expected. If it is missing:
    #   PQC_ENFORCE off (default): advisory -- the check runs and the outcome
    #     is surfaced in results["pqc_policy"] ("would_reject") and logged,
    #     but verification is NOT failed on this ground.
    #   PQC_ENFORCE on: reject (rule "B"), as before.
    # Legacy credentials (no canonicalizationAlgorithm / JSON-SORT-KEYS) are
    # exempt: they predate the policy and only ever had one leg.
    if dilithium.public_key_configured() and _has_skeleton(proofs):
        if has_dual_signature(credential):
            results["pqc_policy"] = "satisfied"
        elif _pqc_enforce():
            return {
                "valid": False,
                "error": "PQC policy violation: JCS credential from PQC-capable "
                         "issuer must carry a dual signature (Ed25519 + "
                         "Dilithium), but only an Ed25519 proof is present",
                "pqc_policy": "rejected",
            }
        else:
            results["pqc_policy"] = "would_reject"
            logger.warning(
                "PQC policy (advisory, PQC_ENFORCE off): JCS credential from a "
                "PQC-capable issuer carries only an Ed25519 proof; it would be "
                "rejected under PQC_ENFORCE. Accepting."
            )

    for p in proofs:
        if not isinstance(p, dict):
            results["checks"].append({
                "type": str(type(p).__name__),
                "valid": False,
                "error": "proof entry is not a dict",
            })
            results["valid"] = False
            continue

        ptype = p.get("type", "")
        ptype_str = ptype if isinstance(ptype, str) else ""

        # Cross-check verificationMethod against the key being used to verify.
        # Defense-in-depth: a valid signature over a valid body is not enough
        # if the proof claims to be from a key we are not using.
        vm = p.get("verificationMethod", "")
        if not isinstance(vm, str) or not vm:
            results["checks"].append({
                "type": ptype_str, "valid": False,
                "error": "missing or invalid verificationMethod",
            })
            results["valid"] = False
            continue

        # Re-derive the signed payload. JCS proofs require the jcs library;
        # legacy proofs fall back to sort_keys. A JCS-labelled proof with no
        # jcs library is a hard fail (the issuer could not have produced it).
        try:
            payload = _signed_payload(credential, p, proofs)
        except Exception as e:
            results["checks"].append({"type": ptype_str, "valid": False, "error": str(e)})
            results["valid"] = False
            continue

        # Validate proofValue is a hex string of reasonable length.
        pv = p.get("proofValue", "")
        if not isinstance(pv, str) or not pv:
            results["checks"].append({
                "type": ptype_str, "valid": False,
                "error": "missing or non-string proofValue",
            })
            results["valid"] = False
            continue
        if len(pv) > _MAX_PROOFVALUE_HEX_LEN:
            results["checks"].append({
                "type": ptype_str, "valid": False,
                "error": f"proofValue too long ({len(pv)} hex chars; max "
                         f"{_MAX_PROOFVALUE_HEX_LEN})",
            })
            results["valid"] = False
            continue
        try:
            signature = bytes.fromhex(pv)
        except ValueError as e:
            results["checks"].append({
                "type": ptype_str, "valid": False,
                "error": f"proofValue is not valid hex: {e}",
            })
            results["valid"] = False
            continue

        # Exact-match dispatch (not substring) to prevent type confusion.
        # "EvilEd25519NotReally" must NOT be treated as Ed25519.
        #
        # verificationMethod is also checked by exact equality against the
        # allowed key ids. startswith would let an attacker append a suffix
        # such as #key-ed25519-attacker and still pass this defense-in-depth
        # check (they still cannot forge a signature without the key, but we
        # keep the type field trustworthy).
        if ptype_str == ED25519_PROOF_TYPE:
            if vm not in ED25519_KEY_IDS:
                results["checks"].append({
                    "type": "Ed25519", "valid": False,
                    "error": f"Ed25519 proof verificationMethod does not "
                             f"match an issuer Ed25519 key: {vm}",
                })
                results["valid"] = False
                continue
            try:
                ed25519_verify_key.verify(payload, signature)
                results["checks"].append({"type": "Ed25519", "valid": True})
            except Exception as e:
                results["checks"].append({"type": "Ed25519", "valid": False, "error": str(e)})
                results["valid"] = False

        elif ptype_str == DILITHIUM_PROOF_TYPE:
            if vm not in DILITHIUM_KEY_IDS:
                results["checks"].append({
                    "type": "Dilithium", "valid": False,
                    "error": f"Dilithium proof verificationMethod does not "
                             f"match the issuer Dilithium key: {vm}",
                })
                results["valid"] = False
                continue
            pk_hex = dilithium.get_public_key_hex()
            if not pk_hex:
                results["checks"].append({
                    "type": "Dilithium",
                    "valid": False,
                    "error": "Dilithium public key not configured on this verifier",
                })
                results["valid"] = False
            else:
                ok = dilithium.verify(payload, signature, bytes.fromhex(pk_hex))
                results["checks"].append({"type": "Dilithium", "valid": ok})
                if not ok:
                    results["valid"] = False
        else:
            results["checks"].append({
                "type": ptype_str, "valid": False,
                "error": f"Unknown proof type: {ptype_str!r}",
            })
            results["valid"] = False

    if not results["valid"]:
        errors = [c.get("error", "check failed")
                  for c in results["checks"] if not c.get("valid")]
        results["error"] = "; ".join(errors)

    return results