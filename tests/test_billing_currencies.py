"""The advertised currency list and the accepted one must be the same list."""
from app.billing import SUPPORTED_CURRENCIES, TIERS


def test_usd_only():
    """The v2 Stripe catalogue resolves prices by lookup_key, and every
    lookup_key in TIERS is a USD price. The single live EUR price has no
    lookup_key, so an EUR checkout can only end in a 400."""
    assert SUPPORTED_CURRENCIES == ("usd",)


def test_every_tier_has_a_lookup_key():
    for name, tier in TIERS.items():
        assert tier.get("lookup_key"), f"{name} has no lookup_key to resolve a price by"


def test_advertised_and_accepted_cannot_drift():
    """/plans built its list inline and the checkout guard built another.
    Two literals for one fact is how they came to disagree."""
    import inspect

    import app.billing as billing

    source = inspect.getsource(billing)
    assert '"currencies": ["usd", "eur"]' not in source
    assert 'not in {"usd", "eur"}' not in source
