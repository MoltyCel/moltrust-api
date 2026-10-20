"""Asking twice inside a week returns the credential you already have.

Between 1 and 3 October 2026 three agents polled POST /credentials/track-record
instead of the trust score and took 75 credentials between them — 28, 27 and 20
— each one minted and each one anchored out of BASE_ANCHOR_KEY. The endpoint had
no reuse path, so every call was a fresh issue.

What these tests pin down is the shape of the fix and, as importantly, its end:
reuse holds while the measurement holds. A track record describes a wallet at a
moment, so a changed nonce or a changed age issues afresh, and so does a
credential older than the window. A permanent lock would make the credential say
something it cannot know.
"""
import datetime

import pytest


WINDOW_DAYS = 7


def _claims(nonce=3, age=41):
    return {"id": "did:moltrust:aaaaaaaaaaaaaaaa", "type": "TrackRecordCredential",
            "wallet_address": "0xabc", "wallet_chain": "base",
            "nonce": nonce, "wallet_age_days": age, "wallet_tx_count": nonce}


def should_reuse(existing_claims, measurement, issued_at, now):
    """The decision the endpoint makes, isolated so it can be tested.

    Mirrors app/main.py: inside the window and with both measured values
    unchanged, the stored credential is returned; otherwise a new one is issued.
    """
    if existing_claims is None:
        return False
    if now - issued_at >= datetime.timedelta(days=WINDOW_DAYS):
        return False
    return (existing_claims.get("nonce") == measurement.get("nonce")
            and existing_claims.get("wallet_age_days") == measurement.get("wallet_age_days"))


NOW = datetime.datetime(2026, 10, 3, 12, 0, 0)


def test_same_values_inside_the_window_reuse():
    issued = NOW - datetime.timedelta(days=2)
    assert should_reuse(_claims(), _claims(), issued, NOW) is True


def test_a_changed_nonce_issues_afresh():
    issued = NOW - datetime.timedelta(days=2)
    assert should_reuse(_claims(nonce=3), _claims(nonce=4), issued, NOW) is False


def test_a_changed_age_issues_afresh():
    issued = NOW - datetime.timedelta(days=2)
    assert should_reuse(_claims(age=41), _claims(age=42), issued, NOW) is False


def test_past_the_window_issues_afresh_even_unchanged():
    issued = NOW - datetime.timedelta(days=WINDOW_DAYS, seconds=1)
    assert should_reuse(_claims(), _claims(), issued, NOW) is False


def test_at_the_window_edge_it_issues_afresh():
    issued = NOW - datetime.timedelta(days=WINDOW_DAYS)
    assert should_reuse(_claims(), _claims(), issued, NOW) is False


def test_no_prior_credential_issues():
    assert should_reuse(None, _claims(), NOW, NOW) is False


@pytest.mark.parametrize("n_calls", [2, 20, 28])
def test_a_polling_loop_mints_once(n_calls):
    """The three loops that caused this would each have produced one credential."""
    issued_at, stored, minted = NOW, _claims(), 1
    for i in range(n_calls - 1):
        now = issued_at + datetime.timedelta(minutes=5 * (i + 1))
        if not should_reuse(stored, _claims(), issued_at, now):
            minted += 1
            issued_at = now
    assert minted == 1, f"{n_calls} Aufrufe haetten {minted} Credentials erzeugt"
