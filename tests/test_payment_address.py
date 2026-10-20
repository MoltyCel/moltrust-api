"""BASE_ADDR is the one source of the receiving address, read at every use.

The point of S2 (ops backlog, 2026-10-12): moving the wallet is a change to
~/.moltrust_secrets and a restart, not a commit. The tests below change the
variable inside one process and check that every reader follows without a
reload — the same thing a restart does with a new secrets file.
"""
import importlib
import pathlib
import re
import sys

import pytest

from app.payment_address import PaymentAddressNotConfigured, payment_address

A = "0x" + "a1" * 20
B = "0x" + "b2" * 20
REPO = pathlib.Path(__file__).resolve().parent.parent


def test_reads_the_variable(monkeypatch):
    monkeypatch.setenv("BASE_ADDR", A)
    assert payment_address() == A


def test_a_change_takes_effect_without_reload(monkeypatch):
    monkeypatch.setenv("BASE_ADDR", A)
    assert payment_address() == A
    monkeypatch.setenv("BASE_ADDR", B)
    assert payment_address() == B


@pytest.mark.parametrize("value", ["", "   ", "0x1234", "D63f988201A0Bd184deaB7D4e97b1717B25a5800",
                                   "0x" + "g" * 40])
def test_missing_or_malformed_is_an_error_not_a_default(monkeypatch, value):
    monkeypatch.setenv("BASE_ADDR", value)
    with pytest.raises(PaymentAddressNotConfigured):
        payment_address()


def test_unset_is_an_error(monkeypatch):
    monkeypatch.delenv("BASE_ADDR", raising=False)
    with pytest.raises(PaymentAddressNotConfigured):
        payment_address()


def test_live_poller_filters_on_the_current_address(monkeypatch, tmp_path):
    # The poller reads ~/.moltrust_secrets at import; give it an empty one.
    (tmp_path / ".moltrust_secrets").write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delitem(sys.modules, "monitor.poll_payments", raising=False)
    monkeypatch.setenv("BASE_ADDR", A)
    poller = importlib.import_module("monitor.poll_payments")
    seen = []

    class FakeEth:
        def get_logs(self, query):
            seen.append(query["topics"][2])
            return []

    class FakeW3:
        eth = FakeEth()

    monkeypatch.setattr(poller, "w3_client", lambda: FakeW3())
    poller.get_usdc_transfers(1, 2)
    monkeypatch.setenv("BASE_ADDR", B)
    poller.get_usdc_transfers(1, 2)
    assert seen == ["0x" + A[2:].zfill(64), "0x" + B[2:].zfill(64)]


def test_no_hard_wired_address_in_the_readers():
    """The three readers carry no 20-byte literal of their own any more."""
    literal = re.compile(r"0x[0-9a-fA-F]{40}")
    usdc_contract = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    for rel in ("app/usdc.py", "monitor/poll_payments.py", "agents/poll_payments.py"):
        found = set(literal.findall((REPO / rel).read_text())) - {usdc_contract}
        assert not found, f"{rel} still holds {sorted(found)}"
