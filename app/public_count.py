"""The public agent figures, derived from the registry export and nothing else.

This used to run its own SQL with its own bucket list. That was the second
generation path, and on 2026-09-27 the two disagreed: this one excluded
platforms by name while the export included them, and the file that went live
listed fifty partner DIDs individually. There is now one query
(`app/sql/registry_export.sql`), one bucket definition, and one snapshot; this
module only reshapes what `app.registry_export` already built.

Anything that wants the rows themselves calls `registry_export.build` directly.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from app import registry_export
from app.registry_export import IncompleteRead  # re-exported for callers

__all__ = ["read", "IncompleteRead"]


def read(as_of: str | None = None) -> dict:
    """Headline figures plus the per-day series, at one snapshot.

    Raises IncompleteRead when the export cannot show that its buckets account
    for every row it read.
    """
    doc = registry_export.build(as_of)
    totals, by_bucket = doc["totals"], doc["by_bucket"]

    counted = registry_export.COUNTED
    per_day = Counter()
    unmeasured = 0
    for row in doc["rows"]:
        if row["bucket"] not in counted:
            continue
        if row["kind"] == "aggregate":
            # The aggregate row carries no registration dates by design, so the
            # series below is the itemised buckets only. It feeds a rate, not a
            # headline, and partner registrations do not arrive in bursts.
            continue
        per_day[row["registered"]] += 1
        unmeasured += 1 if row["before_telemetry_cutoff"] else 0

    cutoff = (date.fromisoformat(doc["as_of"][:10]) - timedelta(days=7)).isoformat()
    recent = sum(n for day, n in per_day.items() if day >= cutoff)

    return {
        "counts": {
            "public": totals["registered_with_anchor"],
            "activated": totals["activated_counted"],
            "unmeasured": unmeasured,
            "deducted": by_bucket.get("own_test", {}).get("dids", 0),
            "anchors": totals["anchors"],
            "roots": totals["roots"],
        },
        "buckets": [
            {"bucket": b, "dids": v["dids"], "anchors": v["anchors"],
             "activated": v["activated"], "counted": b in counted}
            for b, v in sorted(by_bucket.items())
        ],
        "per_day": [{"day": d, "registered": n} for d, n in sorted(per_day.items())][-14:],
        "rate7": round(recent / 7.0, 2),
        "as_of": doc["as_of"],
        "generated_at": doc["generated_at"],
    }


def _today() -> str:
    return datetime.now().date().isoformat()
