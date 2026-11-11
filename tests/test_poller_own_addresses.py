"""The USDC poller does not book transfers from our own wallets (ops job C4, 2026-10-13).

On 2026-10-12 the wallet rotation moved 0.39 USDC from OLD to NEW. The poller
saw a transfer to the payment address and recorded it in payment_events as a
payment received. Three scripts already kept a list of our own addresses; the
poller kept none.
"""
import importlib
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OLD = "0x380238347e58435f40b4da1f1a045a271d5838f5"
NEW = "0xd63f988201a0bd184deab7d4e97b1717b25a5800"
FOREIGN = "0x1111111111111111111111111111111111111111"
PLACEHOLDERS = {"0x" + "0" * 40, "0x" + "0" * 39 + "1"}
LISTS = ("scripts/gate_measure.py", "scripts/runden_auswertung.py",
         "scripts/bazaar_index_check.py")


@pytest.fixture
def pp(monkeypatch, tmp_path):
    # The poller reads ~/.moltrust_secrets at import; CI has none.
    (tmp_path / ".moltrust_secrets").write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASE_RPC", "https://rpc.ankr.com/base/testschluessel")
    monkeypatch.setenv("BASE_ADDR", "0xD63f988201A0Bd184deaB7D4e97b1717B25a5800")
    monkeypatch.delitem(sys.modules, "monitor.poll_payments", raising=False)
    return importlib.import_module("monitor.poll_payments")


@pytest.mark.parametrize("path", LISTS)
def test_poller_list_holds_every_address_of_the_three_scripts(pp, path):
    named = {a.lower() for a in re.findall(r"0x[0-9a-fA-F]{40}", (ROOT / path).read_text())}
    missing = named - PLACEHOLDERS - pp.OWN_ADDRESSES
    assert not missing, f"{path} names own addresses the poller does not exclude: {sorted(missing)}"


def test_old_and_new_are_own(pp):
    assert pp.is_own_address(OLD)
    assert pp.is_own_address(NEW.upper().replace("0X", "0x"))
    assert not pp.is_own_address(FOREIGN)


def _entry(sender, tx):
    return {
        "topics": ["0x" + "ab" * 32, "0x" + "0" * 24 + sender[2:]],
        "data": hex(390_000),
        "transactionHash": tx,
        "blockNumber": 1_000_100,
    }


def test_a_run_books_the_foreign_transfer_and_skips_the_own_one(pp, monkeypatch):
    cursor = 1_000_000
    eth = types.SimpleNamespace(block_number=cursor + 200,
                                get_block=lambda n: {"timestamp": 1_760_000_000})
    monkeypatch.setattr(pp, "w3_client", lambda: types.SimpleNamespace(eth=eth))
    saved = {}
    monkeypatch.setattr(pp, "load_state", lambda: {"last_block": cursor})
    monkeypatch.setattr(pp, "save_state", lambda s: saved.update(s))
    monkeypatch.setattr(pp, "send_telegram", lambda text, **kw: True)
    monkeypatch.setattr(pp, "get_usdc_transfers", lambda a, b: [
        _entry(OLD, "0x" + "01" * 32),
        _entry(FOREIGN, "0x" + "02" * 32),
    ])
    booked = []

    async def record(tx_hash, from_addr, usdc_amount, block_num, timestamp):
        booked.append(from_addr)
        return True

    monkeypatch.setattr(pp, "record_to_db", record)
    assert pp.main() == 0
    assert booked == [FOREIGN]
    assert saved["last_block"] == cursor + 200, "an own transfer must not hold the cursor"
