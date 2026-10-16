"""The track-record score: how long the record has stood, and whether it is still used.

The track-record credential (app/track_record.py) says a wallet existed and
acted before the agent asked for a discount. It says nothing about what happened
afterwards, so a record issued in April and a record issued this morning read
the same at a gate.

Two curves fix that, and both are numbers here rather than configuration,
for the reason the thresholds in app/track_record.py are: a figure an operator
can move quietly is not a figure a relying party can rely on. Anyone holding
`days_since_first_anchor` and `days_since_independent_call` recomputes the score
without asking us.

Tenure counts from the first anchoring transaction, not from the registration
row. A registration is a line in our database; the anchor is a transaction on
Base that was paid for and cannot be backdated.

Dormancy counts from the last call to an endpoint no bounty task text
prescribed — the same list app/sql/registry_export.sql uses for
`activated_counted`, so "active" means one thing across the registry proof and
this score.
"""
from __future__ import annotations

import pathlib
import re
from typing import Optional

#: Days of tenure at which the tenure part reaches its maximum. A year, because
#: a credential runs a year: an agent that has renewed once has been around for
#: one full validity window, and that is the longest span the registry can show
#: about anyone today.
TENURE_SATURATION_DAYS = 365

#: What tenure is worth at saturation. Twenty points on a hundred-point scale,
#: the same range app/swarm/trust_score.py works in, so the two figures can sit
#: beside each other without conversion.
TENURE_MAX_POINTS = 20.0

#: Dormancy below this leaves the score untouched. Thirty days is the window the
#: strategy of 2026-10-16 measures returns in, and it is long enough that an
#: agent calling monthly never sees a fall.
DORMANT_AFTER_DAYS = 30

#: Dormancy at and above this leaves nothing. Ninety days of fall after the
#: grace period: an agent that stops is at zero after a quarter, which is the
#: span over which 241 of 525 registered agents made no second call at all.
DORMANT_ZERO_DAYS = 120

#: request_log keeps thirty days. Past that there is no record of an independent
#: call, and absence of a row is not a date — see `score` for what is done with
#: that and what is published alongside the figure.
REQUEST_LOG_RETENTION_DAYS = 30

_SQL_PATH = pathlib.Path(__file__).with_name("sql") / "registry_export.sql"


def scripted_endpoint_patterns() -> list[str]:
    """The endpoints a bounty task text prescribed, read from the counting query.

    Kept in one place rather than two. The list lives in
    app/sql/registry_export.sql because that is where `activated_counted` is
    computed; reading it from there means a pattern added for the registry proof
    reaches this score in the same commit.
    """
    text = _SQL_PATH.read_text(encoding="utf-8")
    block = re.search(r"scripted\(pats\)\s+AS\s+\(VALUES\s+\(ARRAY\[(.*?)\]\)\)",
                      text, re.S)
    if block is None:
        raise RuntimeError(
            "app/sql/registry_export.sql no longer declares scripted(pats); "
            "the score cannot tell a prescribed endpoint from an independent one"
        )
    return re.findall(r"'([^']+)'", block.group(1))


def is_independent(endpoint: str) -> bool:
    """Was this call the agent's own idea, by the registry's counting rule?"""
    for pattern in scripted_endpoint_patterns():
        regex = "^" + "".join(".*" if ch == "%" else re.escape(ch) for ch in pattern) + "$"
        if re.match(regex, endpoint):
            return False
    return True


def tenure_points(days_since_first_anchor: Optional[float]) -> float:
    """The growing curve: linear from 0 to TENURE_MAX_POINTS over a year.

    Linear and not a saturating curve. A relying party has to be able to redo
    the arithmetic in its head from two published numbers, and `days / 365 × 20`
    is that; a logarithm would buy a shape nobody asked for at the cost of a
    figure nobody checks.
    """
    if days_since_first_anchor is None or days_since_first_anchor <= 0:
        return 0.0
    share = min(1.0, days_since_first_anchor / TENURE_SATURATION_DAYS)
    return round(TENURE_MAX_POINTS * share, 2)


def freshness_factor(days_since_independent_call: Optional[float]) -> float:
    """The falling curve: 1.0 for thirty days, then linear to 0.0 at a hundred and twenty.

    `None` means no independent call is on record at all, and that is the floor,
    not a gap: an agent nobody can show calling is an agent whose record has
    nothing current behind it.
    """
    if days_since_independent_call is None:
        return 0.0
    if days_since_independent_call <= DORMANT_AFTER_DAYS:
        return 1.0
    if days_since_independent_call >= DORMANT_ZERO_DAYS:
        return 0.0
    span = DORMANT_ZERO_DAYS - DORMANT_AFTER_DAYS
    return round((DORMANT_ZERO_DAYS - days_since_independent_call) / span, 4)


def score(days_since_first_anchor: Optional[float],
          days_since_independent_call: Optional[float]) -> float:
    """Tenure, reduced by dormancy. Zero to TENURE_MAX_POINTS."""
    return round(tenure_points(days_since_first_anchor)
                 * freshness_factor(days_since_independent_call), 2)


def curve() -> dict:
    """The four numbers the score is computed from, for publication beside it."""
    return {
        "tenure_saturation_days": TENURE_SATURATION_DAYS,
        "tenure_max_points": TENURE_MAX_POINTS,
        "dormant_after_days": DORMANT_AFTER_DAYS,
        "dormant_zero_days": DORMANT_ZERO_DAYS,
        "formula": "min(1, tenure_days / tenure_saturation_days) * tenure_max_points "
                   "* clamp01((dormant_zero_days - dormant_days) / "
                   "(dormant_zero_days - dormant_after_days))",
    }


async def for_did(conn, did: str) -> Optional[dict]:
    """The score for a DID, or None when it holds no anchored credential.

    None rather than zero. An agent with no anchor has no track record, and a
    zero would read as a record that decayed.

    `independent_call_seen` is published with the figure because of what
    request_log keeps. Thirty days of rows is the whole evidence; where there is
    no row, dormancy is measured from the first anchor, which is the longest it
    could be. The figure is then a lower bound, and the flag says so rather than
    leaving a reader to find it out.
    """
    first_anchor = await conn.fetchval(
        "SELECT min(a.anchored_at) FROM credential_anchors a "
        "JOIN credentials c ON c.id = a.credential_id "
        "WHERE c.subject_did = $1",
        did,
    )
    if first_anchor is None:
        return None

    patterns = scripted_endpoint_patterns()
    last_call = await conn.fetchval(
        "SELECT max(ts) FROM request_log "
        "WHERE agent_did = $1 AND NOT (endpoint LIKE ANY ($2::text[]))",
        did, patterns,
    )

    tenure_days = await conn.fetchval(
        "SELECT EXTRACT(EPOCH FROM (now() - $1::timestamptz)) / 86400", first_anchor)
    tenure_days = float(tenure_days) if tenure_days is not None else None

    if last_call is None:
        dormant_days = tenure_days
        seen = False
    else:
        dormant_days = float(await conn.fetchval(
            "SELECT EXTRACT(EPOCH FROM (now() - $1::timestamptz)) / 86400", last_call))
        seen = True

    return {
        "score": score(tenure_days, dormant_days),
        "max": TENURE_MAX_POINTS,
        "first_anchor_at": first_anchor.isoformat(),
        "days_since_first_anchor": round(tenure_days, 2) if tenure_days is not None else None,
        "days_since_independent_call": round(dormant_days, 2) if dormant_days is not None else None,
        "independent_call_seen": seen,
        "request_log_retention_days": REQUEST_LOG_RETENTION_DAYS,
        "curve": curve(),
    }
