"""The KG-ENF demo run (examples/kalshi/demo_run.py) end to end against mocks.

Same stand-ins as test_adapter_kalshi: the real kernel behind /enforce/check and
/enforce/ratify, and a Kalshi demo fake that checks every request signature. The run must
produce at least 20 orders on 15-minute crypto markets, at least 5 forbids, send no forbid,
and leave receipts that verify offline, by API and by the `moltrust-verify` CLI.
"""
import datetime as dt
import importlib.util
import json
import pathlib

import httpx

from moltrust_enforce.adapters.kalshi import KalshiClient, NonDemoEndpoint
from moltrust_enforce.cli import main as verify_cli
from moltrust_enforce.client import EnforceClient

from test_adapter_kalshi import DEMO, KEY_ID, RSA_KEY, FakeKalshi, kernel_transport

HERE = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kg_enf_demo_run",
                                              HERE / "examples" / "kalshi" / "demo_run.py")
demo_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_run)

NOW = dt.datetime(2026, 10, 20, 12, 0, 30, tzinfo=dt.timezone.utc)


class FakeKalshiWithListing(FakeKalshi):
    def __call__(self, request):
        path = request.url.path.removeprefix("/trade-api/v2")
        if request.method == "GET" and path == "/markets" and self._signed(request):
            s = request.url.params.get("series_ticker")
            assert request.url.params.get("status") == "open"
            live = [{"ticker": f"{s}-26OCT201215-{i}", "event_ticker": f"{s}-26OCT201215",
                     "status": "active", "close_time": "2026-10-20T12:15:00Z"} for i in (15, 30)]
            # the demo answered status=open with a market closed months ago (2026-10-20)
            stale = {"ticker": f"{s}-26MAY050000-00", "event_ticker": f"{s}-26MAY050000",
                     "status": "closed", "close_time": "2026-05-05T04:00:00Z"}
            return httpx.Response(200, json={"markets": [stale] + live, "cursor": ""})
        return super().__call__(request)


def _run(tmp_path, approver_factory=None):
    fake = FakeKalshiWithListing()
    kalshi = KalshiClient(KEY_ID, RSA_KEY, base_url=DEMO, transport=httpx.MockTransport(fake))
    enforce = EnforceClient("https://api.test", api_key="k", transport=kernel_transport())
    lines = []
    code = demo_run.run(str(tmp_path), enforce, kalshi, approver_factory=approver_factory,
                        clock=lambda: NOW, log=lines.append)
    return code, fake, json.loads((tmp_path / "summary.json").read_text()), lines


def test_demo_run_meets_the_acceptance_shape(tmp_path):
    code, fake, data, lines = _run(tmp_path)
    s, rows = data["summary"], data["rows"]
    assert code == 0, "\n".join(lines)
    assert s["orders"] >= 20 and s["dispositions"]["forbid"] >= 5
    assert s["unexpected"] == []
    assert not any(r["sent"] for r in rows if r["disposition"] == "hold")  # no approver
    assert s["aer_pass"] == s["orders"]
    assert all(r["receipt"] for r in rows)
    assert not any(r["sent"] for r in rows if r["disposition"] == "forbid")
    assert len(fake.orders) == s["sent"] == s["dispositions"]["allow"]
    assert fake.bad_signatures == 0
    assert all("15M" in o["ticker"] for o in fake.orders)
    assert not any("26MAY" in o["ticker"] for o in fake.orders)
    assert s["accepted_by_kalshi"] == s["sent"]
    # every receipt also passes the CLI, offline
    trust = str(tmp_path / "trust.json")
    for r in rows:
        assert verify_cli(["--input", str(tmp_path / r["receipt"]), "--trust-list", trust]) == 0


def test_demo_run_with_approved_holds(tmp_path):
    def factory(did, key):
        return demo_run.TelegramApprover(lambda t, b: None, lambda tok, s: "approve",
                                         authority_did=did, signing_key=key)
    code, fake, data, _ = _run(tmp_path, factory)
    s = data["summary"]
    assert code == 0 and s["unexpected"] == []
    assert s["dispositions"]["hold"] == 3
    assert len(fake.orders) == s["dispositions"]["allow"] + 3


def test_demo_run_refuses_production():
    try:
        KalshiClient(KEY_ID, RSA_KEY, base_url="https://external-api.kalshi.com/trade-api/v2")
    except NonDemoEndpoint:
        return
    raise AssertionError("production host accepted")


def test_demo_run_stops_without_markets(tmp_path):
    fake = FakeKalshi()  # no /markets listing -> 404
    kalshi = KalshiClient(KEY_ID, RSA_KEY, base_url=DEMO, transport=httpx.MockTransport(fake))
    enforce = EnforceClient("https://api.test", api_key="k", transport=kernel_transport())
    assert demo_run.run(str(tmp_path), enforce, kalshi, clock=lambda: NOW,
                        log=lambda _l: None) == 2
    assert fake.orders == []


def test_demo_run_fails_when_kalshi_refuses(tmp_path):
    class Refusing(FakeKalshiWithListing):
        def __call__(self, request):
            if request.method == "POST":
                return httpx.Response(404, json={"error": {"code": "insufficient_shard_balance"}})
            return super().__call__(request)
    fake = Refusing()
    kalshi = KalshiClient(KEY_ID, RSA_KEY, base_url=DEMO, transport=httpx.MockTransport(fake))
    enforce = EnforceClient("https://api.test", api_key="k", transport=kernel_transport())
    lines = []
    assert demo_run.run(str(tmp_path), enforce, kalshi, clock=lambda: NOW,
                        log=lines.append) == 1
    assert any("kalshi refused" in line for line in lines)


def test_percentile_nearest_rank():
    assert demo_run.percentile([5, 1, 3, 2, 4], 50) == 3
    assert demo_run.percentile(list(range(1, 101)), 95) == 95
    assert demo_run.percentile([7], 95) == 7
