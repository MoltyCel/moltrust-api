"""Track-record credentials: the threshold, the claims, and the gate field.

No database and no network. The three things under test are decisions, and a
decision that needs a live chain to check is a decision nobody can review.
"""

import time

import pytest

from app.signature import build_gate_payload
import app.track_record as tr
from app.track_record import (
    CREDENTIAL_TYPE,
    MIN_AGE_DAYS,
    MIN_NONCE,
    REQUIRED_CHAIN,
    NotEligible,
    anchored_track_record,
    build_claims,
    check_eligible,
)

DID = "did:moltrust:0000000000000001"
WALLET = "0x" + "11" * 20


def measurement(**over):
    base = {
        "wallet_address": WALLET,
        "wallet_chain": REQUIRED_CHAIN,
        "nonce": 4,
        "wallet_age_days": 30,
        "wallet_tx_count": 12,
        "usdc_volume": 3.5,
        "measured_at": "2026-09-23T00:00:00+00:00",
        "thresholds": {"min_nonce": MIN_NONCE, "min_age_days": MIN_AGE_DAYS},
    }
    base.update(over)
    return base


# --- the threshold ---------------------------------------------------------

def test_a_wallet_with_history_clears_it():
    check_eligible(measurement())


def test_a_wallet_that_never_sent_anything_does_not():
    """The case that matters: gasless relaying leaves a worker wallet at 0."""
    with pytest.raises(NotEligible) as exc:
        check_eligible(measurement(nonce=0))
    assert "sent 0 transactions" in str(exc.value)
    assert "send one transaction of your own" in str(exc.value)


def test_a_wallet_made_this_morning_does_not():
    with pytest.raises(NotEligible) as exc:
        check_eligible(measurement(wallet_age_days=2))
    assert f"threshold is {MIN_AGE_DAYS}" in str(exc.value)


def test_exactly_at_both_thresholds_passes():
    check_eligible(measurement(nonce=MIN_NONCE, wallet_age_days=MIN_AGE_DAYS))


# --- the claims ------------------------------------------------------------

def test_claims_carry_the_numbers_and_the_rule_they_were_judged_against():
    """A verifier redoes the judgement from the credential, not from our page."""
    claims = build_claims(DID, measurement())
    assert claims["id"] == DID
    assert claims["type"] == CREDENTIAL_TYPE
    assert claims["wallet_chain"] == REQUIRED_CHAIN
    assert claims["nonce"] == 4
    assert claims["thresholds"] == {"min_nonce": MIN_NONCE, "min_age_days": MIN_AGE_DAYS}


def test_claims_say_what_the_credential_does_not_assert():
    claims = build_claims(DID, measurement())
    assert "says nothing about what the agent does" in claims["note"]


# --- the gate field --------------------------------------------------------

GOOD_TR = {"issued_at": "2026-09-22T06:00:00+00:00", "anchor_tx": "0x" + "ab" * 32}


def _payload(track_record=None):
    return build_gate_payload(
        did=DID, public_key="aa" * 32, trust_score=None, withheld=True,
        credential_types=["AgentTrustCredential"],
        computed_at="2026-09-23T00:00:00+00:00",
        valid_until="2026-09-23T01:00:00+00:00",
        policy_version="phase2", track_record=track_record,
    )


def test_the_field_is_absent_rather_than_null_when_there_is_none():
    """A gate that reads a null here and carries on is the whole failure mode."""
    assert "track_record" not in _payload()


def test_the_field_is_carried_when_there_is_one():
    assert _payload(GOOD_TR)["track_record"] == GOOD_TR


def test_the_rest_of_the_payload_is_unchanged_by_it():
    without, with_ = _payload(), _payload(GOOD_TR)
    del with_["track_record"]
    assert without == with_


# --- what reaches the attestation -----------------------------------------

class _FakeConn:
    def __init__(self, row):
        self._row = row
        self.sql = None

    async def fetchrow(self, sql, *args):
        self.sql = sql
        return self._row


@pytest.mark.asyncio
async def test_no_credential_means_no_field():
    assert await anchored_track_record(_FakeConn(None), DID) is None


@pytest.mark.asyncio
async def test_an_unanchored_credential_means_no_field():
    """Issued is not anchored. anchor_tx is what a relying party checks."""
    import datetime

    row = {"issued_at": datetime.datetime(2026, 9, 23), "tx_hash": None}
    assert await anchored_track_record(_FakeConn(row), DID) is None


@pytest.mark.asyncio
async def test_an_anchored_credential_becomes_the_field():
    import datetime

    row = {"issued_at": datetime.datetime(2026, 9, 22, 6, 0), "tx_hash": "0x" + "cd" * 32}
    got = await anchored_track_record(_FakeConn(row), DID)
    assert got["anchor_tx"] == "0x" + "cd" * 32
    assert got["issued_at"].startswith("2026-09-22T06:00")


@pytest.mark.asyncio
async def test_a_revoked_or_expired_one_is_filtered_in_sql():
    """The filter belongs in the query; a later check would read stale rows."""
    conn = _FakeConn(None)
    await anchored_track_record(conn, DID)
    assert "revoked IS FALSE" in conn.sql
    assert "expires_at > now()" in conn.sql


# --- node-first measurement ------------------------------------------------
#
# The hazard this replaced: Blockscout throttled a 78-wallet sweep into 68
# "not readable" on 2026-09-25, and issuance used to depend on it. A hundred
# agents in the same hour would have been told "could not measure" instead of
# yes or no.

def _no_explorer(monkeypatch):
    """Make the enrichment fetcher fail the way a throttled explorer does."""
    import app.cold_start as cs

    def boom(_wallet):
        raise RuntimeError("HTTP 429 rate limit")

    monkeypatch.setattr(cs, "fetch_blockscout_wallet", boom)


def test_a_dead_explorer_does_not_stop_the_measurement(monkeypatch):
    _no_explorer(monkeypatch)
    monkeypatch.setattr(tr, "wallet_nonce", lambda w: 4)
    old = int(time.time()) - 30 * 86400
    m = tr.measure(WALLET, first_tx={"block": 123, "ts": old, "source": "node"})
    assert m["nonce"] == 4
    assert m["wallet_age_days"] >= 29
    assert m["enriched"] is False
    tr.check_eligible(m)          # decides, rather than refusing to


def test_a_dead_explorer_leaves_the_enriched_fields_empty(monkeypatch):
    _no_explorer(monkeypatch)
    monkeypatch.setattr(tr, "wallet_nonce", lambda w: 1)
    m = tr.measure(WALLET, first_tx={"block": 1, "ts": int(time.time()) - 10 * 86400,
                                     "source": "node"})
    assert m["wallet_tx_count"] == 0 and m["usdc_volume"] == 0.0
    assert m["age_source"] == "node"


def test_the_node_is_the_floor_and_the_explorer_may_only_raise_it(monkeypatch):
    """First-outgoing is never earlier than first-of-any-kind, so the node age
    is a lower bound. Taking the larger of the two can admit nobody the node
    alone would have turned away."""
    import app.cold_start as cs
    monkeypatch.setattr(cs, "fetch_blockscout_wallet",
                        lambda w: {"age_days": 40, "tx_count": 12, "usdc_volume": 3.0})
    monkeypatch.setattr(tr, "wallet_nonce", lambda w: 2)
    m = tr.measure(WALLET, first_tx={"block": 9, "ts": int(time.time()) - 9 * 86400,
                                     "source": "node"})
    assert m["wallet_age_days"] == 40
    assert m["age_source"] == "node+explorer"


def test_an_unreachable_node_refuses_rather_than_guesses(monkeypatch):
    monkeypatch.setattr(tr, "wallet_nonce", lambda w: None)
    with pytest.raises(NotEligible) as exc:
        tr.measure(WALLET)
    assert "try again shortly" in str(exc.value)


def test_a_wallet_that_never_sent_has_no_first_block(monkeypatch):
    calls = {"n": 0}

    def fake_rpc(method, params):
        if method == "eth_blockNumber":
            return hex(60_000_000)
        if method == "eth_getTransactionCount":
            calls["n"] += 1
            return "0x0"
        return {"timestamp": "0x0"}

    monkeypatch.setattr(tr, "_rpc", fake_rpc)
    got = tr.first_outgoing(WALLET)
    assert got == {"block": None, "ts": None, "source": "node"}
    # One probe at the head is enough; no search for something that is not there.
    assert calls["n"] == 1


def test_a_wallet_older_than_the_window_reports_a_floor(monkeypatch):
    def fake_rpc(method, params):
        if method == "eth_blockNumber":
            return hex(60_000_000)
        if method == "eth_getTransactionCount":
            return "0x5"          # active at the head and at the window start
        return {"timestamp": hex(int(time.time()) - 40 * 86400)}

    monkeypatch.setattr(tr, "_rpc", fake_rpc)
    got = tr.first_outgoing(WALLET)
    assert got["source"] == "node-floor"
    assert got["block"] == 60_000_000 - tr.SEARCH_WINDOW_BLOCKS


def test_the_binary_search_finds_the_block_the_nonce_turned(monkeypatch):
    TURN = 59_500_000
    probes = {"n": 0}

    def fake_rpc(method, params):
        if method == "eth_blockNumber":
            return hex(60_000_000)
        if method == "eth_getTransactionCount":
            probes["n"] += 1
            return "0x1" if int(params[1], 16) >= TURN else "0x0"
        return {"timestamp": hex(int(time.time()) - 12 * 86400)}

    monkeypatch.setattr(tr, "_rpc", fake_rpc)
    got = tr.first_outgoing(WALLET)
    assert got["block"] == TURN and got["source"] == "node"
    # Binary search over 1.8 M blocks is ~21 steps; anything near the block
    # count would mean it degenerated into a scan.
    assert probes["n"] < 30
