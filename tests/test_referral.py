"""referrer_did and the ReferralCredential (ops G2).

Every rule is tested twice: the case that makes it fire, and the neighbouring
case that has to stay quiet. The second half is the part that catches a rule
nobody checked — a ring that counts anyway, a credential minted twice in an
afternoon, a refusal that names the wrong reason.

No database and no app import: everything here is a pure function over values
the caller supplies. What the database is asked for sits in
`app.referral.FACTS_SQL` and is checked against the live rows in the job report.
"""
import datetime
import json

import pytest

from app import referral

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 16, 18, 30, tzinfo=UTC)


def fact(did="did:moltrust:aaaa", **flags):
    """One referral, as the counting query hands it over."""
    base = {"did": did, "self": False, "mutual": False, "unknown": False,
            "revoked": False, "activated": False}
    base.update(flags)
    return base


# --- A named referrer: taken or refused --------------------------------------

def test_a_registered_referrer_is_taken():
    assert referral.refusal("did:moltrust:bbbb", "did:moltrust:aaaa",
                            exists=True, revoked=False) is None


def test_no_referrer_named_is_no_refusal():
    assert referral.refusal(None, "did:moltrust:aaaa",
                            exists=False, revoked=False) is None
    assert referral.refusal("", "did:moltrust:aaaa",
                            exists=False, revoked=False) is None


def test_its_own_did_is_refused_as_its_own():
    """Not as "unknown": the DID exists, the agent just named itself."""
    assert referral.refusal("did:moltrust:aaaa", "did:moltrust:aaaa",
                            exists=True, revoked=False) == referral.REFUSE_OWN


def test_an_unknown_did_is_refused():
    assert referral.refusal("did:moltrust:bbbb", "did:moltrust:aaaa",
                            exists=False, revoked=False) == referral.REFUSE_UNKNOWN


def test_a_revoked_did_is_refused():
    assert referral.refusal("did:moltrust:bbbb", "did:moltrust:aaaa",
                            exists=True, revoked=True) == referral.REFUSE_REVOKED


def test_own_did_wins_over_unknown():
    """A fresh DID is not in the register yet, so both tests would hit."""
    assert referral.refusal("did:moltrust:aaaa", "did:moltrust:aaaa",
                            exists=False, revoked=False) == referral.REFUSE_OWN


# --- Which referral counts ----------------------------------------------------

def test_one_referral_counts():
    assert referral.counts([fact()]) == {"brought": 1, "activated": 0}


def test_an_activated_referral_counts_in_both_figures():
    assert referral.counts([fact(activated=True)]) == {"brought": 1, "activated": 1}


def test_a_self_referral_does_not_count():
    assert referral.counts([fact(self=True)]) == {"brought": 0, "activated": 0}


def test_a_mutual_pair_does_not_count():
    """A refers B and B refers A: neither side brought anybody."""
    assert referral.counts([fact(mutual=True, activated=True)]) \
        == {"brought": 0, "activated": 0}


def test_a_did_we_never_registered_does_not_count():
    assert referral.counts([fact(unknown=True)]) == {"brought": 0, "activated": 0}


def test_a_revoked_agent_does_not_count():
    """Every other published figure drops a revoked agent; this one too."""
    assert referral.counts([fact(revoked=True, activated=True)]) \
        == {"brought": 0, "activated": 0}


def test_the_counted_rows_are_counted_beside_the_dropped_ones():
    facts = [fact("did:moltrust:1", activated=True),
             fact("did:moltrust:2"),
             fact("did:moltrust:3", mutual=True, activated=True),
             fact("did:moltrust:4", revoked=True),
             fact("did:moltrust:5", self=True)]
    assert referral.counts(facts) == {"brought": 2, "activated": 1}


def test_nobody_brought_is_zero_and_not_an_error():
    assert referral.counts([]) == {"brought": 0, "activated": 0}
    assert referral.counts(None) == {"brought": 0, "activated": 0}


# --- The facts as the query hands them over ----------------------------------

def test_facts_arrive_as_a_list():
    assert referral.facts_of({"referred": [fact()]}) == [fact()]


def test_facts_arrive_as_json_text():
    assert referral.facts_of({"referred": json.dumps([fact()])}) == [fact()]
    assert referral.facts_of({"referred": json.dumps([fact()]).encode()}) == [fact()]


@pytest.mark.parametrize("value", [None, "", "{", "not json", 7, {}])
def test_anything_else_is_no_referral_rather_than_a_crash(value):
    assert referral.facts_of({"referred": value}) == []
    assert referral.facts_of({}) == []


# --- When a credential is due -------------------------------------------------

def test_the_first_referral_gets_a_credential():
    assert referral.should_issue({"brought": 1, "activated": 0}, None, NOW)


def test_an_agent_that_brought_nobody_gets_none():
    assert not referral.should_issue({"brought": 0, "activated": 0}, None, NOW)


def test_unchanged_numbers_issue_nothing():
    last = {"figures": {"brought": 2, "activated": 1},
            "issued_at": NOW - datetime.timedelta(days=4)}
    assert not referral.should_issue({"brought": 2, "activated": 1}, last, NOW)


def test_a_changed_number_issues_again_the_next_day():
    last = {"figures": {"brought": 2, "activated": 1},
            "issued_at": NOW - datetime.timedelta(days=1)}
    assert referral.should_issue({"brought": 3, "activated": 1}, last, NOW)
    assert referral.should_issue({"brought": 2, "activated": 2}, last, NOW)


def test_at_most_one_a_day():
    """Five registrations in an afternoon are one credential, not five."""
    last = {"figures": {"brought": 2, "activated": 1},
            "issued_at": NOW.replace(hour=1, minute=5)}
    assert not referral.should_issue({"brought": 6, "activated": 1}, last, NOW)


def test_the_day_is_a_utc_day_and_a_stored_naive_time_is_utc():
    """credentials.issued_at is written from utcnow(), so it arrives naive."""
    last = {"figures": {"brought": 1, "activated": 0},
            "issued_at": datetime.datetime(2026, 10, 16, 23, 59)}
    assert not referral.should_issue({"brought": 2, "activated": 0}, last, NOW)
    earlier = {"figures": {"brought": 1, "activated": 0},
               "issued_at": datetime.datetime(2026, 10, 15, 23, 59)}
    assert referral.should_issue({"brought": 2, "activated": 0}, earlier, NOW)


def test_a_credential_we_cannot_read_is_treated_as_no_figures():
    """An unreadable stored document must not freeze the number it stated."""
    last = {"figures": None, "issued_at": NOW - datetime.timedelta(days=2)}
    assert referral.should_issue({"brought": 1, "activated": 0}, last, NOW)


# --- The numbers a stored credential states ----------------------------------

def _vc(brought=2, activated=1):
    return {"credentialSubject": {"id": "did:moltrust:aaaa",
                                  "referrals": {"brought": brought,
                                                "activated": activated}}}


def test_the_figures_are_read_back_from_the_stored_document():
    assert referral.figures_of(_vc()) == {"brought": 2, "activated": 1}
    assert referral.figures_of(json.dumps(_vc())) == {"brought": 2, "activated": 1}
    assert referral.figures_of(json.dumps(_vc()).encode()) == {"brought": 2, "activated": 1}


@pytest.mark.parametrize("value", [
    None, "", "{", 7, {}, {"credentialSubject": {}},
    {"credentialSubject": {"referrals": "two"}},
    {"credentialSubject": {"referrals": {"brought": 1}}},
    {"credentialSubject": {"referrals": {"brought": "zwei", "activated": 1}}},
])
def test_anything_else_reads_as_no_figures(value):
    assert referral.figures_of(value) is None


# --- What the credential says -------------------------------------------------

def test_the_claims_carry_both_numbers_the_cut_and_the_rule():
    c = referral.claims({"brought": 3, "activated": 2}, NOW)["referrals"]
    assert (c["brought"], c["activated"]) == (3, 2)
    assert c["as_of"] == "2026-10-16T18:30:00Z"
    assert c["counting_rule"] == referral.COUNTING_RULE
    assert "mutual" in c["counting_rule"]


def test_the_cut_is_published_in_utc():
    berlin = datetime.timezone(datetime.timedelta(hours=2))
    c = referral.claims({"brought": 1, "activated": 0},
                        datetime.datetime(2026, 10, 16, 20, 30, tzinfo=berlin))
    assert c["referrals"]["as_of"] == "2026-10-16T18:30:00Z"


def test_a_naive_cut_is_read_as_utc_and_not_as_zurich():
    """Everything that writes a time here writes utcnow()."""
    c = referral.claims({"brought": 1, "activated": 0},
                        datetime.datetime(2026, 10, 16, 23, 30))
    assert c["referrals"]["as_of"] == "2026-10-16T23:30:00Z"


def test_the_credential_type_is_named_once():
    assert referral.CREDENTIAL_TYPE == "ReferralCredential"
