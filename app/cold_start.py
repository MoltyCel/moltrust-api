"""F2 Cold-Start Score — derive a startup score for agents without endorsements.

Phase 2 trust scoring withholds a score when an agent has fewer than three
endorsements. That makes brand-new agents indistinguishable from inactive
ones. The cold-start score fills that gap by surfacing what *public*
signals are available (on-chain wallet history, GitHub activity, ERC-8004
registry presence) without inventing a number when none exist.

Contract (per Whitepaper v4 follow-up "Onboarding Q3 2026"):

- Only applies when `endorser_count == 0`. Once endorsements arrive, the
  Phase 2 score replaces this estimate.
- A NULL score with basis `"no_public_data"` is honest — never a fabricated
  default.
- Cap at 60.0 so a cold-start agent can never appear stronger than an
  endorsed one.
- Cached in `agents.cold_start_*` for 24h to avoid hammering Blockscout and
  GitHub on every trust-score lookup.

This module is HTTP-tolerant: any fetcher that fails returns `None` and the
score falls back to the remaining sources.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

import asyncpg

log = logging.getLogger("moltrust.cold_start")

CACHE_TTL_HOURS = 24
COLD_START_CAP = 60.0
HTTP_TIMEOUT_SECONDS = 8

BLOCKSCOUT_API = "https://base.blockscout.com/api"
GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# External fetchers (HTTP) — kept tiny, return None on any failure
# ---------------------------------------------------------------------------

def _http_get_json(url: str, headers: Optional[dict] = None) -> Optional[dict]:
    try:
        req = Request(url, headers={"User-Agent": "MolTrust-ColdStart/1.0", **(headers or {})})
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as r:  # noqa: S310 — URL is constructed from API constants
            return json.loads(r.read())
    except Exception as e:
        log.warning("cold-start http fetch failed for %s: %s", url, e)
        return None


def fetch_blockscout_wallet(wallet: str) -> Optional[dict]:
    """Return {tx_count, age_days, usdc_volume} from Blockscout, or None on failure.

    USDC volume is approximated as the count of USDC token transfers — Blockscout's
    ERC-20 transfer endpoint gives us the per-tx amounts but the cold-start
    formula caps at $1000-equivalent, so the rough sum is sufficient.

    Uses Blockscout's Etherscan-compatible /api, which needs no API key
    (migrated from the deprecated Basescan v1 API, 2026-06).
    """
    if not wallet:
        return None

    # 1) Tx list — most recent up to 10k
    tx_url = (
        f"{BLOCKSCOUT_API}?module=account&action=txlist"
        f"&address={quote(wallet)}&startblock=0&endblock=99999999"
        f"&sort=asc"
    )
    tx_data = _http_get_json(tx_url)
    if not tx_data or tx_data.get("status") != "1":
        return None
    txs = tx_data.get("result", []) or []
    tx_count = len(txs)
    if tx_count == 0:
        return {"tx_count": 0, "age_days": 0, "usdc_volume": 0.0}

    try:
        first_ts = int(txs[0].get("timeStamp", 0))
    except (TypeError, ValueError):
        first_ts = 0
    age_days = max(0, int((datetime.datetime.utcnow().timestamp() - first_ts) / 86400)) if first_ts else 0

    # 2) USDC volume (Base USDC contract). Cheap approximation: count + sum
    usdc_contract = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    erc_url = (
        f"{BLOCKSCOUT_API}?module=account&action=tokentx"
        f"&contractaddress={usdc_contract}"
        f"&address={quote(wallet)}&sort=asc"
    )
    erc_data = _http_get_json(erc_url)
    usdc_volume = 0.0
    if erc_data and erc_data.get("status") == "1":
        for t in erc_data.get("result", []) or []:
            try:
                # USDC has 6 decimals on Base
                usdc_volume += float(t.get("value", 0)) / 1e6
            except (TypeError, ValueError):
                pass

    return {"tx_count": tx_count, "age_days": age_days, "usdc_volume": usdc_volume}


def fetch_github_user(username: str) -> Optional[dict]:
    """Return {public_repos, account_age_days, recent_commits} from GitHub, or None.

    `recent_commits` uses the public events feed (last 90 days, push events only).
    """
    if not username:
        return None
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    user = _http_get_json(f"{GITHUB_API}/users/{quote(username)}", headers=headers)
    if not user or "login" not in user:
        return None

    try:
        created_at = datetime.datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
        age_days = max(0, (datetime.datetime.now(datetime.timezone.utc) - created_at).days)
    except (KeyError, ValueError):
        age_days = 0

    # Recent commits: list push events from the last 90 days
    events = _http_get_json(f"{GITHUB_API}/users/{quote(username)}/events/public", headers=headers) or []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)
    recent_commits = 0
    if isinstance(events, list):
        for ev in events:
            if ev.get("type") != "PushEvent":
                continue
            try:
                ev_ts = datetime.datetime.fromisoformat(ev.get("created_at", "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if ev_ts >= cutoff:
                payload = ev.get("payload") or {}
                recent_commits += len(payload.get("commits") or [])

    return {
        "public_repos": int(user.get("public_repos", 0) or 0),
        "account_age_days": age_days,
        "recent_commits": recent_commits,
    }


async def check_erc8004_match(wallet: str, conn: asyncpg.Connection) -> bool:
    """True if this wallet shows up in our local erc8004_outreach scan."""
    if not wallet:
        return False
    try:
        row = await conn.fetchrow(
            "SELECT 1 FROM erc8004_outreach WHERE LOWER(wallet_address) = LOWER($1) LIMIT 1",
            wallet,
        )
        return row is not None
    except asyncpg.UndefinedTableError:
        return False


# ---------------------------------------------------------------------------
# Pure scoring — no IO, easy to test
# ---------------------------------------------------------------------------

def calculate_cold_start_score(
    wallet_data: Optional[dict],
    github_data: Optional[dict],
    erc8004_match: bool,
) -> tuple[Optional[float], str, str]:
    """Score in [0, 60], basis tag, and confidence label.

    Returns (None, "no_public_data", "none") when nothing is available — by
    design, we never fabricate a starting score from thin air.
    """
    score = 0.0
    basis: list[str] = []

    if wallet_data and wallet_data.get("tx_count", 0) > 0:
        tx_score = min(wallet_data.get("tx_count", 0) / 10, 8)
        age_score = min(wallet_data.get("age_days", 0) / 30, 7)
        vol_score = min(wallet_data.get("usdc_volume", 0) / 1000, 5)
        score += tx_score + age_score + vol_score
        basis.append("onchain")

    if github_data and github_data.get("public_repos", 0) > 0:
        repo_score = min(github_data.get("public_repos", 0) / 3, 5)
        age_score = min(github_data.get("account_age_days", 0) / 60, 5)
        commit_score = min(github_data.get("recent_commits", 0) / 10, 5)
        score += repo_score + age_score + commit_score
        basis.append("github")

    if erc8004_match:
        score += 10
        basis.append("erc8004")

    if not basis:
        return None, "no_public_data", "none"

    confidence = "high" if len(basis) >= 2 else "medium" if len(basis) == 1 else "low"
    return min(round(score, 1), COLD_START_CAP), "+".join(basis), confidence


# ---------------------------------------------------------------------------
# Orchestrator + 24h cache
# ---------------------------------------------------------------------------

async def get_cold_start_score(did: str, conn: asyncpg.Connection) -> dict:
    """Return the cold-start payload for an agent, computing or returning a cached value.

    Result shape:
        {
            "cold_start_score": float | None,
            "cold_start_basis": str,
            "cold_start_confidence": "none"|"low"|"medium"|"high",
            "cold_start_computed_at": iso str | None,
            "cold_start_note": str,
        }
    """
    row = await conn.fetchrow(
        "SELECT wallet_address, cold_start_score, cold_start_basis, "
        "cold_start_confidence, cold_start_computed_at "
        "FROM agents WHERE did = $1",
        did,
    )
    if row is None:
        return _payload(None, "no_public_data", "none", None)

    computed_at = row["cold_start_computed_at"]
    if computed_at:
        age = datetime.datetime.utcnow() - (
            computed_at.replace(tzinfo=None) if computed_at.tzinfo else computed_at
        )
        if age < datetime.timedelta(hours=CACHE_TTL_HOURS):
            return _payload(
                row["cold_start_score"],
                row["cold_start_basis"] or "no_public_data",
                row["cold_start_confidence"] or "none",
                computed_at,
            )

    wallet = row["wallet_address"]
    wallet_data = fetch_blockscout_wallet(wallet) if wallet else None
    erc8004_match = await check_erc8004_match(wallet, conn) if wallet else False
    # GitHub username is not yet a first-class field on agents; once the
    # registration schema carries it this branch will activate. Until then
    # we explicitly pass None rather than guessing.
    github_data = None

    score, basis, confidence = calculate_cold_start_score(wallet_data, github_data, erc8004_match)
    now = datetime.datetime.utcnow()
    await conn.execute(
        "UPDATE agents SET cold_start_score = $1, cold_start_basis = $2, "
        "cold_start_confidence = $3, cold_start_computed_at = $4 WHERE did = $5",
        score, basis, confidence, now, did,
    )
    return _payload(score, basis, confidence, now)


def _payload(score: Optional[float], basis: str, confidence: str, computed_at) -> dict:
    if score is None:
        note = "No public history found. Score will build from first interactions."
    else:
        note = "Score based on public data. Will be replaced by behavioral history."
    return {
        "cold_start_score": score,
        "cold_start_basis": basis,
        "cold_start_confidence": confidence,
        "cold_start_computed_at": computed_at.isoformat() if computed_at else None,
        "cold_start_note": note,
    }
