"""The free tier's two allowances, and the boundary /plans advertises.

The SQL itself is exercised against a real Postgres; these tests pin the numbers
and the shapes that the pricing page and the API have to agree on.
"""
from app.billing import FREE_TIER, TIERS
from app.credits import ENDPOINT_COSTS
from app.free_tier import FREE_CALLS_PER_HOUR, FREE_MONTHLY_FLOOR


def test_free_tier_is_not_a_checkout_tier():
    """Everything in TIERS resolves to a Stripe lookup_key. "free" has nothing
    to resolve, so it must not be reachable from the checkout path."""
    assert "free" not in TIERS
    assert "lookup_key" not in FREE_TIER


def test_plans_and_code_report_the_same_numbers():
    """One fact, one source. /billing/plans quoting its own literals is how
    the pricing page and the middleware would come to disagree."""
    assert FREE_TIER["free_calls_per_hour"] == FREE_CALLS_PER_HOUR
    assert FREE_TIER["monthly_credit_floor"] == FREE_MONTHLY_FLOOR


def test_first_credential_issuance_is_free():
    """An agent should be able to hold the thing it came for before deciding
    whether to pay for more of them."""
    assert FREE_TIER["first_credential_issuance_free"] is True


def test_issuance_keeps_its_price_for_everyone_else():
    """The free first one is a middleware bypass, not a price change — the
    second issuance must still cost what the table says."""
    assert ENDPOINT_COSTS["POST /credentials/issue"] == 2


def test_the_floor_does_not_stack():
    """A floor that accumulated would let an idle key be parked for a year and
    then cashed in. The flag is part of the published contract."""
    assert FREE_TIER["floor_stacks"] is False


def test_both_signup_paths_are_advertised():
    assert set(FREE_TIER["signup"]) == {"email", "did_signature"}


def test_signup_by_signature_costs_nothing():
    """A signup that charged credits could never be a caller's first call."""
    assert ENDPOINT_COSTS["POST /auth/signup-did"] == 0
    assert ENDPOINT_COSTS["POST /auth/signup"] == 0


def test_boundary_is_stated_in_both_directions():
    """The line the pricing page draws has to survive a machine reader: say
    what is free AND what is not, or it reads as 'everything is free'."""
    boundary = FREE_TIER["boundary"].lower()
    assert "free" in boundary and "paid" in boundary


def test_the_allowance_covers_reading_not_producing():
    """Sixty free calls an hour on every priced endpoint would have made
    issuance, compliance assessment and anchoring free — the whole paid rail.
    The allowance covers the self-use half only."""
    from app.free_tier import covered_by_free_tier

    for read in ("GET /identity/verify/{did}", "POST /credentials/verify",
                 "GET /reputation/query/{did}", "GET /a2a/agent-card/{did}"):
        assert covered_by_free_tier(read), read

    for produce in ("POST /credentials/issue", "POST /compliance/assess",
                    "POST /anchors/batch", "POST /delegation/create",
                    "POST /identity/register", "POST /reputation/rate"):
        assert not covered_by_free_tier(produce), produce


def test_every_free_tier_endpoint_is_one_that_costs_something():
    """An entry for a free endpoint is dead weight and hides a typo — the
    allowance can only ever matter where there is a price to waive."""
    from app.free_tier import FREE_TIER_ENDPOINTS

    for key in FREE_TIER_ENDPOINTS:
        assert ENDPOINT_COSTS.get(key, 0) > 0, f"{key} is not a priced endpoint"
