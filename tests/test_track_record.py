"""Track-record credentials: the threshold, the claims, and the gate field.

No database and no network. The three things under test are decisions, and a
decision that needs a live chain to check is a decision nobody can review.
"""

import pytest

from app.signature import build_gate_payload
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
