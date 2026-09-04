"""AAE §5 Step 9 — delegation-chain walk over inline ancestors.

A delegated AAE carries a `mandate.delegation` object naming its parent. Step 9
verifies every ancestor in the chain and the link between each pair: the parent's
`credentialSubject.id` equals the child's `delegator_did`, signing authority holds
for each AAE, the constraints narrow monotonically, and the depth rules of
Section 3 hold. Cycles are rejected by tracking the AAE ids already visited.

Ancestors are supplied inline with the request. Section 3 makes
`delegator_aae_uri` REQUIRED "unless the parent AAE is embedded in the request by
the transport binding", and this implementation takes the embedded route: a chain
verifies from the JWS the caller hands over, with no outbound fetch. A delegation
that names only a URI raises NotImplementedError and is deferred together with
did:web resolution and Step 8 revocation, all three waiting on the egress proxy.

What Step 9 does NOT do for ancestors, per the draft: subject binding (Step 4) and
the single-use check (Step 5). Ancestor agents are not required to be online to
answer a challenge, and single-use state applies only to the presented AAE.

Temporal validity stays where the acceptance gate has always left it. The walk
computes the effective window — the latest not_before and the earliest not_after
over the chain — and returns it; the Evaluator applies Step 3 at evaluate-time.
Monotonic nesting of the windows is a structural rule of Section 3 and IS enforced
here.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from typing import Any, Callable

# An implementation-defined ceiling, as Step 9 requires. The limit actually applied
# is the smaller of this and the smallest max_depth observed in the chain.
MAX_RECURSION_LIMIT = 8
# Bound the inline material before any of it is verified.
MAX_ANCESTORS = 16

_HASH_PREFIX = "sha-256:"

# Comparison rules per constraint type (Section 3, "equal to or more restrictive").
_NUMERIC_UPPER_BOUND = {"max_transaction_value"}
_ALLOWLIST = {"allowed_domains"}
_RATE_LIMIT = {"rate_limit"}
# Constraint types whose value carries a currency and must not change under delegation.
_CURRENCY_VALUED = {"max_transaction_value"}


class DelegationChainError(ValueError):
    """Step 9 rejected (fail-closed). The acceptance gate maps this to its own error."""


def _aae(vc: dict) -> dict:
    return vc["credentialSubject"]["aae"]


def _delegation(vc: dict) -> dict | None:
    d = _aae(vc)["mandate"].get("delegation")
    return d if isinstance(d, dict) else None


def _effective_depth(vc: dict) -> int:
    """Root AAE: 0. Delegated AAE: its delegation.depth (Section 3)."""
    d = _delegation(vc)
    return int(d["depth"]) if d else 0


def _effective_max_depth(vc: dict) -> int:
    """The parent-side ceiling a child's max_depth must not exceed (Section 3)."""
    d = _delegation(vc)
    if d:
        return int(d["max_depth"])
    policy = _aae(vc)["mandate"].get("delegation_policy")
    if not isinstance(policy, dict) or not isinstance(policy.get("max_depth"), int) \
            or isinstance(policy.get("max_depth"), bool) or policy["max_depth"] < 0:
        raise DelegationChainError(
            "parent is a root AAE without a delegation_policy.max_depth; it may not delegate")
    return int(policy["max_depth"])


def _parse_rfc3339(value, what: str) -> datetime:
    if not isinstance(value, str):
        raise DelegationChainError(f"{what} must be an RFC 3339 timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DelegationChainError(f"{what} is not a valid RFC 3339 timestamp")
    if parsed.tzinfo is None:
        raise DelegationChainError(f"{what} must carry a UTC offset")
    return parsed.astimezone(timezone.utc)


def _constraints_by_type(vc: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in _aae(vc)["constraints"]:
        if not isinstance(c, dict) or not isinstance(c.get("type"), str):
            raise DelegationChainError("every constraint must be an object with a string type")
        if c["type"] in out:
            raise DelegationChainError(f"constraint type {c['type']} appears more than once")
        out[c["type"]] = c
    return out


def _is_required(constraint: dict) -> bool:
    """Section 2.3: an absent `required` member is treated as required: true."""
    return constraint.get("required", True) is True


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DelegationChainError(f"{what} must be a number")
    return float(value)


def _assert_constraint_narrower(ctype: str, child: dict, parent: dict) -> None:
    """One constraint, compared per the element rules of Section 3."""
    if ctype in _NUMERIC_UPPER_BOUND:
        if ctype in _CURRENCY_VALUED and child.get("currency") != parent.get("currency"):
            # No configured conversion policy exists, so differing currencies are undecidable.
            raise DelegationChainError(
                f"constraint {ctype} changes currency under delegation")
        if _number(child.get("value"), f"{ctype}.value") > _number(
                parent.get("value"), f"parent {ctype}.value"):
            raise DelegationChainError(f"constraint {ctype} widens the numeric upper bound")
        return

    if ctype in _RATE_LIMIT:
        if child.get("window") != parent.get("window"):
            raise DelegationChainError(
                f"constraint {ctype} uses a different window than the parent; "
                "no profile defines cross-window comparison, so the AAE is rejected")
        if _number(child.get("value"), f"{ctype}.value") > _number(
                parent.get("value"), f"parent {ctype}.value"):
            raise DelegationChainError(f"constraint {ctype} raises the rate limit")
        return

    if ctype in _ALLOWLIST:
        cv, pv = child.get("value"), parent.get("value")
        if not isinstance(cv, list) or not isinstance(pv, list):
            raise DelegationChainError(f"constraint {ctype}.value must be an array")
        if not set(map(repr, cv)).issubset(set(map(repr, pv))):
            raise DelegationChainError(f"constraint {ctype} is not a subset of the parent allowlist")
        return

    # Section 3, closing rule: an element whose narrowing cannot be determined is a rejection.
    if {k: v for k, v in child.items() if k != "required"} != \
            {k: v for k, v in parent.items() if k != "required"}:
        raise DelegationChainError(
            f"constraint type {ctype} has no defined comparison and differs from the parent")


def assert_monotonic(child_vc: dict, parent_vc: dict) -> None:
    """Section 3: the delegated AAE MUST be equal to or more restrictive, per element."""
    child_aae, parent_aae = _aae(child_vc), _aae(parent_vc)

    # --- actions: subset ---
    child_actions, parent_actions = child_aae["mandate"].get("actions"), \
        parent_aae["mandate"].get("actions")
    if not isinstance(child_actions, list) or not isinstance(parent_actions, list):
        raise DelegationChainError("mandate.actions must be an array on both AAEs")
    if not set(map(str, child_actions)).issubset(set(map(str, parent_actions))):
        raise DelegationChainError("delegated mandate.actions is not a subset of the parent's")

    # --- constraints ---
    child_c, parent_c = _constraints_by_type(child_vc), _constraints_by_type(parent_vc)
    for ctype, pc in parent_c.items():
        if _is_required(pc):
            cc = child_c.get(ctype)
            if cc is None:
                raise DelegationChainError(
                    f"delegated AAE omits the parent's required constraint {ctype}")
            if not _is_required(cc):
                raise DelegationChainError(
                    f"delegated AAE downgrades the parent's required constraint {ctype}")
    for ctype, cc in child_c.items():
        pc = parent_c.get(ctype)
        if pc is not None:  # an added constraint only narrows and needs no comparison
            _assert_constraint_narrower(ctype, cc, pc)

    # --- validity nesting ---
    cv, pv = child_aae["validity"], parent_aae["validity"]
    if _parse_rfc3339(cv.get("not_before"), "validity.not_before") < \
            _parse_rfc3339(pv.get("not_before"), "parent validity.not_before"):
        raise DelegationChainError("delegated validity.not_before precedes the parent's")
    if _parse_rfc3339(cv.get("not_after"), "validity.not_after") > \
            _parse_rfc3339(pv.get("not_after"), "parent validity.not_after"):
        raise DelegationChainError("delegated validity.not_after outlasts the parent's")


def _assert_depth_rules(child_vc: dict, parent_vc: dict) -> int:
    """Section 3 depth rules for one link. Returns the child's max_depth."""
    d = _delegation(child_vc)
    depth, max_depth = d.get("depth"), d.get("max_depth")
    for name, value in (("depth", depth), ("max_depth", max_depth)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DelegationChainError(f"delegation.{name} must be a non-negative integer")
    if depth != _effective_depth(parent_vc) + 1:
        raise DelegationChainError(
            "delegation.depth must equal the parent's effective depth plus 1")
    if max_depth > _effective_max_depth(parent_vc):
        raise DelegationChainError(
            "delegation.max_depth exceeds the parent's effective maximum depth")
    if depth > max_depth:
        raise DelegationChainError("delegation.depth exceeds delegation.max_depth")
    return max_depth


def _assert_parent_hash(delegation: dict, parent_jws: str) -> None:
    """Section 3: sha-256 over the exact ASCII octets of the parent JWS, as retrieved."""
    declared = delegation.get("delegator_aae_hash")
    if declared is None:
        return
    if not isinstance(declared, str) or not declared.startswith(_HASH_PREFIX):
        raise DelegationChainError('delegator_aae_hash must have the form "sha-256:<base64url>"')
    digest = hashlib.sha256(parent_jws.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if declared[len(_HASH_PREFIX):] != expected:
        raise DelegationChainError("delegator_aae_hash does not match the supplied parent AAE")


def _assert_signing_authority(vc: dict, signing_did: str) -> None:
    """Step 1, signing authority, for the delegated case.

    Case (a) of the draft: the signing DID is `delegation.delegator_did` and the VC
    issuer is that same DID. Case (b) — a delegator's DID document authorizing another
    DID to issue on its behalf — has no representation in the DID methods this
    deployment resolves, so it is an authorization mechanism the relying party does not
    understand, and the draft requires rejection in that case.
    """
    d = _delegation(vc)
    if d is None:
        return
    delegator = d.get("delegator_did")
    if not isinstance(delegator, str) or not delegator:
        raise DelegationChainError("delegation.delegator_did is required and must be a string")
    if signing_did != delegator or vc.get("issuer") != delegator:
        raise DelegationChainError(
            "signing authority for a delegated AAE requires signing DID and issuer to equal "
            "delegation.delegator_did; delegated issuance on another DID's behalf is not "
            "an authorization mechanism this relying party understands")


async def verify_delegation_chain(
    vc: dict,
    *,
    aae_jws: str,
    ancestor_jws: list | None,
    conn,
    verify_core: Callable[..., Any],
    signing_did: str,
) -> dict | None:
    """Walk the chain of a delegated AAE over inline ancestors.

    `verify_core` runs §5 Step 1 + Step 2 over one compact JWS and returns the parsed
    VC together with its signing DID. Step 4 and Step 5 are deliberately not applied
    to ancestors.

    Returns None when the AAE carries no delegation. Otherwise returns the walk's
    result: the visited path, the chain length, the applied recursion limit and the
    effective validity window.
    """
    if _delegation(vc) is None:
        return None

    _assert_signing_authority(vc, signing_did)

    # --- index the inline material, before trusting any of it ---
    ancestor_jws = ancestor_jws or []
    if not isinstance(ancestor_jws, list):
        raise DelegationChainError("ancestor_jws must be an array of compact JWS strings")
    if len(ancestor_jws) > MAX_ANCESTORS:
        raise DelegationChainError(f"more than {MAX_ANCESTORS} inline ancestors supplied")
    by_id: dict[str, tuple[dict, str]] = {}
    for raw in ancestor_jws:
        parsed, anc_signing_did = await verify_core(raw, conn)
        _assert_signing_authority(parsed, anc_signing_did)
        if parsed["id"] in by_id:
            raise DelegationChainError(f"ancestor {parsed['id']} supplied more than once")
        by_id[parsed["id"]] = (parsed, raw)

    # --- walk ---
    visited = [vc["id"]]
    seen = {vc["id"]}
    current, current_jws = vc, aae_jws
    smallest_max_depth = MAX_RECURSION_LIMIT
    not_before = _parse_rfc3339(_aae(vc)["validity"].get("not_before"), "validity.not_before")
    not_after = _parse_rfc3339(_aae(vc)["validity"].get("not_after"), "validity.not_after")

    while (d := _delegation(current)) is not None:
        parent_id = d.get("delegator_aae_id")
        if not isinstance(parent_id, str) or not parent_id:
            raise DelegationChainError("delegation.delegator_aae_id is required")
        if parent_id in seen:
            raise DelegationChainError(f"delegation cycle: {parent_id} appears twice in the path")
        entry = by_id.get(parent_id)
        if entry is None:
            if isinstance(d.get("delegator_aae_uri"), str) and d["delegator_aae_uri"]:
                raise NotImplementedError(
                    "ancestor retrieval over delegator_aae_uri is deferred until the egress "
                    "proxy exists; supply the parent AAE inline as ancestor_jws")
            raise DelegationChainError(
                f"parent AAE {parent_id} was neither supplied inline nor named by a URI")
        parent, parent_jws = entry

        _assert_parent_hash(d, parent_jws)
        if parent["credentialSubject"]["id"] != d.get("delegator_did"):
            raise DelegationChainError(
                "parent credentialSubject.id does not equal the child's delegation.delegator_did")
        smallest_max_depth = min(smallest_max_depth, _assert_depth_rules(current, parent))
        assert_monotonic(current, parent)

        pv = _aae(parent)["validity"]
        not_before = max(not_before, _parse_rfc3339(pv.get("not_before"), "parent not_before"))
        not_after = min(not_after, _parse_rfc3339(pv.get("not_after"), "parent not_after"))

        seen.add(parent_id)
        visited.append(parent_id)
        current, current_jws = parent, parent_jws

        if len(visited) - 1 > MAX_RECURSION_LIMIT:
            raise DelegationChainError(
                f"delegation chain exceeds the maximum recursion limit of {MAX_RECURSION_LIMIT}")

    links = len(visited) - 1
    limit = min(MAX_RECURSION_LIMIT, smallest_max_depth)
    if links > limit:
        raise DelegationChainError(
            f"delegation chain of {links} links exceeds the applied recursion limit of {limit}")

    return {
        "path": visited,                       # presented AAE first, root last
        "chain_length": links,
        "recursion_limit": limit,
        "root_aae_id": visited[-1],
        "effective_not_before": not_before.isoformat().replace("+00:00", "Z"),
        "effective_not_after": not_after.isoformat().replace("+00:00", "Z"),
    }
