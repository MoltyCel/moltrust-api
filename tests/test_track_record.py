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


# --- computed block timestamps ---------------------------------------------
#
# The block fetch was 12.7 KB read for one field, and the largest response in a
# cold search by an order of magnitude. Base holds two seconds a block, measured
# 2026-09-26 over 1.5 M blocks with zero drift, so the timestamp is computed and
# the model is re-checked against the head block once an hour.

def _reset_model(monkeypatch, ok=False, checked=0.0):
    monkeypatch.setattr(tr, "_block_model_checked_at", checked, raising=False)
    monkeypatch.setattr(tr, "_block_model_ok", ok, raising=False)


def test_the_reference_block_predicts_itself():
    ref = tr.BLOCK_TIME_REFERENCE
    assert tr.predicted_timestamp(ref["block"]) == ref["ts"]
    assert tr.predicted_timestamp(ref["block"] + 1) == ref["ts"] + tr.BASE_BLOCK_SECONDS
    assert tr.predicted_timestamp(ref["block"] - 30) == ref["ts"] - 60


def test_the_model_is_checked_against_the_head_and_then_remembered(monkeypatch):
    _reset_model(monkeypatch)
    calls = {"n": 0}

    def fake_rpc(method, params):
        if method == "eth_getBlockByNumber":
            calls["n"] += 1
            return {"timestamp": hex(tr.predicted_timestamp(int(params[0], 16)))}
        return None

    monkeypatch.setattr(tr, "_rpc", fake_rpc)
    head = tr.BLOCK_TIME_REFERENCE["block"] + 1_000_000
    assert tr.block_model_holds(head) is True
    assert tr.block_model_holds(head) is True
    assert tr.block_model_holds(head) is True
    # One check serves every search inside the TTL. Checking per search would
    # trade the fetch this replaced for an identical one.
    assert calls["n"] == 1


def test_drift_inside_one_block_is_tolerated_and_beyond_it_is_not(monkeypatch):
    head = tr.BLOCK_TIME_REFERENCE["block"] + 500
    for drift, expected in ((0, True), (2, True), (-2, True), (3, False), (-9, False)):
        _reset_model(monkeypatch)
        monkeypatch.setattr(tr, "_rpc", lambda m, p, d=drift: (
            {"timestamp": hex(tr.predicted_timestamp(int(p[0], 16)) + d)}
            if m == "eth_getBlockByNumber" else None))
        assert tr.block_model_holds(head) is expected, f"drift {drift}"


def test_a_broken_model_falls_back_to_asking(monkeypatch):
    _reset_model(monkeypatch)
    head = tr.BLOCK_TIME_REFERENCE["block"] + 500
    target = head - 100
    real = tr.predicted_timestamp(target) + 99_999

    def fake_rpc(method, params):
        if method != "eth_getBlockByNumber":
            return None
        b = int(params[0], 16)
        # The head is far off the model; the target answers with its real value.
        return {"timestamp": hex(tr.predicted_timestamp(b) + (99_999 if b == head else 0))
                if b == head else hex(real)}

    monkeypatch.setattr(tr, "_rpc", fake_rpc)
    assert tr.block_model_holds(head) is False
    assert tr.timestamp_of(target, head) == real


def test_an_unreachable_node_does_not_declare_the_model_broken(monkeypatch):
    """No answer is not a refutation; keep the last verdict and retry later."""
    _reset_model(monkeypatch, ok=True, checked=0.0)
    monkeypatch.setattr(tr, "_rpc", lambda m, p: None)
    assert tr.block_model_holds(tr.BLOCK_TIME_REFERENCE["block"]) is True


# --- the threshold at one block either side --------------------------------

def _age_at(ts):
    return tr._age_days_from_ts(ts)


def test_the_age_threshold_flips_within_one_block(monkeypatch):
    """Seven days is the line, and one block is two seconds across it."""
    now = int(time.time())
    monkeypatch.setattr(tr, "wallet_nonce", lambda w: 1)
    exactly = now - tr.MIN_AGE_DAYS * 86400          # age == 7 exactly

    assert _age_at(exactly) == tr.MIN_AGE_DAYS
    assert _age_at(exactly - tr.BASE_BLOCK_SECONDS) == tr.MIN_AGE_DAYS      # one block older
    assert _age_at(exactly + tr.BASE_BLOCK_SECONDS) == tr.MIN_AGE_DAYS - 1  # one block newer

    import app.cold_start as cs
    monkeypatch.setattr(cs, "fetch_blockscout_wallet", lambda w: None)

    tr.check_eligible(tr.measure(WALLET, first_tx={"block": 1, "ts": exactly,
                                                   "source": "node"}))
    tr.check_eligible(tr.measure(WALLET, first_tx={
        "block": 1, "ts": exactly - tr.BASE_BLOCK_SECONDS, "source": "node"}))
    with pytest.raises(NotEligible) as exc:
        tr.check_eligible(tr.measure(WALLET, first_tx={
            "block": 1, "ts": exactly + tr.BASE_BLOCK_SECONDS, "source": "node"}))
    assert "6 days old" in str(exc.value)
