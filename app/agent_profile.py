"""What we know about an agent beyond the row it registered with.

Two kinds of fact live here and they are kept apart. `declared_*` is what the
agent said about itself — optional, unverified, and an agent can claim any
framework it likes. Everything else is observed from request_log and from chain.
The distinction is the whole point: a cluster that mixes them turns a claim into
a measurement, and the interesting question is precisely where the two disagree.

Enrichment is best-effort by design. An agent that has never made a request has
nothing to observe, and saying so is more useful than a row of nulls that reads
like a failed lookup. `observed_from` and `observed_rows` carry that difference.
"""
from __future__ import annotations

import datetime as _dt
import re

# Agents whose only traffic is the registration call itself have nothing to
# cluster on. Kept as a named constant because it is a judgement, not a fact.
MIN_ROWS_FOR_ENDPOINTS = 1

# ASN organisation strings are free text from the geo lookup. These map the ones
# that actually occur to a provider name; anything else stays None rather than
# being guessed into a bucket, because a wrong cloud attribution is worse than
# an empty one when the table is used to spot Sybil clusters.
_CLOUD_PATTERNS: list[tuple[str, str]] = [
    (r"amazon|aws|ec2", "aws"),
    (r"google|gcp|goog", "gcp"),
    (r"microsoft|azure", "azure"),
    (r"hetzner", "hetzner"),
    (r"digitalocean|digital ocean", "digitalocean"),
    (r"ovh", "ovh"),
    (r"linode|akamai", "akamai"),
    (r"cloudflare", "cloudflare"),
    (r"oracle", "oracle"),
    (r"alibaba|aliyun", "alibaba"),
    (r"vultr|choopa", "vultr"),
    (r"scaleway|online s\.a\.s", "scaleway"),
    (r"fly\.io", "fly"),
    (r"railway", "railway"),
    (r"render", "render"),
]

# User-Agent to framework. Ordered: the first match wins, so the more specific
# patterns come first. A bare "python-requests" is a library and not a framework,
# and is reported as such instead of being left blank — "we saw a plain HTTP
# client" is a finding.
_UA_PATTERNS: list[tuple[str, str]] = [
    (r"langgraph", "langgraph"),
    (r"langchain", "langchain"),
    (r"crewai", "crewai"),
    (r"autogen", "autogen"),
    (r"llamaindex|llama_index", "llamaindex"),
    (r"semantic-kernel|semantickernel", "semantic-kernel"),
    (r"openai-agents|agents-sdk", "openai-agents"),
    (r"anthropic|claude", "anthropic-sdk"),
    (r"moltrust", "moltrust-sdk"),
    (r"\bmcp\b|model-?context-?protocol", "mcp"),
    (r"n8n", "n8n"),
    (r"eliza", "eliza"),
    (r"virtuals|acp-", "virtuals-acp"),
    (r"node-fetch|undici|axios", "node-http"),
    (r"python-requests|httpx|aiohttp|urllib", "python-http"),
    (r"curl|wget", "shell-http"),
    (r"go-http-client", "go-http"),
]


def classify_cloud(ip_org: str | None) -> str | None:
    if not ip_org:
        return None
    low = ip_org.lower()
    for pattern, name in _CLOUD_PATTERNS:
        if re.search(pattern, low):
            return name
    return None


def classify_ua(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    low = user_agent.lower()
    for pattern, name in _UA_PATTERNS:
        if re.search(pattern, low):
            return name
    return None


def wallet_age_days(bound_at, now: _dt.datetime | None = None) -> int | None:
    """Days since the wallet was bound to the DID.

    This is the age of the *binding*, not of the wallet on chain. The on-chain
    age needs a first-transaction lookup per address and is a separate job; this
    one is free and already answers "did the wallet appear with the agent or
    long before it".
    """
    if bound_at is None:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if bound_at.tzinfo is None:
        bound_at = bound_at.replace(tzinfo=_dt.timezone.utc)
    return max(0, (now - bound_at).days)


# The observation query. Two keys, tried in order, and which one answered is
# recorded — they are not equally good evidence.
#
#   did  the request carried the agent's DID. Unambiguous.
#   ip   the request came from the /24 the agent registered from. Weaker: a NAT,
#        a shared host or a cloud egress range puts several agents behind one
#        value, so a cluster built on it says "same origin", never "same agent".
_OBSERVE_BY_DID = """
    SELECT ip_org, ip_country, user_agent, caller_framework, endpoint, ts
    FROM request_log
    WHERE agent_did = $1
    ORDER BY ts ASC
"""

_OBSERVE_BY_IP = """
    SELECT ip_org, ip_country, user_agent, caller_framework, endpoint, ts
    FROM request_log
    WHERE ip = $1 AND ip IS NOT NULL
    ORDER BY ts ASC
"""


def _mode(values: list[str | None]) -> str | None:
    """Most frequent non-null value, or None."""
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def summarise_rows(rows, registered_at) -> dict:
    """Turn request_log rows into the observed half of a profile.

    `first_endpoints_24h` is the set of endpoints seen in the first 24 hours
    after registration, in the order they were first hit. What an agent reaches
    for first says more about what it is than the total it eventually calls,
    which is dominated by whatever it polls.
    """
    if not rows:
        return {
            "asn": None, "country": None, "cloud_provider": None,
            "ua_framework": None, "first_endpoints_24h": [], "observed_rows": 0,
        }

    orgs = [r["ip_org"] for r in rows]
    countries = [r["ip_country"] for r in rows]
    asn = _mode(orgs)
    country = _mode(countries)

    # A declared caller_framework beats a guess from the User-Agent, because the
    # client stated it on purpose. The UA is the fallback.
    declared_fw = _mode([r["caller_framework"] for r in rows])
    ua_framework = declared_fw or _mode([classify_ua(r["user_agent"]) for r in rows])

    endpoints: list[str] = []
    if registered_at is not None:
        cutoff = registered_at
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=_dt.timezone.utc)
        window_end = cutoff + _dt.timedelta(hours=24)
        for r in rows:
            ts = r["ts"]
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_dt.timezone.utc)
            if cutoff <= ts <= window_end:
                ep = r["endpoint"]
                if ep and ep not in endpoints:
                    endpoints.append(ep)

    return {
        "asn": asn,
        "country": country,
        "cloud_provider": classify_cloud(asn),
        "ua_framework": ua_framework,
        "first_endpoints_24h": endpoints[:20],
        "observed_rows": len(rows),
    }


async def enrich_agent(conn, did: str) -> dict | None:
    """Compute and store one agent's observed profile. Returns what was written.

    Never raises on a thin agent: an agent with no traffic gets a row with
    `observed_from = 'none'`, which is a fact about the agent rather than a
    failure of the job.
    """
    agent = await conn.fetchrow(
        """SELECT did, created_at, registration_ip, wallet_address, wallet_bound_at,
                  erc8004_agent_id, platform
           FROM agents WHERE did = $1""",
        did,
    )
    if agent is None:
        return None

    rows = await conn.fetch(_OBSERVE_BY_DID, did)
    observed_from = "did"
    if not rows and agent["registration_ip"]:
        rows = await conn.fetch(_OBSERVE_BY_IP, agent["registration_ip"])
        observed_from = "registration_ip"
    if not rows:
        observed_from = "none"

    summary = summarise_rows(rows, agent["created_at"])

    # taskmarket_tasks stays NULL. Submissions live in the contract's history
    # and the first-party CLI exposes no way to read their bodies, so a
    # submission cannot be matched to the DID that claims it without an indexer
    # — scripts/taskmarket_measure.py documents the same gap. A count of
    # "agents registered on platform=taskmarket" would fill the column with a
    # number that answers a different question, which is worse than a null.
    await conn.execute(
        """
        INSERT INTO agent_profile (
            did, asn, country, cloud_provider, ua_framework, first_endpoints_24h,
            wallet_age_days, observed_from, observed_rows, enriched_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now())
        ON CONFLICT (did) DO UPDATE SET
            asn = EXCLUDED.asn,
            country = EXCLUDED.country,
            cloud_provider = EXCLUDED.cloud_provider,
            ua_framework = EXCLUDED.ua_framework,
            first_endpoints_24h = EXCLUDED.first_endpoints_24h,
            wallet_age_days = EXCLUDED.wallet_age_days,
            observed_from = EXCLUDED.observed_from,
            observed_rows = EXCLUDED.observed_rows,
            enriched_at = now()
        """,
        did, summary["asn"], summary["country"], summary["cloud_provider"],
        summary["ua_framework"], summary["first_endpoints_24h"],
        wallet_age_days(agent["wallet_bound_at"]),
        observed_from, summary["observed_rows"],
    )
    return {**summary, "did": did, "observed_from": observed_from}


# erc8004_skills and a2a_skills are left untouched here. Both need a network
# fetch — the ERC-8004 tokenURI and the agent's own agent card — and a
# registration handler is the wrong place to wait on a third-party HTTP call.
# scripts/enrich_agent_profiles.py fills them in its --skills pass.


async def store_declared(conn, did: str, *, capabilities=None, description=None,
                         agent_card_url=None, framework=None) -> None:
    """Persist what the agent said about itself at registration.

    Written on its own rather than folded into enrich_agent, because the two
    have different lifetimes: the declaration is set once and never recomputed,
    the observation is recomputed daily. A single upsert would let the nightly
    job overwrite a declaration with NULL.
    """
    if capabilities is None and description is None and agent_card_url is None and framework is None:
        return
    await conn.execute(
        """
        INSERT INTO agent_profile (did, declared_capabilities, declared_description,
                                   agent_card_url, declared_framework)
        VALUES ($1,$2,$3,$4,$5)
        ON CONFLICT (did) DO UPDATE SET
            declared_capabilities = COALESCE(EXCLUDED.declared_capabilities, agent_profile.declared_capabilities),
            declared_description  = COALESCE(EXCLUDED.declared_description,  agent_profile.declared_description),
            agent_card_url        = COALESCE(EXCLUDED.agent_card_url,        agent_profile.agent_card_url),
            declared_framework    = COALESCE(EXCLUDED.declared_framework,    agent_profile.declared_framework)
        """,
        did, capabilities, description, agent_card_url, framework,
    )


async def enrich_in_background(db_pool, did: str) -> None:
    """Fire-and-forget enrichment right after registration.

    A freshly registered agent has almost nothing to observe — usually the one
    request that created it — so this is not where the profile gets good. It is
    here so that origin and framework are on record from minute one, before the
    /24 it registered from starts serving somebody else. The nightly pass is
    what fills the rest in.

    Swallows everything: a registration must not fail because a side table did.
    """
    if not db_pool:
        return
    try:
        async with db_pool.acquire() as conn:
            await enrich_agent(conn, did)
    except Exception as exc:  # noqa: BLE001 - see docstring
        import logging
        logging.getLogger(__name__).warning("agent_profile enrichment failed for %s: %s", did, exc)
