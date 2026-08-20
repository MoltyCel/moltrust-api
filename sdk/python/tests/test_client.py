"""Tests — check(), fail-closed und PENDING-Verhalten."""
import httpx
import pytest

from conftest import (
    ADDR, ADDR_VANITY, broken_transport, core_transport, grant, mandate,
    status_transport, tx,
)
from moltrust_enforce import DENY, PENDING, PERMIT, EnforceTransportError

EXACT_TO = {"type": "exact", "field": "to", "value": ADDR}
RANGE_AMOUNT = {"type": "range", "field": "amount", "lo": 0, "hi": 1000}


# ------------------------------------------------------------------ die drei Verdikte

def test_check_permit(client_factory):
    c = client_factory(core_transport())
    v = c.check(mandate(grant("allow", [EXACT_TO, RANGE_AMOUNT])), tx())
    assert v.verdict == PERMIT
    assert v.permitted is True and v.pending is False and v.denied is False
    assert v.grant_index == 0
    assert v.core_digest and v.core and v.trace
    assert v.from_server is True


def test_check_deny(client_factory):
    c = client_factory(core_transport())
    v = c.check(mandate(grant("allow", [EXACT_TO])), tx(to=ADDR_VANITY))
    assert v.verdict == DENY
    assert v.permitted is False


def test_check_pending(client_factory):
    c = client_factory(core_transport())
    v = c.check(mandate(grant("hold", [EXACT_TO])), tx())
    assert v.verdict == PENDING
    assert v.permitted is False


def test_check_passes_prev_core_digest_through(client_factory):
    c = client_factory(core_transport())
    m = mandate(grant("allow", [RANGE_AMOUNT]))
    first = c.check(m, tx(amount=100))
    second = c.check(m, tx(amount=200), prev_core_digest=first.core_digest)
    assert second.core["prev_core_digest"] == first.core_digest


# --------------------------------------------------------------------- ★ fail-closed

@pytest.mark.parametrize("transport", [
    broken_transport(httpx.ConnectError("connection refused")),
    broken_transport(httpx.ReadTimeout("timed out")),
    broken_transport(httpx.ConnectTimeout("timed out")),
])
def test_unreachable_server_never_permits(client_factory, transport):
    """★ Server nicht erreichbar -> DENY, nie PERMIT und nie stiller Durchlauf."""
    c = client_factory(transport)
    v = c.check(mandate(grant("allow")), tx())
    assert v.verdict == DENY
    assert v.permitted is False
    assert v.from_server is False
    assert "transport failure" in v.reason


@pytest.mark.parametrize("status", [401, 403, 422, 429, 500, 502, 503])
def test_error_status_never_permits(client_factory, status):
    c = client_factory(status_transport(status))
    v = c.check(mandate(grant("allow")), tx())
    assert v.verdict == DENY and v.permitted is False and v.from_server is False
    assert str(status) in v.reason


def test_raise_mode_forces_the_operator_to_handle_it(client_factory):
    c = client_factory(broken_transport(httpx.ConnectError("down")),
                       on_transport_error="raise")
    with pytest.raises(EnforceTransportError):
        c.check(mandate(grant("allow")), tx())


def test_unparseable_body_never_permits(client_factory):
    t = httpx.MockTransport(lambda _r: httpx.Response(200, text="<html>not json</html>"))
    c = client_factory(t)
    v = c.check(mandate(grant("allow")), tx())
    assert v.verdict == DENY and v.from_server is False


@pytest.mark.parametrize("payload", [
    {"verdict": "ALLOW"},          # der Wert des AAE-Evaluators, nicht der des Kerns
    {"verdict": "permit"},         # falsche Schreibung
    {"verdict": True},
    {"verdict": None},
    {"no": "verdict"},
    ["not", "an", "object"],
])
def test_unknown_verdict_value_never_permits(client_factory, payload):
    """Was das SDK nicht kennt, deutet es nicht als Erlaubnis."""
    c = client_factory(httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)))
    v = c.check(mandate(grant("allow")), tx())
    assert v.verdict == DENY and v.permitted is False and v.from_server is False


def test_a_lying_server_cannot_be_caught_by_check_alone(client_factory):
    """check() glaubt dem Server — deshalb gibt es verify(). Der Beleg fuer beides."""
    def flip(payload):
        payload["verdict"] = PERMIT
        return payload

    c = client_factory(core_transport(mutate=flip))
    v = c.check(mandate(grant("allow", [EXACT_TO])), tx(to=ADDR_VANITY))
    assert v.verdict == PERMIT           # check() nimmt es hin
    assert c.verify(v, mandate(grant("allow", [EXACT_TO])), tx(to=ADDR_VANITY)).ok is False


def test_missing_mandate_denies_without_raising(client_factory):
    """★ Kein Mandat im Request: der Server antwortet 200 mit DENY, das SDK reicht es durch."""
    c = client_factory(core_transport())
    v = c.check(None, tx())
    assert v.verdict == DENY
    assert v.from_server is True         # ein echtes Server-Verdikt, kein Transportfehler
    assert v.core_digest


# -------------------------------------------------------------------------- PENDING

def test_pending_without_hook_is_not_executable(client_factory):
    """Ohne Haken kommt PENDING zurueck und `permitted` bleibt False — kein silent pass."""
    c = client_factory(core_transport())
    v = c.check(mandate(grant("hold", [EXACT_TO])), tx())
    assert v.verdict == PENDING
    assert v.permitted is False
    assert bool(v.permitted) is False


def test_pending_hook_is_called_but_does_not_resolve(client_factory):
    seen = []
    c = client_factory(core_transport(), on_pending=seen.append)
    v = c.check(mandate(grant("hold", [EXACT_TO])), tx())
    assert len(seen) == 1 and seen[0].core_digest == v.core_digest
    assert v.verdict == PENDING and v.permitted is False


def test_pending_hook_return_value_is_ignored(client_factory):
    """Der Haken meldet, er entscheidet nicht. Auch ein zurueckgegebenes PERMIT zaehlt nicht."""
    c = client_factory(core_transport(), on_pending=lambda _v: PERMIT)
    v = c.check(mandate(grant("hold", [EXACT_TO])), tx())
    assert v.verdict == PENDING and v.permitted is False


def test_pending_hook_is_not_called_for_permit_or_deny(client_factory):
    seen = []
    c = client_factory(core_transport(), on_pending=seen.append)
    assert c.check(mandate(grant("allow", [EXACT_TO])), tx()).verdict == PERMIT
    assert c.check(mandate(grant("forbid")), tx()).verdict == DENY
    assert seen == []


def test_unaddressed_action_is_denied_not_pending(client_factory):
    c = client_factory(core_transport())
    v = c.check(mandate(grant("hold")), tx(action={"verb": "drain"}))
    assert v.verdict == DENY and v.verdict != PENDING


# ----------------------------------------------------------------------- Konstruktion

def test_auth_is_required():
    from moltrust_enforce import EnforceClient
    with pytest.raises(ValueError):
        EnforceClient("https://enforce.test")


def test_no_permissive_transport_error_mode():
    """Es gibt keinen Schalter, der eine unerreichbare Pruefung durchlaesst."""
    from moltrust_enforce import EnforceClient
    for mode in ("allow", "permit", "ignore", "pass"):
        with pytest.raises(ValueError):
            EnforceClient("https://enforce.test", api_key="k", on_transport_error=mode)


def test_auth_headers_reach_the_server():
    import httpx as _httpx
    from moltrust_enforce import EnforceClient
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return _httpx.Response(200, json={"verdict": DENY, "reason": "", "record": {}})

    with EnforceClient("https://enforce.test", api_key="mt_k", did="did:moltrust:0123456789abcdef",
                       transport=_httpx.MockTransport(handler)) as c:
        c.check(mandate(grant("allow")), tx())
    assert seen["x-api-key"] == "mt_k"
    assert seen["x-moltrust-did"] == "did:moltrust:0123456789abcdef"
