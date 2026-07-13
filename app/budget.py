"""Operator-level monthly budget caps for agents.

A pure-logic module — endpoints in `app/main.py` call into it; nothing here
imports `app.main`. The shape of every public function is:

    async fn(conn: asyncpg.Connection, ...) -> dict | None

so callers can compose the helpers inside their own transaction.

Status machine (see `app/migrations/009_agent_budget_caps.sql` for SQL
constraints):

    active     → warning   when spend >= cap * warning_threshold
    warning    → capped    when spend >= cap * 1.0
    capped     → active    at start of next month (lazy reset on read/write)
    any        → suspended manually (set by operator/admin only)

Telegram alerts fire on transitions `active → warning` and any-→ `capped`.
They go to the global `TELEGRAM_CHAT_ID` for now — per-operator routing is
a follow-up sprint once we have `operators.telegram_chat_id` modeled.
"""
from __future__ import annotations

import datetime
import logging
import os
from typing import Optional

import asyncpg
import httpx

from app import notify

logger = logging.getLogger("moltrust.budget")

DEFAULT_WARNING_THRESHOLD = 0.8

# Status constants — single source of truth, matches the CHECK constraint.
STATUS_ACTIVE     = "active"
STATUS_WARNING    = "warning"
STATUS_CAPPED     = "capped"
STATUS_SUSPENDED  = "suspended"

# Partner platforms with negotiated free-tier access. record_spend_event
# short-circuits to {"status": "exempt"} before touching agent_budget_caps.
# TODO: aeoess (1000 agents) and agentnexus (2 agents) have agreed quotas
# we don't yet enforce — both are well below today (aeoess 17, agentnexus 3),
# so deferred until one approaches the limit.
EXEMPT_PLATFORMS = {"ownify", "aeoess", "agentnexus"}


def _month_key(now: Optional[datetime.datetime] = None) -> str:
    """Return e.g. '2026-05' for the current UTC month."""
    return (now or datetime.datetime.utcnow()).strftime("%Y-%m")


def _percentage(spend: float, cap: float) -> float:
    """Spend / cap, safe against zero-cap rows."""
    if cap <= 0:
        return 0.0
    return round(spend / cap, 4)


def _to_payload(row: asyncpg.Record) -> dict:
    """Shape an `agent_budget_caps` row into the public API response."""
    return {
        "operator_did":        row["operator_did"],
        "agent_did":           row["agent_did"],
        "monthly_cap_chf":     float(row["monthly_cap_chf"]),
        "warning_threshold":   float(row["warning_threshold"]),
        "current_month_spend": float(row["current_month_spend"]),
        "current_month_key":   row["current_month_key"],
        "status":              row["status"],
        "spend_percentage":    _percentage(
            float(row["current_month_spend"]), float(row["monthly_cap_chf"]),
        ),
    }


# ---------------------------------------------------------------------------
# Lazy monthly reset
# ---------------------------------------------------------------------------

async def _maybe_reset_month(conn: asyncpg.Connection, row: asyncpg.Record) -> asyncpg.Record:
    """If the row's `current_month_key` is stale, reset it in place.

    Suspended rows do NOT get auto-reactivated — the operator must clear
    `suspended` explicitly via an upsert.

    Returns the up-to-date row (re-fetched on reset, original otherwise).
    """
    now_key = _month_key()
    if row["current_month_key"] == now_key:
        return row
    if row["status"] == STATUS_SUSPENDED:
        # Keep `current_month_key` ticking forward so the row stays "fresh"
        # in queries, but don't change status or spend.
        await conn.execute(
            "UPDATE agent_budget_caps SET current_month_key = $1, updated_at = NOW() "
            "WHERE id = $2",
            now_key, row["id"],
        )
    else:
        await conn.execute(
            "UPDATE agent_budget_caps "
            "SET current_month_spend = 0.0, status = $1, current_month_key = $2, updated_at = NOW() "
            "WHERE id = $3",
            STATUS_ACTIVE, now_key, row["id"],
        )
    refreshed = await conn.fetchrow(
        "SELECT * FROM agent_budget_caps WHERE id = $1", row["id"],
    )
    return refreshed


# ---------------------------------------------------------------------------
# CRUD-ish helpers used by the endpoints
# ---------------------------------------------------------------------------

async def upsert_cap(
    conn: asyncpg.Connection,
    operator_did: str,
    agent_did: str,
    monthly_cap_chf: float,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
) -> dict:
    """Set or update the budget cap. Preserves accumulated spend; clears
    `suspended` so operator-driven updates reactivate the row."""
    now_key = _month_key()
    row = await conn.fetchrow(
        """
        INSERT INTO agent_budget_caps
            (operator_did, agent_did, monthly_cap_chf, warning_threshold,
             current_month_spend, current_month_key, status)
        VALUES ($1, $2, $3, $4, 0.0, $5, 'active')
        ON CONFLICT (operator_did, agent_did) DO UPDATE
            SET monthly_cap_chf   = EXCLUDED.monthly_cap_chf,
                warning_threshold = EXCLUDED.warning_threshold,
                current_month_key = EXCLUDED.current_month_key,
                status            = CASE
                    WHEN agent_budget_caps.status = 'suspended' THEN 'active'
                    ELSE agent_budget_caps.status
                END,
                updated_at        = NOW()
        RETURNING *
        """,
        operator_did, agent_did, monthly_cap_chf, warning_threshold, now_key,
    )
    # Recompute status against the *current* spend (an existing row may have
    # already crossed warning/capped under the old cap value).
    row = await _recompute_status(conn, row)
    return _to_payload(row)


async def get_cap(
    conn: asyncpg.Connection, operator_did: str, agent_did: str,
) -> Optional[dict]:
    row = await conn.fetchrow(
        "SELECT * FROM agent_budget_caps WHERE operator_did = $1 AND agent_did = $2",
        operator_did, agent_did,
    )
    if row is None:
        return None
    row = await _maybe_reset_month(conn, row)
    return _to_payload(row)


async def list_caps_for_operator(conn: asyncpg.Connection, operator_did: str) -> dict:
    rows = await conn.fetch(
        "SELECT * FROM agent_budget_caps WHERE operator_did = $1 ORDER BY agent_did",
        operator_did,
    )
    payloads = []
    total_cap = 0.0
    total_spend = 0.0
    for row in rows:
        row = await _maybe_reset_month(conn, row)
        p = _to_payload(row)
        payloads.append({
            "agent_did":           p["agent_did"],
            "monthly_cap_chf":     p["monthly_cap_chf"],
            "current_month_spend": p["current_month_spend"],
            "status":              p["status"],
            "spend_percentage":    p["spend_percentage"],
        })
        total_cap   += p["monthly_cap_chf"]
        total_spend += p["current_month_spend"]
    return {
        "operator_did":          operator_did,
        "agents":                payloads,
        "total_monthly_cap_chf": round(total_cap, 2),
        "total_current_spend":   round(total_spend, 2),
    }


# ---------------------------------------------------------------------------
# Spend events — the hook point for renewal / issuance / anchor / export
# ---------------------------------------------------------------------------

async def record_spend_event(
    conn: asyncpg.Connection,
    agent_did: str,
    event_type: str,
    amount_chf: float,
    stripe_price_id: Optional[str] = None,
    gate_event_id: Optional[int] = None,
    telegram_client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """Append a spend event, accumulate it on the cap, transition status.

    Designed to be safe to call even when the agent has no cap or no
    operator — those agents are simply unmetered (returns
    `{"status": "no_cap"}`). Partner platforms (Ownify, Aeoess, AgentNexus)
    short-circuit to `{"status": "exempt"}` before metering — see
    EXEMPT_PLATFORMS for the current allowlist.

    Returns a small status dict the caller can branch on:
        {"status": "no_cap"|"exempt"|"active"|"warning"|"capped"|"suspended",
         "spend_pct": float, "transition": "active→warning"|None,
         "exemption_reason": "platform:<name>"}  # only on "exempt"
    """
    # 1. Resolve operator from the agent record. If the agent isn't claimed
    #    by an operator, there's nothing to meter — log the event for audit
    #    but skip status logic.
    agent = await conn.fetchrow(
        "SELECT operator_did, platform FROM agents WHERE did = $1", agent_did,
    )
    operator_did = agent["operator_did"] if agent else None

    # Partner-platform exemption — log for analytics, skip metering. Wins over
    # the operator/cap branches below: an Ownify agent with a cap configured
    # still goes through exempt (deal terms beat operator policy).
    if agent is not None and agent["platform"] in EXEMPT_PLATFORMS:
        await conn.execute(
            "INSERT INTO budget_spend_events "
            "(operator_did, agent_did, event_type, amount_chf, stripe_price_id, gate_event_id) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            operator_did or "", agent_did, event_type, amount_chf,
            stripe_price_id, gate_event_id,
        )
        return {"status": "exempt", "spend_pct": 0.0, "transition": None,
                "exemption_reason": f"platform:{agent['platform']}"}

    if operator_did is None:
        # We still log unmetered events so analytics can see usage even
        # without a cap in place.
        await conn.execute(
            "INSERT INTO budget_spend_events "
            "(operator_did, agent_did, event_type, amount_chf, stripe_price_id, gate_event_id) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            "", agent_did, event_type, amount_chf, stripe_price_id, gate_event_id,
        )
        return {"status": "no_cap", "spend_pct": 0.0, "transition": None}

    # 2. Lock the cap row, lazy-reset month, then accumulate.
    # Atomic read-modify-write. The FOR UPDATE row lock only serialises
    # concurrent spends for the same agent if held INSIDE a transaction —
    # without it asyncpg auto-commits the SELECT and the lock is released
    # immediately, so two simultaneous spends both read prev_status='active'
    # and both emit the 'active->warning' alert (duplicate-alert bug). The
    # transaction makes the status transition single-fire per crossing.
    async with conn.transaction():
        cap_row = await conn.fetchrow(
            "SELECT * FROM agent_budget_caps "
            "WHERE operator_did = $1 AND agent_did = $2 FOR UPDATE",
            operator_did, agent_did,
        )
        if cap_row is None:
            # Operator is set but no cap configured — same as no_cap, but we
            # do log against the resolved operator_did.
            await conn.execute(
                "INSERT INTO budget_spend_events "
                "(operator_did, agent_did, event_type, amount_chf, stripe_price_id, gate_event_id) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                operator_did, agent_did, event_type, amount_chf, stripe_price_id, gate_event_id,
            )
            return {"status": "no_cap", "spend_pct": 0.0, "transition": None}

        cap_row = await _maybe_reset_month(conn, cap_row)
        prev_status = cap_row["status"]

        new_spend = float(cap_row["current_month_spend"]) + amount_chf
        new_status = _status_for(new_spend, float(cap_row["monthly_cap_chf"]),
                                 float(cap_row["warning_threshold"]),
                                 current=prev_status)
        cap_chf = float(cap_row["monthly_cap_chf"])
        await conn.execute(
            "UPDATE agent_budget_caps "
            "SET current_month_spend = $1, status = $2, updated_at = NOW() "
            "WHERE id = $3",
            new_spend, new_status, cap_row["id"],
        )
        await conn.execute(
            "INSERT INTO budget_spend_events "
            "(operator_did, agent_did, event_type, amount_chf, stripe_price_id, gate_event_id) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            operator_did, agent_did, event_type, amount_chf, stripe_price_id, gate_event_id,
        )

    # --- transaction committed, cap-row lock released ---
    transition = f"{prev_status}→{new_status}" if prev_status != new_status else None
    pct = _percentage(new_spend, cap_chf)

    # Telegram alert on crossings — fire-and-forget, AFTER commit so the
    # HTTP call never holds the cap-row lock. Single-fire because the
    # transition is now computed under serialised access.
    if transition in {"active→warning", "warning→capped", "active→capped"}:
        try:
            await _send_telegram_alert(
                telegram_client,
                new_status, operator_did, agent_did, new_spend,
                cap_chf, pct,
            )
        except Exception as e:
            logger.warning("telegram alert failed for %s: %s", agent_did, e)

    return {"status": new_status, "spend_pct": pct, "transition": transition}


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def _status_for(spend: float, cap: float, warning_threshold: float, *, current: str) -> str:
    """Derive the new status from a spend / cap pair.

    Suspended is sticky — only an explicit operator action clears it. All
    other states recompute from the numbers.
    """
    if current == STATUS_SUSPENDED:
        return STATUS_SUSPENDED
    if cap <= 0:
        return STATUS_ACTIVE
    if spend >= cap:
        return STATUS_CAPPED
    if spend >= cap * warning_threshold:
        return STATUS_WARNING
    return STATUS_ACTIVE


async def _recompute_status(conn: asyncpg.Connection, row: asyncpg.Record) -> asyncpg.Record:
    """After a cap edit, the current spend may already cross the new
    thresholds. Recompute and persist the status."""
    new_status = _status_for(
        float(row["current_month_spend"]),
        float(row["monthly_cap_chf"]),
        float(row["warning_threshold"]),
        current=row["status"],
    )
    if new_status == row["status"]:
        return row
    await conn.execute(
        "UPDATE agent_budget_caps SET status = $1, updated_at = NOW() WHERE id = $2",
        new_status, row["id"],
    )
    return await conn.fetchrow("SELECT * FROM agent_budget_caps WHERE id = $1", row["id"])


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _format_alert(status: str, operator_did: str, agent_did: str,
                  spend: float, cap: float, pct: float) -> str:
    pct_int = int(pct * 100)
    icon = "🔴" if status == STATUS_CAPPED else "⚠️"
    headline = "Budget Cap Reached" if status == STATUS_CAPPED else "Budget Warning"
    tail = "Agent is now rate-limited." if status == STATUS_CAPPED else f"Status: {status}"
    return (
        f"{icon} {headline}: Agent {agent_did}\n"
        f"Operator: {operator_did}\n"
        f"Spend: CHF {spend:.2f} / {cap:.2f} ({pct_int}%)\n"
        f"{tail}"
    )


async def _send_telegram_alert(
    client: Optional[httpx.AsyncClient],
    status: str, operator_did: str, agent_did: str,
    spend: float, cap: float, pct: float,
) -> None:
    """Best-effort Telegram notification. Sends ONLY when Telegram is enabled via
    MOLTRUST_NOTIFY (decoupled from MOLTRUST_ENV, which governs crypto posture);
    otherwise it logs the alert instead of sending. No-ops when secrets missing."""
    message = _format_alert(status, operator_did, agent_did, spend, cap, pct)
    # Telegram-gate: MOLTRUST_NOTIFY (NOT MOLTRUST_ENV — that stays on KMS/PQC).
    if not notify.telegram_allowed(f"budget alert: {message}", logger=logger):
        return
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=10.0)
    try:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
        )
    finally:
        if owns_client:
            await client.aclose()
