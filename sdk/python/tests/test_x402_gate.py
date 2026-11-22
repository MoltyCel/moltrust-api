"""Receiver-side gate for x402 payments (`moltrust_enforce.x402_gate`).

Covers the cases (a)-(e) through the shared vectors, the derivation rule, the absence of
any network call in the check path, and the agent-to-hook flow end to end.
"""
from __future__ import annotations

import copy
import json
import pathlib
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

import x402_vector_source as src
from moltrust_enforce import EnforceClient, enforce_check
from moltrust_enforce.x402_gate import (
    Abort, ReceiptError, build_receipt, build_x402_headers, check_payment,
    decode_payment_header, mandate_digest, require_moltrust_verdict, trust_mandates,
    x402_transaction,
)

VECTORS = pathlib.Path(__file__).resolve().parent / "x402-vectors.json"


def _load():
    return json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a connection or resolve a name fails the test."""
    def refuse(*a, **k):
        raise AssertionError("network call in the check path")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield


# --------------------------------------------------------------------------- vectors

def test_vector_file_matches_its_source():
    assert VECTORS.read_text(encoding="utf-8") == src.dump(src.build()), \
        "x402-vectors.json is stale: run python tests/x402_vector_source.py"


def test_vectors_cover_every_case():
    cases = {v["case"] for v in _load()}
    assert {"a", "b", "c", "d", "e"} <= cases
    b = {v["id"] for v in _load() if v["case"] == "b"}
    assert b == {"b-other-amount", "b-other-payee", "b-other-asset", "b-other-network",
                 "b-other-nonce"}
    assert [v["id"] for v in _load() if v["expect"] == "pass"] == ["e-bound-allow"]


@pytest.mark.parametrize("v", _load(), ids=lambda v: v["id"])
def test_vector(v, no_network):
    accept = trust_mandates(*v["accepted_mandate_digests"])
    d = check_payment(v["payment_payload"], v["requirements"], accept_mandate=accept)
    assert d.allowed is (v["expect"] == "pass"), d
    assert d.reason == v["reason"], d


@pytest.mark.parametrize("v", _load(), ids=lambda v: v["id"])
def test_vector_through_hook(v, no_network):
    hook = require_moltrust_verdict(accept_mandate=trust_mandates(*v["accepted_mandate_digests"]))
    ctx = SimpleNamespace(payment_payload=v["payment_payload"], requirements=v["requirements"])
    out = hook(ctx)
    if v["expect"] == "pass":
        assert out is None
    else:
        assert out is not None and out.reason.startswith(f"moltrust:{v['reason']}")


# --------------------------------------------------------------------------- derivation

def test_transaction_comes_from_the_payment_not_the_receipt():
    pay = src.payment()
    tx = x402_transaction(pay)
    assert tx == {
        "action": {"verb": "x402.pay", "scheme": "exact", "network": src.NETWORK,
                   "asset": src.USDC},
        "from": src.PAYER, "to": src.PAY_TO, "amount": 10000, "nonce": src.NONCE,
    }
    # A transaction smuggled next to the receipt changes nothing.
    receipt = src.receipt_for(src.mandate(), pay)
    receipt["transaction"] = {"action": {}, "to": src.OTHER, "amount": 1}
    d = check_payment(src.with_receipt(pay, receipt), src.requirements(),
                      accept_mandate=trust_mandates(mandate_digest(src.mandate())))
    assert d.allowed and d.transaction == tx


def test_addresses_and_nonce_are_lowercased():
    pay = src.payment()
    pay["payload"]["authorization"]["to"] = src.PAY_TO.upper().replace("0X", "0x")
    pay["accepted"]["payTo"] = pay["payload"]["authorization"]["to"]
    pay["payload"]["authorization"]["nonce"] = src.NONCE.upper().replace("0X", "0x")
    tx = x402_transaction(pay)
    assert tx["to"] == src.PAY_TO and tx["nonce"] == src.NONCE


@pytest.mark.parametrize("bad", [None, "x", 7, [], {"x402Version": 2}])
def test_unreadable_payload_aborts(bad):
    d = check_payment(bad, accept_mandate=lambda *_: True)
    assert not d.allowed and d.reason == "payment_unreadable"


def test_accept_mandate_raising_aborts():
    def boom(*_):
        raise RuntimeError("store down")
    pay = src.with_receipt(src.payment(), src.receipt_for(src.mandate(), src.payment()))
    d = check_payment(pay, accept_mandate=boom)
    assert not d.allowed and d.reason == "check_failed"


def test_accept_mandate_truthy_non_bool_is_not_acceptance():
    pay = src.with_receipt(src.payment(), src.receipt_for(src.mandate(), src.payment()))
    d = check_payment(pay, accept_mandate=lambda *_: "yes")
    assert not d.allowed and d.reason == "mandate_not_accepted"


def test_hook_requires_accept_mandate():
    with pytest.raises(TypeError):
        require_moltrust_verdict(accept_mandate=None)
    with pytest.raises(ValueError):
        trust_mandates("not-a-digest")


def test_hook_survives_a_broken_context_and_logger():
    seen = []
    hook = require_moltrust_verdict(accept_mandate=lambda *_: True,
                                    on_decision=lambda d: (seen.append(d), 1 / 0))
    out = hook(object())
    assert out is not None and out.reason.startswith("moltrust:payment_unreadable")
    assert seen and not seen[0].allowed


def test_pydantic_like_model_is_read_by_alias():
    pay = src.with_receipt(src.payment(), src.receipt_for(src.mandate(), src.payment()))

    class Model:
        def model_dump(self, by_alias=False, exclude_none=False):
            assert by_alias
            return copy.deepcopy(pay)

    d = check_payment(Model(), accept_mandate=trust_mandates(mandate_digest(src.mandate())))
    assert d.allowed


# --------------------------------------------------------------------------- offline

def test_check_path_imports_no_http_client():
    code = ("import sys; import moltrust_enforce.x402_gate as g; "
            "print(','.join(m for m in ('httpx', 'requests', 'urllib3', 'ssl') "
            "if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == ""


# --------------------------------------------------------------------------- agent side

def _server_response(mandate, transaction):
    """The JSON body POST /enforce/check returns, produced by the same core."""
    rec = enforce_check(mandate, transaction)
    return {"verdict": rec["verdict"], "reason": rec["reason"],
            "grant_index": rec["grant_index"], "trace": rec["trace"],
            "record": {"core": rec["core"], "core_digest": rec["core_digest"]}}


def test_end_to_end_agent_to_hook(no_network):
    mandate = src.mandate()
    pay = src.payment()
    # Agent: derive the transaction from its own payment, ask /enforce/check, build headers.
    response = _server_response(mandate, x402_transaction(pay))
    headers = build_x402_headers(mandate, pay, response)
    assert set(headers) == {"PAYMENT-SIGNATURE"}
    # Receiver: decode the header as the x402 server does, run the hook.
    hook = require_moltrust_verdict(accept_mandate=trust_mandates(mandate_digest(mandate)))
    ctx = SimpleNamespace(payment_payload=decode_payment_header(headers["PAYMENT-SIGNATURE"]),
                          requirements=src.requirements())
    assert hook(ctx) is None
    # The same headers on a different payment: aborted.
    moved = decode_payment_header(headers["PAYMENT-SIGNATURE"])
    moved["accepted"]["payTo"] = moved["payload"]["authorization"]["to"] = src.OTHER
    out = hook(SimpleNamespace(payment_payload=moved,
                               requirements=src.requirements(pay_to=src.OTHER)))
    assert out.reason.startswith("moltrust:receipt_not_bound")


def test_end_to_end_with_client_verdict_object(no_network):
    import httpx

    mandate, pay = src.mandate(), src.payment()

    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(200, json=_server_response(body["mandate"], body["transaction"]))

    client = EnforceClient("https://enforce.test", api_key="k", auto_register=False,
                           transport=httpx.MockTransport(handler))
    verdict = client.check(mandate, x402_transaction(pay))
    headers = build_x402_headers(mandate, pay, verdict)
    d = check_payment(decode_payment_header(headers["PAYMENT-SIGNATURE"]), src.requirements(),
                      accept_mandate=trust_mandates(mandate_digest(mandate)))
    assert d.allowed


@pytest.mark.parametrize("disp", ["hold", "forbid"])
def test_agent_helper_refuses_a_receipt_that_would_be_rejected(disp):
    m, pay = src.mandate(disp), src.payment()
    with pytest.raises(ReceiptError):
        build_receipt(m, pay, _server_response(m, x402_transaction(pay)))


def test_agent_helper_refuses_a_response_for_another_payment():
    m = src.mandate()
    other = _server_response(m, x402_transaction(src.payment(amount="20000")))
    with pytest.raises(ReceiptError):
        build_x402_headers(m, src.payment(), other)


def test_agent_helper_keeps_existing_extensions():
    m, pay = src.mandate(), src.payment()
    pay["extensions"] = {"bazaar": {"x": 1}}
    headers = build_x402_headers(m, pay, _server_response(m, x402_transaction(pay)))
    ext = decode_payment_header(headers["PAYMENT-SIGNATURE"])["extensions"]
    assert set(ext) == {"bazaar", "moltrust"}


def test_x402_sdk_abort_result_when_installed():
    schemas = pytest.importorskip("x402.schemas")
    hook = require_moltrust_verdict(accept_mandate=lambda *_: True)
    assert isinstance(hook(SimpleNamespace(payment_payload={}, requirements=None)),
                      schemas.AbortResult)


def test_network_guard_bites(no_network):
    """Negative check for the fixture itself: a connection attempt fails the test."""
    with pytest.raises(AssertionError, match="network call"):
        socket.create_connection(("api.moltrust.ch", 443), timeout=1)
    with pytest.raises(AssertionError, match="network call"):
        socket.getaddrinfo("api.moltrust.ch", 443)


def test_real_x402_models_when_installed(no_network):
    payments = pytest.importorskip("x402.schemas")
    m, pay = src.mandate(), src.payment()
    headers = build_x402_headers(m, pay, _server_response(m, x402_transaction(pay)))
    model = payments.PaymentPayload.model_validate(
        decode_payment_header(headers["PAYMENT-SIGNATURE"]))
    reqs = payments.PaymentRequirements.model_validate(src.requirements())
    hook = require_moltrust_verdict(accept_mandate=trust_mandates(mandate_digest(m)))
    assert hook(SimpleNamespace(payment_payload=model, requirements=reqs)) is None
    model.accepted.amount = "20000"
    model.payload["authorization"]["value"] = "20000"
    reqs.amount = "20000"
    out = hook(SimpleNamespace(payment_payload=model, requirements=reqs))
    assert isinstance(out, payments.AbortResult)
    assert out.reason.startswith("moltrust:receipt_not_bound")


def test_standalone_abort_without_x402(monkeypatch):
    monkeypatch.setitem(sys.modules, "x402", None)
    monkeypatch.setitem(sys.modules, "x402.schemas", None)
    hook = require_moltrust_verdict(accept_mandate=lambda *_: True)
    out = hook(SimpleNamespace(payment_payload={}, requirements=None))
    assert type(out) is Abort and out.reason.startswith("moltrust:")


def test_readme_example_runs_green():
    script = pathlib.Path(__file__).resolve().parents[1] / "examples" / "x402_receiver_gate" \
        / "run_example.py"
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "all expectations held" in out.stdout
