"""scripts/watch_old_wallet.py (ops R2): incoming value on OLD is reported, silence has a reason."""
import datetime as dt
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("watch_old_wallet", ROOT / "scripts" / "watch_old_wallet.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

NOW = dt.datetime(2026, 10, 13, 7, 20, tzinfo=dt.timezone.utc)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(w, "HEARTBEAT", tmp_path / "hb.json")
    sent = []
    import types
    fake_notify = types.SimpleNamespace(ALERTS="alerts",
                                        send_telegram=lambda text, channel: sent.append(text) or True)
    monkeypatch.setitem(__import__("sys").modules, "app.notify", fake_notify)
    import app
    monkeypatch.setattr(app, "notify", fake_notify, raising=False)
    return tmp_path, sent


def fake_rpc(logs, head=w.START_BLOCK + 10, balance=0, fail=False):
    def rpc(method, params):
        if fail:
            raise RuntimeError("rpc down")
        if method == "eth_blockNumber":
            return hex(head)
        if method == "eth_getLogs":
            frm, to = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            return [lg for lg in logs if frm <= int(lg["blockNumber"], 16) <= to]
        if method == "eth_getBalance":
            return hex(balance)
        raise AssertionError(method)
    return rpc


def hb(tmp):
    return json.loads((tmp / "hb.json").read_text())


def test_nothing_in_is_ok_with_a_reason(env, monkeypatch):
    tmp, sent = env
    monkeypatch.setattr(w, "rpc", fake_rpc([]))
    assert w.main([], now=NOW) == 0
    assert hb(tmp)["status"] == "ok" and "0 token transfer" in hb(tmp)["detail"]
    assert sent == []


def test_a_transfer_to_old_is_a_finding_and_is_sent(env, monkeypatch):
    tmp, sent = env
    log = {"transactionHash": "0x" + "ab" * 32, "blockNumber": hex(w.START_BLOCK + 3),
           "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "data": hex(50000)}
    monkeypatch.setattr(w, "rpc", fake_rpc([log]))
    assert w.main([], now=NOW) == 0
    assert hb(tmp)["status"] == "finding"
    assert len(sent) == 1 and "0x" + "ab" * 32 in sent[0]


def test_native_increase_is_a_finding(env, monkeypatch):
    tmp, sent = env
    monkeypatch.setattr(w, "rpc", fake_rpc([], balance=100))
    w.main([], now=NOW)
    monkeypatch.setattr(w, "rpc", fake_rpc([], head=w.START_BLOCK + 20, balance=500))
    w.main([], now=NOW)
    assert hb(tmp)["status"] == "finding" and "native balance rose" in sent[-1]


def test_unreadable_chain_is_an_error_not_a_quiet_zero(env, monkeypatch):
    tmp, sent = env
    monkeypatch.setattr(w, "rpc", fake_rpc([], fail=True))
    assert w.main([], now=NOW) == 1
    assert hb(tmp)["status"] == "error" and not (tmp / "state.json").exists()


def test_after_the_window_the_run_says_expired(env, monkeypatch):
    tmp, _ = env
    monkeypatch.setattr(w, "rpc", fake_rpc([], fail=True))
    assert w.main([], now=w.WINDOW_END + dt.timedelta(seconds=1)) == 0
    assert hb(tmp)["status"] == "expired"
