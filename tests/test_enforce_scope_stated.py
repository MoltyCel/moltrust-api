"""/enforce/check says what it does not check, in every response and in its description.

The endpoint verifies no signature and no delegation chain; `ancestors` are compared as
plain JSON. Pages described it as checking "the mandate its principal signed". The
response now carries `scope`, outside the record, so the limit travels with every
PERMIT and the digest stays as it was.
"""
import os

os.environ.setdefault("MOLTRUST_API_KEYS", "ci_placeholder")
os.environ.setdefault("NONCE_SECRET", "x")
os.environ.setdefault("MOLTSTACK_DB_PW", "x")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_x")
os.environ.setdefault("BASESCAN_WEBHOOK_SECRET", "x")

from app import main  # noqa: E402


def test_scope_names_the_chain_as_not_checked():
    s = main._ENFORCE_SCOPE
    assert s["chain_verified"] is False
    joined = " ".join(s["not_checked"])
    for item in ("signature", "validity window", "delegation chain", "evaluated as a root"):
        assert item in joined


def test_the_signed_form_says_what_it_verified():
    s = main._ENFORCE_SCOPE_SIGNED
    assert s["chain_verified"] is True
    assert "revocation" in s["not_checked"]


def test_every_response_carries_scope_outside_the_record():
    import inspect
    src = inspect.getsource(main.enforce_check_endpoint)
    assert '"scope": _ENFORCE_SCOPE_SIGNED if chain else _ENFORCE_SCOPE' in src
    assert src.index('"record"') < src.index('"scope"')


def test_the_openapi_description_states_the_limit():
    op = main.app.openapi()["paths"]["/enforce/check"]["post"]
    assert "nothing about its origin is checked" in op["description"]
    assert "evaluated as a root" in op["description"]
