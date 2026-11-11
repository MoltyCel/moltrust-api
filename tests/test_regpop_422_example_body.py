"""The 402 invitation carries a body to copy, for the two reasons agents fail on.

ops REG-POP-422. The handler from #994 wrote the reason of every 422 on
/identity/register-pop into the journal, and over 16.10.-30.10.2026 the 543
refusals came apart into exactly two classes that a real client produced:

* 29 calls with no body at all -> 422 ``body:missing``
* 2 calls with a ``public_key`` in base64 instead of hex -> 422 ``value_error``
  on ``body.public_key`` (44 characters, a 32-byte key in padded base64). Both
  had sent all four fields, held a challenge and signed it; they failed on the
  encoding alone.

The remaining 490 are crawlers posting an empty object. Nothing in the hint is
aimed at them, and a hint cannot reach them anyway.

Both classes are shape errors, so the fix is a body to copy in the invitation
rather than a change in the endpoint. These tests hold three things: the
invitation carries that body, the body gets past the shape gate (or it would
teach the error it is meant to prevent), and the two reasons stay nameable in
the journal — if a later edit stops naming them, the next look back at the 422
cannot tell these classes apart again.
"""
import logging

from fastapi.testclient import TestClient

from app.main import PopRegisterRequest, app
from app.register_hint import EXAMPLE_BODY, HINT_OBJECT, with_hint

PATH = "/identity/register-pop"
REQUIRED = ("public_key", "challenge", "signature", "pow_nonce")

# No context manager: no startup event, no database. Every call here is refused
# in body validation, before any endpoint code could need one.
client = TestClient(app)


def _pop_step(hint):
    step = hint["register_hint"]["steps"][0]
    assert step["path"] == PATH, step
    return step


def _reason(caplog):
    lines = [r.getMessage() for r in caplog.records
             if r.name == "moltrust" and r.getMessage().startswith(f"422 on POST {PATH}")]
    assert len(lines) == 1, lines
    field = lines[0].split("reason=", 1)[1]
    return field.split(" ", 1)[0]


def test_402_invitation_carries_the_example_body():
    hint = with_hint({"error": "insufficient_credits"}, source="402-credits")
    step = _pop_step(hint)

    assert step["example_body"] == EXAMPLE_BODY
    for field in REQUIRED:
        assert field in step["example_body"], field
    # The challenge is not inventable; the body has to say where it comes from.
    assert step["challenge_from"] == "GET /identity/register-challenge"


def test_the_invitation_names_both_reasons_a_real_agent_hit():
    refused = " ".join(_pop_step(with_hint({"error": "x"}))["refused_when"])
    assert "no body" in refused
    assert "base64" in refused
    assert "64 hex" in refused


def test_example_public_key_is_64_hex_characters():
    key = EXAMPLE_BODY["public_key"]
    assert len(key) == 64, key
    assert all(c in "0123456789abcdef" for c in key), key
    # 44 is the length that produced the two real refusals. A hint that shipped
    # one would teach the error it exists to prevent.
    assert len(key) != 44


def test_example_body_gets_past_the_shape_gate():
    """No ValidationError: copying it cannot produce either of the two reasons.

    It is still refused further in — the challenge and signature are
    placeholders — but by the endpoint and with a reason of its own.
    """
    parsed = PopRegisterRequest(**EXAMPLE_BODY)
    assert parsed.public_key == EXAMPLE_BODY["public_key"]


def test_a_base64_public_key_is_still_refused_and_named(caplog):
    # 44 characters, the form the two agents sent.
    body = dict(EXAMPLE_BODY, public_key="Rpxbnzo88Sl" + "c0zvdvLQPwDrY4WP0QyGPrE+t8JUe5l8=")
    assert len(body["public_key"]) == 44

    with caplog.at_level(logging.INFO, logger="moltrust"):
        resp = client.post(PATH, json=body)

    assert resp.status_code == 422
    assert _reason(caplog) == "public_key:value_error"
    # The refusal says what the right encoding is, not just that this is wrong.
    assert "hex" in resp.text


def test_a_call_with_no_body_is_still_refused_and_named(caplog):
    with caplog.at_level(logging.INFO, logger="moltrust"):
        resp = client.post(PATH)

    assert resp.status_code == 422
    assert _reason(caplog) == "body:missing"


def test_the_hint_object_itself_is_not_mutated_by_with_hint():
    """The example body is shared state; a caller must not be able to edit it."""
    before = HINT_OBJECT["steps"][0]["example_body"]["public_key"]
    hint = with_hint({"message": "something"}, source="402-credits")
    _pop_step(hint)["example_body"]["public_key"] = "tampered"
    assert HINT_OBJECT["steps"][0]["example_body"]["public_key"] == before
