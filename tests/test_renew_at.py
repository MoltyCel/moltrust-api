"""renew_at and the track-record score (ops G1).

Each rule below is tested twice: once with the case that makes it fire, once
with the neighbouring case that has to stay quiet. The second half is the part
that catches a threshold nobody checked — a prefix match that swallows
/credentialsomething, a free price that makes every credential endpoint free,
a decay that starts on day one.

No database and no app import: everything here is a pure function over values
the caller supplies.
"""
import datetime

import pytest

from app import credits, renewal, track_record_score as trs

UTC = datetime.timezone.utc


# --- Where the field appears -------------------------------------------------

@pytest.mark.parametrize("path", [
    "/credentials/issue",
    "/credentials/renew",
    "/identity/verify/did:moltrust:0123456789abcdef",
    "/vc/ipr/stats",
    "/vc/aae/submit",
])
def test_credential_bearing_paths_carry_the_field(path):
    assert renewal.applies_to(path)


@pytest.mark.parametrize("path", [
    "/health",
    "/identity/resolve/did:moltrust:0123456789abcdef",
    "/credits/balance/did:moltrust:0123456789abcdef",
    "/credentialsomething",      # prefix without the slash is a different path
    "/identity/verifyer/x",
    "/vcard",
])
def test_everything_else_stays_quiet(path):
    assert not renewal.applies_to(path)


# --- Whose renewal the answer is about ---------------------------------------

def test_verify_answers_about_the_did_in_the_path():
    assert renewal.subject_of("/identity/verify/did:moltrust:aaaa", "did:moltrust:bbbb") \
        == "did:moltrust:aaaa"


def test_every_other_path_answers_about_the_caller():
    assert renewal.subject_of("/credentials/issue", "did:moltrust:bbbb") == "did:moltrust:bbbb"
    assert renewal.subject_of("/credentials/issue", None) is None
    assert renewal.subject_of("/identity/verify/", "did:moltrust:bbbb") is None


# --- The date itself ---------------------------------------------------------

def test_renew_at_is_the_expiry_minus_the_lead():
    expires = datetime.datetime(2026, 12, 31, 10, 30, 0, tzinfo=UTC)
    assert renewal.renew_at(expires) == "2026-12-01T10:30:00Z"


def test_renew_at_normalises_a_foreign_zone_to_utc():
    berlin = datetime.timezone(datetime.timedelta(hours=2))
    expires = datetime.datetime(2026, 12, 31, 12, 30, 0, tzinfo=berlin)
    assert renewal.renew_at(expires) == "2026-12-01T10:30:00Z"


def test_a_past_date_is_published_as_it_is():
    expires = datetime.datetime(2020, 1, 31, 0, 0, 0, tzinfo=UTC)
    assert renewal.renew_at(expires) == "2020-01-01T00:00:00Z"


def test_no_credential_no_date():
    assert renewal.renew_at(None) is None


# --- Where the date goes in the body -----------------------------------------

def test_a_200_carries_the_date_at_the_top_level():
    out = renewal.rewrite_body(b'{"did":"did:moltrust:aaaa"}', "2026-12-01T10:30:00Z", 200)
    assert out is not None
    assert b'"renew_at": "2026-12-01T10:30:00Z"' in out


def test_a_402_carries_it_in_the_gate_block_under_the_body_s_own_reason():
    import json
    body = b'{"error":"insufficient_credits","detail":"Not enough credits for this call."}'
    out = renewal.rewrite_body(body, "2026-12-01T10:30:00Z", 402)
    assert out is not None
    block = json.loads(out)["moltrustGate"]
    assert block == {"reason": "insufficient_credits", "renew_at": "2026-12-01T10:30:00Z"}


def test_a_subject_without_credentials_gets_the_field_with_no_date():
    import json
    out = renewal.rewrite_body(b'{"did":"did:moltrust:aaaa"}', None, 200)
    assert out is not None
    assert json.loads(out)["renew_at"] is None


def test_a_body_that_already_names_the_date_is_left_alone():
    body = b'{"renew_at":"2027-01-01T00:00:00Z"}'
    assert renewal.rewrite_body(body, "2026-12-01T10:30:00Z", 200) is None


def test_a_json_array_is_passed_through():
    assert renewal.rewrite_body(b'[{"did":"did:moltrust:aaaa"}]', "2026-12-01T10:30:00Z", 200) is None


def test_a_body_that_is_not_json_is_passed_through():
    assert renewal.rewrite_body(b"<html>no</html>", "2026-12-01T10:30:00Z", 200) is None


def test_an_oversized_body_is_passed_through():
    big = b'{"x":"' + b"a" * (renewal.MAX_BODY_BYTES + 1) + b'"}'
    assert renewal.rewrite_body(big, "2026-12-01T10:30:00Z", 200) is None


# --- Renewal costs nothing ---------------------------------------------------

def test_renewal_is_priced_at_zero_and_says_so():
    assert "POST /credentials/renew" in credits.ENDPOINT_COSTS
    assert credits.get_endpoint_cost("POST", renewal.RENEW_PATH) == 0


def test_the_other_credential_endpoints_keep_their_price():
    assert credits.get_endpoint_cost("POST", "/credentials/issue") == 2
    assert credits.get_endpoint_cost("POST", "/credentials/verify") == 2


# --- A renewal call counts as activity ---------------------------------------

def test_a_renewal_counts_towards_activated():
    assert trs.is_independent(renewal.RENEW_PATH)


@pytest.mark.parametrize("endpoint", [
    "/credentials/track-record",
    "/identity/verify/did:moltrust:0123456789abcdef",
    "/identity/bind",
    "/identity/nonce",
    "/skill/trust-score/did:moltrust:0123456789abcdef",
])
def test_the_prescribed_endpoints_do_not(endpoint):
    assert not trs.is_independent(endpoint)


def test_the_prescribed_list_comes_from_the_counting_query():
    patterns = trs.scripted_endpoint_patterns()
    assert "/credentials/track-record%" in patterns
    assert "/identity/verify/%" in patterns
    assert len(patterns) == 6


# --- The growing curve -------------------------------------------------------

def test_tenure_grows_with_the_age_of_the_first_anchor():
    assert trs.tenure_points(0) == 0.0
    assert trs.tenure_points(trs.TENURE_SATURATION_DAYS / 2) == 10.0
    assert trs.tenure_points(trs.TENURE_SATURATION_DAYS) == trs.TENURE_MAX_POINTS
    earlier = [trs.tenure_points(d) for d in (10, 50, 200, 364)]
    assert earlier == sorted(earlier)


def test_tenure_stops_at_the_saturation_point_and_at_zero():
    assert trs.tenure_points(trs.TENURE_SATURATION_DAYS * 3) == trs.TENURE_MAX_POINTS
    assert trs.tenure_points(-5) == 0.0
    assert trs.tenure_points(None) == 0.0


# --- The falling curve -------------------------------------------------------

def test_nothing_falls_inside_the_grace_period():
    assert trs.freshness_factor(0) == 1.0
    assert trs.freshness_factor(trs.DORMANT_AFTER_DAYS) == 1.0


def test_the_fall_starts_after_thirty_days_and_ends_at_a_hundred_and_twenty():
    assert trs.freshness_factor(trs.DORMANT_AFTER_DAYS + 1) < 1.0
    assert trs.freshness_factor(75) == 0.5
    assert trs.freshness_factor(trs.DORMANT_ZERO_DAYS) == 0.0
    assert trs.freshness_factor(trs.DORMANT_ZERO_DAYS + 500) == 0.0


def test_no_independent_call_on_record_is_the_floor():
    assert trs.freshness_factor(None) == 0.0


# --- Both curves together ----------------------------------------------------

def test_the_score_is_tenure_reduced_by_dormancy():
    assert trs.score(trs.TENURE_SATURATION_DAYS, 0) == trs.TENURE_MAX_POINTS
    assert trs.score(trs.TENURE_SATURATION_DAYS, 75) == 10.0
    assert trs.score(trs.TENURE_SATURATION_DAYS, trs.DORMANT_ZERO_DAYS) == 0.0
    assert trs.score(0, 0) == 0.0


def test_the_four_numbers_are_published_with_the_score():
    curve = trs.curve()
    assert curve["tenure_saturation_days"] == trs.TENURE_SATURATION_DAYS
    assert curve["tenure_max_points"] == trs.TENURE_MAX_POINTS
    assert curve["dormant_after_days"] == trs.DORMANT_AFTER_DAYS
    assert curve["dormant_zero_days"] == trs.DORMANT_ZERO_DAYS
