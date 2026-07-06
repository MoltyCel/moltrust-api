"""
Anti-Collusion Mechanism — Whitepaper v4, Section 4.3

Zwei Mechanismen:
1. Jaccard Cluster Detection (threshold 0.8)
   Gegenseitige Endorsements in dichten Clustern -> Penalty
2. Vertical Diversity Requirement (min 3 distinct verticals)
   Alle Endorsements aus < 3 Verticals -> Penalty

Bekannte Limitation (ehrlich dokumentiert per Whitepaper):
Resistent gegen kleine Sybil-Cluster (< ~10 Agents) und
naive gegenseitige Endorsement-Ringe. Nicht resistent gegen
gut-finanzierte Angreifer mit diversen, langjährigen Identitäten.
"""
import asyncpg
from typing import Set


async def _get_endorsed_by(
    conn: asyncpg.Connection,
    endorser_did: str
) -> Set[str]:
    """Alle DIDs die dieser Agent endorsed hat."""
    rows = await conn.fetch(
        "SELECT endorsed_did FROM endorsements WHERE endorser_did = $1",
        endorser_did
    )
    return {r["endorsed_did"] for r in rows}


async def _get_verticals(
    conn: asyncpg.Connection,
    endorsed_did: str
) -> Set[str]:
    """Alle Verticals aus denen endorsements kommen."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT vertical
        FROM endorsements
        WHERE endorsed_did = $1
          AND expires_at > NOW()
        """,
        endorsed_did
    )
    return {r["vertical"] for r in rows}


async def compute_sybil_penalty(
    did: str,
    endorsers: Set[str],
    conn: asyncpg.Connection
) -> float:
    """
    Vollständiger Graph-Check per Whitepaper Section 4.3.
    Returns sybil_penalty [0, inf).
    Unter normalen Bedingungen: 0.0.
    """
    endorser_list = list(endorsers)

    # 1. JACCARD CLUSTER DETECTION
    jaccard = 0.0
    if len(endorser_list) > 1:
        cluster_links = {}
        for e in endorser_list:
            cluster_links[e] = await _get_endorsed_by(conn, e)

        mutual_count = 0
        total_pairs = 0

        for i in range(len(endorser_list)):
            for j in range(i + 1, len(endorser_list)):
                a = endorser_list[i]
                b = endorser_list[j]
                a_to_b = b in cluster_links.get(a, set())
                b_to_a = a in cluster_links.get(b, set())

                if a_to_b or b_to_a:
                    total_pairs += 1
                if a_to_b and b_to_a:
                    mutual_count += 1

        if total_pairs > 0:
            jaccard = mutual_count / total_pairs

    # TechSpec v0.10 (VD1): bounded sybil penalty = 20·max(0, jaccard−0.7) ∈ [0, 6].
    # Callers subtract this value directly (the legacy `· 20` in the score formula is
    # removed). REMOVED with this change: the bespoke `jaccard·N·0.5` (unbounded,
    # endorser-count-scaled), the 0.8 threshold, and the `+10` vertical-diversity add.
    # NOTE: that `+10` was a vertical-diversity penalty (<3 verticals), NOT a sybil
    # signal — dropping it removes vertical-diversity penalization from this term.
    #
    # DROPPED v0.10 (PR #217): vertical-diversity penalty (<3 verticals, +10) entfernt —
    # war KEIN sybil-signal. Bewusst deferred, nicht relevant. Falls Wiederaufnahme:
    # als eigener low_diversity_penalty, NICHT zurueck in compute_sybil_penalty. Siehe git-BACKLOG.
    return 20.0 * max(0.0, jaccard - 0.7)
