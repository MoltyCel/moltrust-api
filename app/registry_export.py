"""Build the registry-proof document. One query, one bucket definition, one cut.

Everything that states a public agent figure reads this module: the proof page,
the milestone trigger, and whatever reports it next. There is no second query
and no second bucket list, because there were two of those on 2026-09-27 and the
one nobody was watching published fifty partner DIDs.

Three properties the shape enforces rather than documents:

**The platform universe is an allow-list.** `app/sql/registry_export.sql` names
every platform that produces a row. A platform absent from those four lists
produces nothing — no row, no count, no residue. An exclusion that has to be
written as a name in a `NOT IN` is an exclusion that the next person adding a
bucket forgets.

**Partners are one aggregate row carrying no DID.** Its anchors travel with it,
so the subtotal stays recomputable: every leaf still replays to its root and
every root still stands in a Base transaction. What a reader cannot do is say
which partner, or which agent.

**One cut for all three sources.** Registrations, anchors and the activation
window stop at the same instant. Pinning only the anchors leaves the activation
figure drifting while the rows stand still, which on 2026-09-26 was the
difference between 29 and 30.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

SQL_PATH = os.path.join(os.path.dirname(__file__), "sql", "registry_export.sql")
SCHEMA = "moltrust/registry-proof/v1"
CALLDATA_PREFIX = "MolTrust/VC/v1"

# The buckets that count toward the public figure. `own_test` is carried in the
# file and excluded from every headline.
COUNTED = ("bounty", "partner", "organic")
AGGREGATED = ("partner",)

PARTNER_NOTE = (
    "One row for all partner agents. No DIDs, by agreement. The anchors below "
    "are the full set for this row, so the subtotal stays recomputable: every "
    "leaf replays to its root and every root stands in a Base transaction."
)
AS_OF_NOTE = (
    "Snapshot boundary for registrations, anchors and the activation window "
    "alike. Without it the activation figure drifts while the rows stand still."
)
NOT_RECOMPUTABLE_NOTE = (
    "The activated column comes from our request log. It has no anchor, and a "
    "reader is taking our word for it. Everything else on this page can be "
    "checked against the chain without asking us."
)


class IncompleteRead(RuntimeError):
    """The export ran but could not prove it saw a coherent set."""


def _psql(as_of: str, sql_path: str | None = None, timeout: int = 300) -> list[dict]:
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-t", "-P", "pager=off",
         "-v", f"AS_OF={as_of}", "-f", os.path.abspath(sql_path or SQL_PATH)],
        capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError(f"psql: {out.stderr[:400]}")
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def _oldest_per_type(anchors):
    """One anchor per credential type: the earliest issued.

    Between 1 and 3 October 2026 three agents polled /credentials/track-record
    instead of the trust score and were issued one credential per call — 28, 27
    and 20. Every one is validly issued, validly anchored and not revoked. What
    was wrong was the count, and the endpoint no longer mints a fresh credential
    for an unchanged measurement.

    The file keeps the first of each type. A reader counting rows should be
    counting agents and the kinds of claim they hold, not how often somebody's
    retry loop ran. The duplicates stay on chain, because they are true.
    """
    oldest = {}
    for a in sorted(anchors, key=lambda x: (x.get("issued_at") or "",
                                            x.get("credential_id") or 0)):
        oldest.setdefault(a.get("credential_type"), a)
    return list(oldest.values())


def build(as_of: str | None = None, sql_path: str | None = None) -> dict:
    """Run the query at `as_of` and return the v1 document."""
    as_of = as_of or datetime.now(timezone.utc).replace(
        second=0, microsecond=0).isoformat()
    raw = _psql(as_of, sql_path)

    rows, totals, by_bucket = [], {}, {}
    agg_anchors: dict[str, list] = {b: [] for b in AGGREGATED}
    agg_dids = {b: 0 for b in AGGREGATED}
    agg_activated = {b: 0 for b in AGGREGATED}

    withheld = 0
    for r in raw:
        bucket = r["bucket"]
        anchors = r.get("anchors") or []
        kept = _oldest_per_type(anchors)
        withheld += len(anchors) - len(kept)
        anchors = kept
        slot = by_bucket.setdefault(bucket, {"dids": 0, "anchors": 0, "activated": 0})
        slot["dids"] += 1
        slot["anchors"] += len(anchors)
        slot["activated"] += 1 if r.get("activated") else 0

        if bucket in AGGREGATED:
            agg_dids[bucket] += 1
            agg_activated[bucket] += 1 if r.get("activated") else 0
            agg_anchors[bucket].extend(anchors)
            continue

        rows.append({
            "kind": "agent",
            "bucket": bucket,
            "did": r["did"],
            "registered": r["registered"],
            "before_telemetry_cutoff": r["before_telemetry_cutoff"],
            "activated": bool(r.get("activated")),
            "activated_endpoint_prefix": r.get("activated_endpoint_prefix"),
            "last_independent_call_day": r.get("last_independent_call_day"),
            "anchors": anchors,
        })

    for bucket in AGGREGATED:
        if not agg_dids[bucket]:
            continue
        # Sorted by leaf, not by credential id: consecutive ids and issue times
        # let a reader line the aggregate up against the itemised rows, which
        # would undo the point of aggregating it.
        shuffled = sorted(agg_anchors[bucket],
                          key=lambda a: (a.get("merkle_proof") or {}).get("leaf", ""))
        for a in shuffled:
            a.pop("credential_id", None)
            a.pop("issued_at", None)
        rows.append({
            "kind": "aggregate",
            "bucket": bucket,
            "dids": agg_dids[bucket],
            "activated": agg_activated[bucket],
            "note": PARTNER_NOTE,
            "anchors": shuffled,
        })

    counted_dids = sum(by_bucket.get(b, {}).get("dids", 0) for b in COUNTED)
    counted_anchors = sum(by_bucket.get(b, {}).get("anchors", 0) for b in COUNTED)
    activated = sum(by_bucket.get(b, {}).get("activated", 0) for b in COUNTED)
    roots, txs = set(), set()
    for r in rows:
        for a in r.get("anchors") or []:
            root = (a.get("merkle_proof") or {}).get("root")
            if root:
                roots.add(root)
            if a.get("anchor_tx"):
                txs.add(a["anchor_tx"])

    totals = {
        "duplicates_withheld": withheld,
        "activated_counted": activated,
        "registered_with_anchor": counted_dids,
        "anchors": counted_anchors,
        "roots": len(roots),
        "transactions": len(txs),
        "rows": len(rows),
    }

    body = {
        "as_of": as_of,
        "as_of_note": AS_OF_NOTE,
        "counting_rule": {
            "headline": "activated_counted",
            "activated_definition":
                "registered, not revoked, at least one anchored credential, and at "
                "least one authenticated call to an endpoint no bounty task text "
                "prescribed",
            "counted_buckets": list(COUNTED),
            "aggregated_buckets": list(AGGREGATED),
            "platform_universe": "allow-list in app/sql/registry_export.sql; a "
                                 "platform absent from it produces no row",
        },
        "totals": totals,
        "by_bucket": by_bucket,
        "duplicates_note":
            "Later credentials of a type a DID already holds are not listed. They "
            "are validly issued, anchored and not revoked; three agents polled the "
            "issuing endpoint instead of the trust score between 1 and 3 October "
            "2026 and were issued one per call. The cause is fixed. The count of "
            "DIDs is unaffected, which the export asserts rather than assumes.",
        "not_recomputable": ["activated", "activated_endpoint_prefix",
                             "last_independent_call_day"],
        "not_recomputable_note": NOT_RECOMPUTABLE_NOTE,
        "rows": rows,
    }

    # Completeness. The four buckets have to account for every row the query
    # returned, and the counted three have to account for the headline. A reader
    # that cannot show that returns an error, not a number.
    if sum(b["dids"] for b in by_bucket.values()) != len(raw):
        raise IncompleteRead(f"{len(raw)} rows but buckets hold "
                             f"{sum(b['dids'] for b in by_bucket.values())}")
    if counted_dids and not roots:
        raise IncompleteRead("counted agents but no Merkle roots")

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chain": "eip155:8453",
        "calldata_prefix": CALLDATA_PREFIX,
        "leaf_preimage":
            "sha256(credential_id|subject_did|credential_type|issued_at|proof_value)",
        "data_sha256": hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        **body,
    }


def counts(as_of: str | None = None) -> dict:
    """Just the totals, for callers that report a figure and nothing else."""
    doc = build(as_of)
    return {"totals": doc["totals"], "by_bucket": doc["by_bucket"],
            "as_of": doc["as_of"]}
