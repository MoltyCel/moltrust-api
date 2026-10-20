"""Public credentials must survive the secret scrubber.

`scrub_secrets` rewrites every string in every response, and a compact JWS looks
exactly like the secrets it is built to catch. Three fields have been silently
destroyed by it so far: the A2A card's `protected` and `signature` on
2026-05-31, and `gate_attestation` on 2026-09-21, which shipped as the
ten-character string `[REDACTED]` where a three-part token belongs — so every
gate rejected it as "not a compact JWS" and the whole feature did nothing.

The pattern is always the same and always invisible in the tests that mattered:
the producing code is correct, the middleware eats the result, and the endpoint
returns 200. So this file asserts on the filter's own behaviour rather than on
any one endpoint.
"""

import pytest

from app.main import _KNOWN_PUBLIC_CREDENTIAL_FIELDS, scrub_secrets

# A real compact JWS shape: three base64url segments. Long random-looking runs
# are what the sensitive-pattern matcher reacts to.
SAMPLE_JWS = (
    "eyJhbGciOiJFZERTQSIsImtpZCI6Im1vbHRydXN0LXJlZ2lzdHJ5LTIwMjYtdjEiLCJ0eXAiOiJKV1QifQ"
    ".eyJ2IjoyLCJkaWQiOiJkaWQ6bW9sdHJ1c3Q6MTU3MjI0MTkwYmUyNDA3MiIsInRydXN0X3Njb3JlIjo3NX0"
    ".c2lnbmF0dXJlLWJ5dGVzLXRoYXQtbG9vay1saWtlLWEtc2VjcmV0LWJ1dC1hcmUtbm90LWF0LWFsbA"
)

PUBLIC_FIELDS = sorted(_KNOWN_PUBLIC_CREDENTIAL_FIELDS)


@pytest.mark.parametrize("field", PUBLIC_FIELDS)
def test_a_public_credential_field_passes_through_untouched(field):
    out = scrub_secrets({field: SAMPLE_JWS})
    assert out[field] == SAMPLE_JWS, (
        f"{field} was rewritten by scrub_secrets — add it to "
        "_KNOWN_PUBLIC_CREDENTIAL_FIELDS, or the value reaches the caller broken"
    )
    assert "[REDACTED]" not in out[field]


@pytest.mark.parametrize("field", PUBLIC_FIELDS)
def test_a_public_credential_survives_nesting(field):
    """Responses nest. The allowlist is keyed on the field name, so a value
    one level down has to be protected too."""
    out = scrub_secrets({"credentials": [{"anchor": {field: SAMPLE_JWS}}]})
    assert out["credentials"][0]["anchor"][field] == SAMPLE_JWS


def test_gate_attestation_is_on_the_list():
    """Named explicitly rather than only covered by the parametrised sweep: if
    someone removes it, this test says which field and why."""
    assert "gate_attestation" in _KNOWN_PUBLIC_CREDENTIAL_FIELDS, (
        "the gate attestation is a compact JWS; scrubbed, it arrives as "
        "[REDACTED] and every gate rejects it as malformed"
    )


def test_the_scrubber_still_scrubs_an_unlisted_field():
    """The allowlist must stay an allowlist. If this passes unredacted, the
    filter has stopped working and the tests above prove nothing.

    The sample is a real SENSITIVE_PATTERNS match — a GitHub token shape.
    An invented secret-looking string is not a control: it would pass here
    for the wrong reason, and the first version of this test did exactly
    that."""
    secret = "ghp_" + "A1b2C3d4" * 5
    out = scrub_secrets({"some_unlisted_field": secret})
    assert "[REDACTED]" in out["some_unlisted_field"], (
        "scrub_secrets let a GitHub token through — the filter is broken, "
        "and the allowlist tests above are meaningless without it"
    )


def test_a_jws_under_an_unlisted_key_is_still_filtered():
    """The same bytes are public under `gate_attestation` and unknown under
    anything else. That asymmetry is the whole point of the list."""
    listed = scrub_secrets({"gate_attestation": SAMPLE_JWS})["gate_attestation"]
    unlisted = scrub_secrets({"attestation": SAMPLE_JWS})["attestation"]
    assert listed == SAMPLE_JWS
    assert unlisted != SAMPLE_JWS, (
        "the 40-character base64 run inside a JWS is what the AWS-secret "
        "pattern matches; under an unlisted key it must still be caught"
    )
