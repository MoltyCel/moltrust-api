"""Tool authorization matrix per docs/auto-probe-token-spec.md §6.

Each entry maps an MCP tool name to the minimum identity tier required:

  - "any":     no auth required (public reads)
  - "probe":   any resolved identity, including auto-minted probes
  - "claimed": permanent claimed identity (no probes); money / cross-agent
               VC issuance / production trust-graph writes

The dispatch-level middleware (app.mcp_auth_middleware) looks each tool
up here on tools/call. Tools not listed default to "claimed" — fail-closed,
so adding a new tool without a matrix entry rejects every probe and
forces a deliberate decision.

Spec notes:
- moltrust_credits and *_issue_vc tools are wired "probe" here. Their
  state-changing REST endpoints (/credits/transfer, /credits/deposit,
  the VC-issue routes) carry their own require_claimed gate in main.py,
  so the dispatch layer stays permissive for the balance/read paths and
  the REST layer rejects the write paths. This avoids breaking probe
  balance checks while keeping the money-mover gate enforced.
"""

TOOL_AUTH_MATRIX: dict[str, str] = {
    # §6.1 — any
    "moltrust_stats": "any",
    "moltguard_market": "any",
    "moltguard_feed": "any",
    "moltrust_identity": "any",  # probe_mcp_tools — must work pre-mint

    # §6.2 — probe (default for most tools)
    "moltrust_register": "probe",
    "moltrust_verify": "probe",
    "moltrust_reputation": "probe",
    "moltrust_rate": "probe",
    "moltrust_credential": "probe",
    "moltrust_credits": "probe",        # REST /credits/transfer gates writes
    "moltrust_deposit_info": "probe",
    "moltrust_deposit_history": "probe",
    "moltrust_erc8004": "probe",

    "moltguard_score": "probe",
    "moltguard_detail": "probe",
    "moltguard_sybil": "probe",
    "moltguard_credential_verify": "probe",
    "moltguard_credential_issue": "probe",  # self-VC only by REST

    "mt_shopping_info": "probe",
    "mt_shopping_verify": "probe",
    "mt_travel_info": "probe",
    "mt_travel_verify": "probe",
    "mt_skill_audit": "probe",
    "mt_skill_verify": "probe",
    "mt_prediction_link": "probe",
    "mt_prediction_wallet": "probe",
    "mt_prediction_leaderboard": "probe",
    "mt_salesguard_register": "probe",
    "mt_salesguard_verify": "probe",
    "mt_salesguard_reseller": "probe",
    "mt_fantasy_commit": "probe",
    "mt_fantasy_verify": "probe",
    "mt_fantasy_history": "probe",
    "mt_endorse_agent": "probe",
    "mt_create_interaction_proof": "probe",
    "mt_get_trust_score": "probe",

    # §6.3 — claimed (money / cross-agent VC issuance / on-chain writes)
    "moltrust_claim_deposit": "claimed",
    "mt_shopping_issue_vc": "claimed",
    "mt_travel_issue_vc": "claimed",
    "mt_skill_issue_vc": "claimed",
}

# Allowed identity tiers, ordered (any < probe < claimed).
_TIER_RANK: dict[str, int] = {"any": 0, "probe": 1, "claimed": 2}

# Default for tools that aren't explicitly listed — fail-closed.
DEFAULT_REQUIREMENT = "claimed"


def required_tier(tool_name: str) -> str:
    """Return the minimum identity tier required to call this tool."""
    return TOOL_AUTH_MATRIX.get(tool_name, DEFAULT_REQUIREMENT)


def identity_satisfies(identity_kind: str | None, required: str) -> bool:
    """Check whether a resolved identity.kind meets the required tier.

    identity_kind values produced by app.identity.resolve_identity:
      - None        — no identity resolved (DB unavailable / skip path)
      - "probe-new" — auto-minted on this request
      - "probe"     — existing unclaimed probe
      - "claimed"   — permanent claimed identity
      - "api-key"   — legacy API key path (treated as claimed)
    """
    if required == "any":
        return True
    if identity_kind is None:
        return False
    # Normalize identity_kind to a tier.
    if identity_kind in ("claimed", "api-key"):
        tier = "claimed"
    elif identity_kind in ("probe", "probe-new"):
        tier = "probe"
    else:
        # Unknown kind — fail closed.
        return False
    return _TIER_RANK[tier] >= _TIER_RANK[required]
