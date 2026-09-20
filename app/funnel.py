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
    "clawhub": "clawhub",
    "taskmarket": "taskmarket",
    "smithery": "smithery",
    "a2a": "a2a",
    "erc8004": "erc8004",
    "erc-8004": "erc8004",
    "sdk": "sdk",
}

# Display order; `other` always last because it is the residue, not a category.
BUCKET_ORDER = ["clawhub", "taskmarket", "smithery", "a2a", "erc8004", "sdk", "other"]

# Buckets we paid to fill. Registrations from a funded bounty are real agents
# and they are not organic reach: on 2026-09-20 ten USDC in escrow produced
# thirteen taskmarket registrations in under an hour, which would read as a
# breakout week if it sat in the same total as everyone who arrived on their
# own. Reported separately so the goal is measured against reach, not spend.
PAID_ACQUISITION_BUCKETS = {"taskmarket"}


def is_paid_acquisition(bucket: str) -> bool:
    return bucket in PAID_ACQUISITION_BUCKETS


def split_paid_and_organic(counts: dict[str, int]) -> dict[str, int]:
    """Split a bucket->count mapping into its paid and organic halves."""
    paid = sum(n for b, n in counts.items() if is_paid_acquisition(b))
    total = sum(counts.values())
    return {"total": total, "paid": paid, "organic": total - paid}

OTHER_BUCKET = "other"

# One SQL function, used by both this endpoint and the nightly Telegram digest,
# so the two cannot drift into reporting different numbers for the same day.
# test_funnel.py asserts the checked-in migration is byte-identical to this.
BUCKET_FUNCTION_NAME = "funnel_platform_bucket"


def bucket_of(platform) -> str:
    """Map a raw platform string to its display bucket."""
    if not platform:
        return OTHER_BUCKET
    return PLATFORM_BUCKETS.get(str(platform).strip().lower(), OTHER_BUCKET)


def build_bucket_function_sql() -> str:
    """Render the SQL function that mirrors :func:`bucket_of`.

    Generated rather than hand-written so the mapping has exactly one source.
    """
    arms = "\n".join(
        f"        WHEN {_quote(raw)} THEN {_quote(bucket)}"
        for raw, bucket in PLATFORM_BUCKETS.items()
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
