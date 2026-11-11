"""Offline trust gate for x402 endpoints.

The implementation lives in :mod:`moltrust_enforce.gate`. This package is the
name an agent developer looks for, re-exporting that module without copying it.

    from moltrust_x402 import require_moltrust, load_jwks

Everything re-exported from moltrust_enforce is the same object as there, so a
behaviour change reaches both names at once and the parity vectors keep
covering three implementations rather than four.

What this package adds on its own is the invitation in the denial --
``gate_block``, ``invite_for``, ``parse_operator_did`` -- so an integrator who
sets ``operatorDid`` is credited as the referrer for the agents the gate sends
to register. See :mod:`moltrust_x402.invite`.
"""
from moltrust_enforce.gate import (
    Decision,
    GateAttestation,
    binding_string,
    check_track_record,
    load_jwks,
    require_moltrust,
    verify_attestation,
)

# The invitation is this package's own, not the verification core's: it is a
# packaging concern -- who gets credited for a registration a third party's
# gate produces -- and moltrust_enforce has no business holding an opinion
# about that.
from .invite import (
    CHALLENGE_URL,
    INVITE_SOURCE,
    INVITE_TEXT,
    REGISTER_URL,
    gate_block,
    invite_for,
    parse_operator_did,
)

__all__ = [
    "CHALLENGE_URL",
    "Decision",
    "GateAttestation",
    "INVITE_SOURCE",
    "INVITE_TEXT",
    "REGISTER_URL",
    "binding_string",
    "check_track_record",
    "gate_block",
    "invite_for",
    "load_jwks",
    "parse_operator_did",
    "require_moltrust",
    "verify_attestation",
]

__version__ = "2.1.0"
