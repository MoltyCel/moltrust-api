"""Signed input for POST /enforce/check: AAE -02 §5 steps 1, 2, 3 and 9 before the kernel.

The kernel (`enforce_check.py`) decides over a mandate it is handed and checks grant
attenuation over the ancestors it is handed. It cannot tell a mandate somebody signed
from one the caller typed, nor a real parent from an invented one. This module closes
that gap for callers who send the envelope itself: the presented AAE and every inline
ancestor are verified (signature, signing authority, schema), the chain is walked by
`delegator_aae_id`, `delegator_aae_hash` and the depth rules, and the effective validity
window of the whole chain must contain the current time. Only then are the mandate and
the parent mandates taken from the verified payloads and handed to the kernel.

The verdict core stays what it was: a function of mandate, transaction and ancestors,
recomputable without this server. What the server adds is the statement that those
inputs came out of a verified chain; it travels in the response next to the record,
not inside it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.enforcement.acceptance_gate import (AcceptanceError, _core_for_ancestor,
                                             _verify_envelope_core)
from app.enforcement.delegation_chain import DelegationChainError, verify_delegation_chain


class ChainRefused(Exception):
    """The signed input does not verify. The endpoint answers 422 with this text."""


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


async def verified_input(aae_jws: str, ancestor_jws, conn, now: datetime | None = None):
    """Return (mandate, ancestors root first, chain statement) from a verified chain."""
    parsed: dict = {}

    async def capture(raw, c):
        vc, did = await _core_for_ancestor(raw, c)
        parsed[vc["id"]] = vc
        return vc, did

    try:
        vc, signing_did, _payload, _tier = await _verify_envelope_core(aae_jws, conn)
        chain = await verify_delegation_chain(
            vc, aae_jws=aae_jws, ancestor_jws=ancestor_jws, conn=conn,
            verify_core=capture, signing_did=signing_did)
    except (AcceptanceError, DelegationChainError) as e:
        raise ChainRefused(str(e))

    validity = vc["credentialSubject"]["aae"]["validity"]
    not_before = chain["effective_not_before"] if chain else validity.get("not_before")
    not_after = chain["effective_not_after"] if chain else validity.get("not_after")
    now = now or datetime.now(timezone.utc)
    try:
        if not (_parse(not_before) <= now <= _parse(not_after)):
            raise ChainRefused(f"outside the effective validity window "
                               f"{not_before} .. {not_after} (§5 step 3, step 9)")
    except (TypeError, ValueError, AttributeError):
        raise ChainRefused("validity window missing or unreadable")

    mandate = vc["credentialSubject"]["aae"]["mandate"]
    if chain is None:
        return mandate, [], {"verified": True, "delegated": False, "aae_id": vc["id"],
                             "not_before": not_before, "not_after": not_after}
    ancestors = [parsed[i]["credentialSubject"]["aae"]["mandate"]
                 for i in reversed(chain["path"][1:])]
    return mandate, ancestors, {"verified": True, "delegated": True, "aae_id": vc["id"],
                                "path": chain["path"], "chain_length": chain["chain_length"],
                                "not_before": not_before, "not_after": not_after}
