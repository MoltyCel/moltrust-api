"""The invitation in the 403.

A gate that only turns callers away tells them nothing about how to stop being
turned away. On ``attestation_missing`` the denial carries a free registration
instead, with whoever runs the gate named as the referrer, so an integrator
who puts this package in front of an endpoint brings agents in by doing so.

Two rules, the same two the moltguard service applies to its 402. The invite
goes out on ``attestation_missing`` alone: the gate works offline and cannot
tell an unregistered agent from a registered one that sent no attestation, and
that reason is the only form in which an unregistered agent arrives. Every
other reason means the caller has an identity and something about it failed,
and an invitation to register would answer the wrong question. And without an
operator DID in the integrator's own configuration there is no invite at all --
a fallback referrer would credit registrations to someone who never put the
gate up.

The gate itself returns a :class:`~moltrust_enforce.gate.Decision` and leaves
the response to the host, so this module is what the host puts in it::

    decision = gate(request.method, request.path, request.headers)
    if not decision:
        return 403, {
            "error": decision.reason,
            "detail": decision.detail,
            "moltrustGate": gate_block(decision, OPERATOR_DID),
        }

The block is the one the TypeScript package emits, field for field; both are
held to that by the shared vectors in ``tests/invite-vectors.json``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional, Tuple

__all__ = [
    "CHALLENGE_URL",
    "INVITE_SOURCE",
    "INVITE_TEXT",
    "REGISTER_URL",
    "gate_block",
    "invite_for",
    "parse_operator_did",
]

REGISTER_URL = "https://api.moltrust.ch/identity/register-pop"
CHALLENGE_URL = "https://api.moltrust.ch/identity/register-challenge"

#: Passed as ``source`` to register-pop, so the registration is attributable.
#: Deliberately not the service's ``moltguard-402``: registrations that arrive
#: through a third party's gate have to be countable apart from the ones our
#: own gate brings in, or the cohort figures cannot say which of the two is
#: working.
INVITE_SOURCE = "x402-package-403"

INVITE_TEXT = (
    "Registering this agent with MolTrust is free and needs no account: GET the "
    "challenge, then POST it signed to register_url with referrer_did and source "
    "as given here."
)

#: Method spec 2.2: sixteen lowercase hex characters after the prefix.
_MOLTRUST_DID = re.compile(r"^did:moltrust:[0-9a-f]{16}$")


def parse_operator_did(raw: Optional[str]) -> Optional[str]:
    """The operator DID from the integrator's configuration, or ``None``.

    A value that is set but not a did:moltrust identifier counts as absent:
    crediting a typo would attribute every registration it produces to nobody.
    """
    value = (raw or "").strip()
    return value if _MOLTRUST_DID.match(value) else None


def _reason_detail_did(decision: Any) -> Tuple[Any, Any, Any]:
    """Read ``reason``, ``detail`` and ``did`` off a Decision or a mapping."""
    if isinstance(decision, Mapping):
        return decision.get("reason"), decision.get("detail"), decision.get("did")
    return (
        getattr(decision, "reason", None),
        getattr(decision, "detail", None),
        getattr(decision, "did", None),
    )


def invite_for(decision: Any, operator_did: Optional[str]) -> Optional[Dict[str, str]]:
    """The invite for a decision, or ``None`` when one of the two rules says no."""
    if not operator_did:
        return None
    reason, _, did = _reason_detail_did(decision)
    if reason != "attestation_missing":
        return None
    # A caller the gate can name is one it already knows. `attestation_missing`
    # never carries a DID, and a denial that does is not an unregistered agent.
    if did:
        return None
    return {
        "register_url": REGISTER_URL,
        "challenge_url": CHALLENGE_URL,
        "referrer_did": operator_did,
        "source": INVITE_SOURCE,
        "text": INVITE_TEXT,
    }


def gate_block(decision: Any, operator_did: Optional[str]) -> Dict[str, Any]:
    """The ``moltrustGate`` block of a 403.

    What the gate decided, and the invite if one applies.
    """
    reason, detail, _ = _reason_detail_did(decision)
    block: Dict[str, Any] = {"reason": reason, "detail": detail or ""}
    invite = invite_for(decision, operator_did)
    if invite is not None:
        block["invite"] = invite
    return block
