"""Stripe errors must keep their real status code instead of collapsing to 500.

Background (journal, live server): every `POST /billing/checkout` with
`currency=eur` and every scanner hit on `/billing/subscription/<junk>` was
logged as `500 Internal Server Error`. Neither is a server fault — the first is
a price/currency mismatch, the second an unknown customer. `billing.py` made
naked Stripe calls, so both landed in main.py's catch-all `Exception` handler.

These tests pin the mapping. They construct Stripe's exception objects directly
and never touch the network or the live account.
"""
from __future__ import annotations

import pytest
import stripe
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.billing import StripeError, stripe_error_handler


def _client_raising(exc: Exception) -> TestClient:
    """A minimal app whose single route raises `exc`, with the handler wired."""
    app = FastAPI()
    app.add_exception_handler(StripeError, stripe_error_handler)

    @app.get("/boom")
    async def boom():
        raise exc

    # raise_server_exceptions=False so the handler's response is returned
    # rather than the exception being re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


def test_unknown_customer_is_404():
    """`resource_missing` means the caller named something that isn't there."""
    exc = stripe.InvalidRequestError(
        "No such customer: 'cust_000000'", "customer", code="resource_missing"
    )
    r = _client_raising(exc).get("/boom")
    assert r.status_code == 404
    assert "No such customer" in r.json()["error"]


def test_currency_mismatch_is_400_and_keeps_the_reason():
    """The real EUR-checkout failure: caller-visible, actionable, not a 500."""
    exc = stripe.InvalidRequestError(
        "The price specified only supports `usd`. "
        "This doesn't match the expected currency: `eur`.",
        "currency",
    )
    r = _client_raising(exc).get("/boom")
    assert r.status_code == 400
    assert "usd" in r.json()["error"]


def test_card_error_is_402():
    exc = stripe.CardError("Your card was declined.", "number", "card_declined")
    r = _client_raising(exc).get("/boom")
    assert r.status_code == 402


def test_rate_limit_is_429():
    r = _client_raising(stripe.RateLimitError("Too many requests")).get("/boom")
    assert r.status_code == 429


def test_our_own_fault_is_502_and_leaks_nothing():
    """Auth/connection failures are ours. The caller gets no configuration detail."""
    exc = stripe.AuthenticationError("Invalid API Key provided: sk_live_abc***")
    r = _client_raising(exc).get("/boom")
    assert r.status_code == 502
    body = r.json()["error"]
    assert body == "Payment provider unavailable"
    assert "sk_live" not in body


def test_handler_is_registered_on_the_real_app():
    """Guard against the registration line being dropped from main.py."""
    from app.main import app as real_app

    assert StripeError in real_app.exception_handlers
