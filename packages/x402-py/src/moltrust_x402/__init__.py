"""Offline trust gate for x402 endpoints.

The implementation lives in :mod:`moltrust_enforce.gate`. This package is the
name an agent developer looks for, re-exporting that module without copying it.

    from moltrust_x402 import require_moltrust, load_jwks

Everything importable here is the same object as in moltrust_enforce, so a
behaviour change reaches both names at once and the parity vectors keep
covering three implementations rather than four.
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

__all__ = [
    "Decision",
    "GateAttestation",
    "binding_string",
    "check_track_record",
    "load_jwks",
    "require_moltrust",
    "verify_attestation",
]

__version__ = "2.0.0"
