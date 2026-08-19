"""Tests — verify(): lokale Nachrechnung und MISMATCH-Erkennung.

Das ist der Kernwert des SDK: der Betreiber prueft die Server-Antwort gegen die Eingaben,
statt sie zu uebernehmen.
"""
import httpx
import pytest

from conftest import (
    ADDR, ADDR_VANITY, as_endpoint_payload, broken_transport, core_transport, grant,
    mandate, tx,
)
from moltrust_enforce import DENY, PENDING, PERMIT, core_digest, enforce_check

EXACT_TO = {"type": "exact", "field": "to", "value": ADDR}
RANGE_AMOUNT = {"type": "range", "field": "amount", "lo": 0, "hi": 1000}


# ------------------------------------------------------------------- ehrlicher Server

@pytest.mark.parametrize("disposition,transaction,expected", [
    ("allow", tx(), PERMIT),
    ("allow", tx(to=ADDR_VANITY), DENY),
    ("hold", tx(), PENDING),
    ("forbid", tx(), DENY),
])
def test_verify_agrees_with_an_honest_server(client_factory, disposition, transaction, expected):
    c = client_factory(core_transport())
    m = mandate(grant(disposition, [EXACT_TO]))
    v = c.check(m, transaction)
    assert v.verdict == expected
    r = c.verify(v, m, transaction)
    assert r.ok is True, r.mismatches
    assert r.mismatches == ()
    assert bool(r) is True
    assert r.local.verdict == expected
    assert r.local.core_digest == v.core_digest


def test_verify_accepts_a_raw_dict_response(client_factory):
    c = client_factory(core_transport())
    m, t = mandate(grant("allow", [EXACT_TO])), tx()
    payload = as_endpoint_payload(enforce_check(m, t))
    assert c.verify(payload, m, t).ok is True


def test_verify_follows_the_hash_chain(client_factory):
    c = client_factory(core_transport())
    m = mandate(grant("allow", [RANGE_AMOUNT]))
    first = c.check(m, tx(amount=100))
    second = c.check(m, tx(amount=200), prev_core_digest=first.core_digest)
    # verify liest prev_core_digest aus dem Record, sonst wuerde die Kette nicht zusammenpassen.
    assert c.verify(second, m, tx(amount=200)).ok is True


# ------------------------------------------------------------------------- MISMATCH

def test_mismatch_when_server_flips_the_verdict(client_factory):
    """Der Server behauptet PERMIT, die Eingaben geben DENY her."""
    def flip(payload):
        payload["verdict"] = PERMIT
        return payload

    c = client_factory(core_transport(mutate=flip))
    m, t = mandate(grant("allow", [EXACT_TO])), tx(to=ADDR_VANITY)
    v = c.check(m, t)
    assert v.verdict == PERMIT
    r = c.verify(v, m, t)
    assert r.ok is False
    assert any("verdict mismatch" in s for s in r.mismatches)
    assert r.local.verdict == DENY


def test_mismatch_when_server_flips_the_verdict_inside_the_core(client_factory):
    def flip(payload):
        payload["verdict"] = PERMIT
        payload["record"]["core"]["verdict"] = PERMIT
        return payload

    c = client_factory(core_transport(mutate=flip))
    m, t = mandate(grant("allow", [EXACT_TO])), tx(to=ADDR_VANITY)
    r = c.verify(c.check(m, t), m, t)
    assert r.ok is False
    # Der Core wurde veraendert, der mitgelieferte Digest passt nicht mehr dazu.
    assert any("does not match the core it ships with" in s for s in r.mismatches)


def test_mismatch_when_server_recomputes_a_consistent_but_wrong_record(client_factory):
    """Der schwierigste Fall: der Server luegt sauber — Core und Digest passen zueinander,
    nur eben nicht zu Mandat und Transaktion."""
    def forge(payload):
        core = payload["record"]["core"]
        core["verdict"] = PERMIT
        payload["verdict"] = PERMIT
        payload["record"]["core_digest"] = core_digest(core)   # in sich stimmig
        return payload

    c = client_factory(core_transport(mutate=forge))
    m, t = mandate(grant("allow", [EXACT_TO])), tx(to=ADDR_VANITY)
    v = c.check(m, t)
    assert v.verdict == PERMIT
    r = c.verify(v, m, t)
    assert r.ok is False
    assert any("local recompute disagrees" in s for s in r.mismatches)


def test_mismatch_when_the_digest_is_tampered(client_factory):
    def tamper(payload):
        payload["record"]["core_digest"] = "sha256:" + "0" * 64
        return payload

    c = client_factory(core_transport(mutate=tamper))
    m, t = mandate(grant("allow", [EXACT_TO])), tx()
    r = c.verify(c.check(m, t), m, t)
    assert r.ok is False and len(r.mismatches) >= 1


def test_mismatch_when_the_record_is_missing(client_factory):
    def strip(payload):
        payload["record"] = {}
        return payload

    c = client_factory(core_transport(mutate=strip))
    m, t = mandate(grant("allow", [EXACT_TO])), tx()
    r = c.verify(c.check(m, t), m, t)
    assert r.ok is False
    assert any("carries no record" in s for s in r.mismatches)


def test_mismatch_when_verifying_a_transport_level_deny(client_factory):
    """Ein lokal entstandenes DENY hat keinen Server-Record — es gibt nichts nachzurechnen,
    und das SDK sagt das, statt ein OK vorzutaeuschen."""
    c = client_factory(broken_transport(httpx.ConnectError("down")))
    m, t = mandate(grant("allow", [EXACT_TO])), tx()
    v = c.check(m, t)
    r = c.verify(v, m, t)
    assert r.ok is False
    assert any("produced locally" in s for s in r.mismatches)


def test_verify_against_different_inputs_than_the_record(client_factory):
    """Wer den Record mit anderen Eingaben prueft als der Server sah, bekommt MISMATCH —
    genau so faellt eine nachtraeglich veraenderte Transaktion auf."""
    c = client_factory(core_transport())
    m = mandate(grant("allow", [RANGE_AMOUNT]))
    v = c.check(m, tx(amount=500))
    assert c.verify(v, m, tx(amount=999999)).ok is False
    wider = mandate(grant("allow", [{"type": "range", "field": "amount",
                                     "lo": 0, "hi": 10 ** 9}]))
    assert c.verify(v, wider, tx(amount=500)).ok is False


# --------------------------------------------------------------------- Determinismus

def test_verify_is_deterministic(client_factory):
    c = client_factory(core_transport())
    m, t = mandate(grant("allow", [EXACT_TO, RANGE_AMOUNT])), tx()
    v = c.check(m, t)
    a, b = c.verify(v, m, t), c.verify(v, m, t)
    assert a.ok is b.ok is True
    assert a.local.core_digest == b.local.core_digest
    assert a.local.core == b.local.core
    assert a.mismatches == b.mismatches


def test_verify_needs_no_network(client_factory):
    """verify() rechnet lokal — auch wenn der Transport tot ist."""
    c = client_factory(core_transport())
    m, t = mandate(grant("allow", [EXACT_TO])), tx()
    v = c.check(m, t)
    dead = client_factory(broken_transport(httpx.ConnectError("down")))
    assert dead.verify(v, m, t).ok is True


def test_key_order_does_not_break_verification(client_factory):
    c = client_factory(core_transport())
    m = mandate(grant("allow", [EXACT_TO]))
    t1 = {"action": {"verb": "transfer", "asset": "USDC", "chain": "base"},
          "to": ADDR, "amount": 500, "region": "CH"}
    t2 = {"region": "CH", "amount": 500, "to": ADDR,
          "action": {"chain": "base", "asset": "USDC", "verb": "transfer"}}
    assert c.verify(c.check(m, t1), m, t2).ok is True
