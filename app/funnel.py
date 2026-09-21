"""Acquisition funnel: registrations per day and what each agent did next.

The question this answers is not "how many agents exist" — the overview panel
has answered that since February. It is whether a registration turns into use:
an authenticated call, a credential, a payment. Those three are the only
evidence that a listing sent us someone real rather than a crawler that filled
in a form.

Three things about the data are worth stating here rather than discovering from
a surprising number later:

* ``platform`` is free-form. ``/identity/register-pop`` accepts any string of
  up to 32 alphanumerics, so the buckets below are a display mapping, not a
  constraint. Anything unmapped lands in ``other`` and the raw value is
  reported alongside it — a bucket that hides what it swallowed is worse than
  no bucket.
* First authenticated call resolves to a *day*, not a timestamp.
  ``usage_daily_keys`` is a daily rollup, so "time to first call" is measured
  in days and an agent that registers and calls within the hour scores 0.
* Payments carry a wallet, not a DID. ``payment_events.did`` is written only
  when the payer was already known to us; otherwise the sole link is
  ``agents.wallet_address``, which most agents never set. The endpoint reports
  how many payment rows it could attribute at all, so an empty payment column
  reads as missing linkage rather than as missing payments.
"""

import datetime as _dt

# 19.09.2026 is the day the first listing-sourced registration arrived. Counting
# from it keeps the funnel about acquisition rather than about the back catalogue.
FUNNEL_EPOCH = _dt.date(2026, 9, 19)

# The target Lars set: 100 registrations within 90 days of the epoch.
FUNNEL_GOAL = 100
FUNNEL_GOAL_DAYS = 90

# Registration issues an AgentTrustCredential on the spot: 86 of 90 land within
# a minute of the agent's own created_at. Counted as the first credential it
# would make that milestone a constant zero and measure nothing. Credentials
# inside this grace window are the automatic one and are not milestones; the
# first one after it is something the agent came back and asked for.
REGISTRATION_VC_GRACE_SECONDS = 60

# Raw platform string -> bucket. Deliberately literal: every key here is a value
# we either have seen or have asked a registry to send. No SDK aliases are
# guessed — no agent has ever registered as crewai or langchain, and inventing
# the mapping now would silently move future rows out of `other` on no evidence.
PLATFORM_BUCKETS = {
    # Skill and tool registries
    "clawhub": "clawhub",
    "hermes": "hermes",
    "smithery": "smithery",
    "glama": "glama",
    # Protocol and chain ecosystems
    "a2a": "a2a",
    "erc8004": "erc8004",
    "erc-8004": "erc8004",
    "rnwy": "rnwy",
    "virtuals-acp": "virtuals-acp",
    "virtuals": "virtuals-acp",
    "virtuals_acp": "virtuals-acp",
    "olas": "olas",
    # Task and payment markets
    "taskmarket": "taskmarket",
    "x402-bazaar": "x402-bazaar",
    "x402bazaar": "x402-bazaar",
    "bazaar": "x402-bazaar",
    # Framework integrations. Each ships as a package that sets its own value,
    # so a registration here is attributable to the package rather than to a
    # string the agent chose.
    "langchain": "langchain",
    "moltrust-langchain": "langchain",
    "crewai": "crewai",
    "moltrust-crewai": "crewai",
    "openai-agents": "openai-agents",
    "openai_agents": "openai-agents",
    "vercel-ai": "vercel-ai",
    "vercel": "vercel-ai",
    "ai-sdk": "vercel-ai",
    # Generic client SDK, for callers that use no framework
    "sdk": "sdk",
    # An agent that hit a gated endpoint, was denied, and followed the pointer
    # in the denial to register. The only pool where the denial itself is the
    # acquisition channel, which is why it is worth its own bucket rather than
    # landing in `other`: it measures whether gating brings agents in or only
    # turns them away.
    "gate": "gate",
    "moltrust-gate": "gate",
}

# Display order; `other` always last because it is the residue, not a category.
BUCKET_ORDER = [
    "clawhub", "hermes", "smithery", "glama",
    "a2a", "erc8004", "rnwy", "virtuals-acp", "olas",
    "taskmarket", "x402-bazaar", "gate", "partner",
    "langchain", "crewai", "openai-agents", "vercel-ai", "sdk",
    "other",
]

# Excluded from the acquisition goal without being ours. Ownify agents are a
# partner's own population, arriving through a commercial arrangement rather
# than through reach, so counting them towards 100 would measure the contract
# twice. They are not internal either — calling them that would misreport
# whose agents they are.
PARTNER_PLATFORMS = {"ownify"}
PARTNER_BUCKET = "partner"


def is_partner_platform(platform) -> bool:
    if not platform:
        return False
    return str(platform).strip().lower() in PARTNER_PLATFORMS


def canonical_platform(platform) -> str:
    """The value the registration path stores.

    Aliases collapse onto one spelling so a pool cannot be split across
    `virtuals`, `virtuals_acp` and `virtuals-acp` and read as three small
    pools. An unrecognised value is kept verbatim rather than rejected: a
    registration is worth more than a tidy taxonomy, and `bucket_of` already
    files anything unknown under `other` where it stays visible.
    """
    if not platform:
        return ""
    raw = str(platform).strip().lower()
    return PLATFORM_BUCKETS.get(raw, raw)

# Buckets we paid to fill. Registrations from a funded bounty are real agents
# and they are not organic reach: on 2026-09-20 ten USDC in escrow produced
# thirteen taskmarket registrations in under an hour, which would read as a
# breakout week if it sat in the same total as everyone who arrived on their
# own. Reported separately so the goal is measured against reach, not spend.
PAID_ACQUISITION_BUCKETS = {"taskmarket"}


def is_paid_acquisition(bucket: str) -> bool:
    return bucket in PAID_ACQUISITION_BUCKETS


def split_paid_and_organic(counts: dict[str, int], internal: int = 0) -> dict[str, int]:
    """Split a bucket->count mapping into paid, internal and organic.

    `internal` is passed in rather than read out of `counts`, because being
    ours is not a bucket — an internal registration still carries whatever
    platform string it was made with, and moving it into a bucket of its own
    would make the platform breakdown lie about where agents came from.

    Organic is what is left after both are removed. That is the number the
    goal is measured against, and the one that was wrong before: 36 of our own
    agents sat in it.
    """
    paid = sum(n for b, n in counts.items() if is_paid_acquisition(b))
    total = sum(counts.values())
    internal = max(0, int(internal or 0))
    return {
        "total": total,
        "paid": paid,
        "internal": internal,
        "organic": max(0, total - paid - internal),
    }

# --- Internal traffic -------------------------------------------------------
#
# Some registrations are ours. Counting them as reach flatters every number
# built on top: on 2026-09-20 the single largest origin cluster in the whole
# database — 36 agents across klaw, moltbook and ownify, spread over four
# months — turned out to be one /24 belonging to our own infrastructure. Read
# through the platform column alone it looked like three independent sources.
#
# Two signals, and either one is enough:
#
#   the operator's /24   the host those 36 registered from
#   the platform string  test and system registrations, plus moltrust-internal,
#                        which is reserved for this and not yet in use by any
#                        row — kept here so the value works the day someone
#                        starts sending it rather than silently landing in
#                        `other`.
#
# `moltrust` (6 rows) is deliberately NOT in this set. It is the platform the
# public registry examples use, so it is reachable by anyone reading the docs,
# and treating it as internal would quietly hide real registrations.

# Stored addresses are already truncated to /24 (_anonymize_ip zeroes the last
# octet), so the network address is what a row actually contains. Written as a
# prefix anyway: the comparison is on the first three octets, which keeps
# working if the storage format ever changes.
INTERNAL_IP_PREFIXES = ("57.129.23.",)

INTERNAL_PLATFORMS = {"test", "system", "moltrust-internal"}

INTERNAL_BUCKET = "internal"


def is_internal_platform(platform) -> bool:
    if not platform:
        return False
    return str(platform).strip().lower() in INTERNAL_PLATFORMS


def is_internal_ip(registration_ip) -> bool:
    if not registration_ip:
        return False
    ip = str(registration_ip).strip()
    return any(ip.startswith(prefix) for prefix in INTERNAL_IP_PREFIXES)


def is_internal(platform=None, registration_ip=None) -> bool:
    """Ours, by either signal."""
    return is_internal_platform(platform) or is_internal_ip(registration_ip)


def is_organic(platform=None, agent_type=None, registration_ip=None) -> bool:
    """True if this registration is evidence that reach worked.

    The per-row counterpart to `split_paid_and_organic`, which does the same
    job on an already-aggregated bucket mapping. Both answer the same question
    and neither carries its own idea of what paid, internal or partner means —
    the definitions live once, above.

    Excluded: what we paid for, what is ours, and a partner's own population.
    Those are the three ways a row can exist without anyone having found us.

    `agent_type == 'system'` is checked on top of the platform value because
    it is the authoritative marker of whose agent it is: a service agent of
    ours that registered under some other platform string is still ours, and
    `moltrust` is deliberately not an internal platform (see the note above
    INTERNAL_PLATFORMS) so the type is what catches those four rows.

    An unrecognised platform counts as organic, for the same reason
    `canonical_platform` keeps an unknown value instead of rejecting it. A pool
    nobody has classified yet is far more likely to be a stranger who found us
    than a category we forgot to exclude, and excluding by default would
    quietly shrink the number that decides whether the acquisition programme
    worked.
    """
    if str(agent_type or "").strip().lower() == "system":
        return False
    if is_paid_acquisition(canonical_platform(platform)):
        return False
    if is_partner_platform(platform):
        return False
    return not is_internal(platform, registration_ip)


def build_internal_function_sql() -> str:
    """Render the SQL predicate that mirrors :func:`is_internal`.

    Generated for the same reason the bucket function is: the endpoint, the
    panel and the nightly digest all ask this question, and three hand-written
    copies would drift the first time the operator host moves.
    """
    platform_list = ", ".join(_quote(p) for p in sorted(INTERNAL_PLATFORMS))
    ip_tests = "\n           OR ".join(
        f"coalesce(reg_ip, '') LIKE {_quote(prefix + '%')}"
        for prefix in INTERNAL_IP_PREFIXES
    )
    return (
        f"CREATE OR REPLACE FUNCTION {INTERNAL_FUNCTION_NAME}(p text, reg_ip text)\n"
        "RETURNS boolean\n"
        "LANGUAGE sql\n"
        "IMMUTABLE\n"
        "AS $$\n"
        f"    SELECT lower(trim(coalesce(p, ''))) IN ({platform_list})\n"
        f"           OR {ip_tests}\n"
        "$$;\n"
    )


OTHER_BUCKET = "other"

# One SQL function, used by both this endpoint and the nightly Telegram digest,
# so the two cannot drift into reporting different numbers for the same day.
# test_funnel.py asserts the checked-in migration is byte-identical to this.
BUCKET_FUNCTION_NAME = "funnel_platform_bucket"
INTERNAL_FUNCTION_NAME = "funnel_is_internal"


def bucket_of(platform) -> str:
    """Map a raw platform string to its display bucket.

    Partner platforms get their own bucket rather than falling into `other`.
    `PARTNER_BUCKET` existed as a constant and nothing used it, so Ownify
    registrations — a partner's own population, excluded from the goal by
    `is_organic` — were displayed as unclassified residue. A bucket that hides
    what it swallowed is worse than no bucket, and `other` was hiding the one
    origin we can name exactly.
    """
    if not platform:
        return OTHER_BUCKET
    raw = str(platform).strip().lower()
    if raw in PARTNER_PLATFORMS:
        return PARTNER_BUCKET
    return PLATFORM_BUCKETS.get(raw, OTHER_BUCKET)


def build_bucket_function_sql() -> str:
    """Render the SQL function that mirrors :func:`bucket_of`.

    Generated rather than hand-written so the mapping has exactly one source.
    """
    # Partner platforms first, so the SQL says the same thing bucket_of does.
    arms = "\n".join(
        [f"        WHEN {_quote(raw)} THEN {_quote(PARTNER_BUCKET)}"
         for raw in sorted(PARTNER_PLATFORMS)]
        + [f"        WHEN {_quote(raw)} THEN {_quote(bucket)}"
           for raw, bucket in PLATFORM_BUCKETS.items()]
    )
    return (
        f"CREATE OR REPLACE FUNCTION {BUCKET_FUNCTION_NAME}(p text)\n"
        "RETURNS text\n"
        "LANGUAGE sql\n"
        "IMMUTABLE\n"
        "AS $$\n"
        "    SELECT CASE lower(trim(coalesce(p, '')))\n"
        f"{arms}\n"
        f"        ELSE {_quote(OTHER_BUCKET)}\n"
        "    END\n"
        "$$;\n"
    )


def _quote(s: str) -> str:
    """Single-quote a literal for SQL. Inputs here are module constants."""
    return "'" + s.replace("'", "''") + "'"


def goal_progress(registrations: int, today: _dt.date) -> dict:
    """Progress against the 100-in-90-days target.

    ``days_elapsed`` counts the epoch itself as day 1, so the first day of the
    window is not reported as zero days spent.
    """
    days_elapsed = max(0, (today - FUNNEL_EPOCH).days + 1)
    days_left = max(0, FUNNEL_GOAL_DAYS - days_elapsed)
    remaining = max(0, FUNNEL_GOAL - registrations)
    return {
        "target": FUNNEL_GOAL,
        "window_days": FUNNEL_GOAL_DAYS,
        "start": FUNNEL_EPOCH.isoformat(),
        "deadline": (FUNNEL_EPOCH + _dt.timedelta(days=FUNNEL_GOAL_DAYS - 1)).isoformat(),
        "registrations": registrations,
        "percent": round(100.0 * registrations / FUNNEL_GOAL, 1),
        "days_elapsed": days_elapsed,
        "days_left": days_left,
        # Where a straight line to the target would stand today. Shown as a
        # marker on the bar so "12 of 100" reads against elapsed time rather
        # than against nothing.
        "on_pace": round(FUNNEL_GOAL * min(days_elapsed, FUNNEL_GOAL_DAYS) / FUNNEL_GOAL_DAYS, 1),
        # What the rest of the window would have to deliver per day. None once
        # the window has closed, because the answer is then no longer a rate.
        "required_per_day": round(remaining / days_left, 2) if days_left and remaining else (
            0.0 if not remaining else None
        ),
    }


def telegram_line(total_7d: int, by_bucket: list) -> str:
    """The one-line funnel summary for the nightly digest.

    ``by_bucket`` is an iterable of (bucket, count), already filtered to the
    7-day window.
    """
    parts = [f"{bucket} {count}" for bucket, count in by_bucket if count]
    if not parts:
        return f"Funnel (7d): +{total_7d} registrations"
    return f"Funnel (7d): +{total_7d} registrations by " + ", ".join(parts)

def pools_telegram_line(days: int, by_pool: list) -> str:
    """One line per digest, named pools separated by a middle dot.

    Ordered by size rather than by the display order, because the digest is
    read to see what moved, and what moved is usually what is largest.
    """
    parts = [f"{pool} {n}" for pool, n in sorted(by_pool, key=lambda x: -x[1]) if n]
    total = sum(n for _, n in by_pool)
    if not parts:
        return f"Pools ({days}d): keine Registrierungen"
    return f"Pools ({days}d): {total} — " + " · ".join(parts)

