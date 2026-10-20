"""Kalshi adapter against the real kernel and a mocked Kalshi demo API.

/enforce/check and /enforce/ratify are answered by `enforce_check` and `ratify` behind an
httpx.MockTransport, in the endpoint's response shape. The Kalshi side is a second
MockTransport that checks the request signature the way Kalshi documents it and answers in
the documented response shapes (Get Market, Get Event, Get Series, Create Order V2). No test
talks to Kalshi.
"""
import base64
import datetime as dt
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import enforce_check
from moltrust_enforce._ratify_core import ratify
from moltrust_enforce.adapters.kalshi import (
    ALLOW, CLASS_MENTION, CLASS_POLITICS, CLASS_STANDARD, CLASS_UNCLASSIFIED, FORBID, HOLD,
    ORDER_PATH, ExposureLedger, KalshiClient, KalshiOrderGuard, KalshiProfile,
    NonDemoEndpoint, SeriesInfo, TelegramApprover, build_mandate, classify, count_c,
    ed25519_attester, fixed_point, order_cost_e6, price_e4, sign_request, trust_list_for,
)
from moltrust_enforce.cli import main as verify_cli
from moltrust_enforce.client import EnforceClient

from conftest import as_endpoint_payload

PRINCIPAL_KEY = Ed25519PrivateKey.from_private_bytes(b"\x11" * 32)
APPROVER_KEY = Ed25519PrivateKey.from_private_bytes(b"\x12" * 32)
PRINCIPAL_DID = "did:moltrust:kkkkkkkkkkkkkkkk"
APPROVER_DID = "did:moltrust:aaaaaaaaaaaaaaaa"
NOW = dt.datetime(2026, 10, 20, 12, 0, 30, tzinfo=dt.timezone.utc)
DEMO = "https://demo-api.kalshi.co/trade-api/v2"
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_ID = "a952bcbe-ec3b-4b5b-b8f9-11dae589608c"

# Series metadata in the Get Series shape. Values are fixtures, not Kalshi data.
SERIES = {
    "KXBTC15M": {"ticker": "KXBTC15M", "category": "Crypto", "categories": ["Crypto"],
                 "tags": ["15 min", "BTC"], "frequency": "fifteen_min"},
    "KXETH15M": {"ticker": "KXETH15M", "category": "Crypto", "categories": ["Crypto"],
                 "tags": ["15 min", "ETH"], "frequency": "fifteen_min"},
    "KXDOGE15M": {"ticker": "KXDOGE15M", "category": "Crypto", "categories": ["Crypto"],
                  "tags": ["15 min"], "frequency": "fifteen_min"},
    "KXPRES": {"ticker": "KXPRES", "category": "Politics", "categories": ["Politics"],
               "tags": [], "frequency": "one_off"},
    "KXSENATE": {"ticker": "KXSENATE", "category": "Elections", "categories": ["Elections"],
                 "tags": ["US Elections"], "frequency": "one_off"},
    "KXSAY": {"ticker": "KXSAY", "category": "Mentions", "categories": ["Mentions"],
              "tags": [], "frequency": "weekly"},
    "KXSHOWMENTION": {"ticker": "KXSHOWMENTION", "category": "Entertainment",
                      "categories": ["Entertainment"], "tags": [], "frequency": "weekly"},
    "KXLEAVITT": {"ticker": "KXLEAVITT", "category": "Companies", "categories": ["Companies"],
                  "tags": ["Trump"], "frequency": "weekly"},
}


def pub_hex(key):
    return key.public_key().public_bytes_raw().hex()


def profile(**over):
    p = dict(
        mandate_id="kalshi-test-1",
        principal={"did": PRINCIPAL_DID, "public_key": pub_hex(PRINCIPAL_KEY)},
        ratification_authorities=({"did": APPROVER_DID, "public_key": pub_hex(APPROVER_KEY),
                                   "role": "risk_desk"},),
        series=("KXBTC15M", "KXETH15M"),
        categories=("Crypto",),
        sides=("bid",),
        max_contracts="10",
        min_price="0.05",
        max_price="0.95",
        max_market_day_exposure="20.00",
        hold_above="5.00",
        valid_from="2026-10-01T00:00:00Z",
        valid_until="2026-12-31T23:59:59Z",
    )
    p.update(over)
    return KalshiProfile(**p)


def market(series, n=1):
    return f"{series}-26OCT201215-{n}"


def kernel_transport(calls=None):
    def handler(request):
        body = json.loads(request.content)
        if calls is not None:
            calls.append(request.url.path)
        if request.url.path == "/enforce/check":
            return httpx.Response(200, json=as_endpoint_payload(
                enforce_check(body.get("mandate"), body.get("transaction"))))
        if request.url.path == "/enforce/ratify":
            r = ratify(body.get("prior_record"), body.get("decision"),
                       body.get("authority_proof"))
            return httpx.Response(200, json={
                "status": r["status"], "decision": r["decision"], "ratifies": r["ratifies"],
                "authority": r["authority"], "reason": r["reason"], "trace": r["trace"],
                "record": {"core": r["core"], "core_digest": r["core_digest"]}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


class FakeKalshi:
    """Kalshi demo stand-in: verifies the signature, records orders, answers per the docs."""

    def __init__(self, public_key=None):
        self.public_key = public_key or RSA_KEY.public_key()
        self.orders = []
        self.bad_signatures = 0

    def _signed(self, request):
        ts = request.headers.get("KALSHI-ACCESS-TIMESTAMP", "")
        msg = f"{ts}{request.method}{request.url.path}".encode()
        try:
            self.public_key.verify(
                base64.b64decode(request.headers.get("KALSHI-ACCESS-SIGNATURE", "")), msg,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                            salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        except Exception:
            self.bad_signatures += 1
            return False
        return request.headers.get("KALSHI-ACCESS-KEY") == KEY_ID

    def __call__(self, request):
        if not self._signed(request):
            return httpx.Response(401, json={"code": "unauthorized"})
        path = request.url.path.removeprefix("/trade-api/v2")
        if request.method == "GET" and path.startswith("/markets/"):
            t = path.split("/")[2]
            return httpx.Response(200, json={"market": {"ticker": t,
                                                        "event_ticker": t.rsplit("-", 1)[0]}})
        if request.method == "GET" and path.startswith("/events/"):
            e = path.split("/")[2]
            return httpx.Response(200, json={"event": {"event_ticker": e,
                                                       "series_ticker": e.split("-")[0]}})
        if request.method == "GET" and path.startswith("/series/"):
            s = SERIES.get(path.split("/")[2])
            return httpx.Response(200, json={"series": s}) if s else httpx.Response(404)
        if request.method == "POST" and path == ORDER_PATH:
            body = json.loads(request.content)
            self.orders.append(body)
            return httpx.Response(201, json={"order_id": f"o-{len(self.orders)}",
                                             "client_order_id": body.get("client_order_id"),
                                             "fill_count": "0.00",
                                             "remaining_count": body["count"],
                                             "ts_ms": 1792500000000})
        return httpx.Response(404)


def kalshi_client(fake=None, **kw):
    fake = fake or FakeKalshi()
    return KalshiClient(KEY_ID, RSA_KEY, base_url=DEMO, transport=httpx.MockTransport(fake),
                        clock_ms=lambda: 1792500000123, **kw), fake


def guard(p=None, approver=None, tmp_path=None, ledger=None, calls=None):
    kc, fake = kalshi_client()
    g = KalshiOrderGuard(
        EnforceClient("https://api.test", api_key="k", transport=kernel_transport(calls)),
        p or profile(), kc, approver=approver,
        attest=ed25519_attester(PRINCIPAL_DID, PRINCIPAL_KEY, keyid="op-1"),
        ledger=ledger, receipts_dir=str(tmp_path) if tmp_path else None, clock=lambda: NOW)
    return g, fake


def order(ticker=None, side="bid", count="1", price="0.4500", **kw):
    o = {"ticker": ticker or market("KXBTC15M"), "side": side, "count": count, "price": price,
         "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross"}
    o.update(kw)
    return o


TRUST = trust_list_for(PRINCIPAL_DID, PRINCIPAL_KEY.public_key(), keyid="op-1")


# ------------------------------------------------------------------ units

def test_fixed_point_is_exact_and_strict():
    assert fixed_point("0.5600", 4) == 5600
    assert fixed_point("10", 2) == 1000
    assert fixed_point("10.50", 2) == 1050
    assert fixed_point("0.12340", 4) == 1234       # trailing zero beyond places is fine
    assert fixed_point("0.12345", 4) is None       # real fraction beyond places is not
    for bad in ("-1", "1e2", "0x10", " ", "", "1.", 0.5, True, None, [1]):
        assert fixed_point(bad, 4) is None, bad
    assert price_e4("0.0000") is None and price_e4("1.0000") is None
    assert price_e4("0.0010") == 10
    assert count_c("0") is None and count_c("0.01") == 1


def test_order_cost_is_what_the_order_can_lose():
    assert order_cost_e6("bid", 1000, 4500) == 4_500_000      # 10 YES at 0.45 = $4.50
    assert order_cost_e6("ask", 1000, 4500) == 5_500_000      # sell YES = buy NO at 0.55
    assert order_cost_e6("buy", 1000, 4500) is None


# ------------------------------------------------------------------ classification

@pytest.mark.parametrize("ticker,expected", [
    ("KXBTC15M", CLASS_STANDARD), ("KXPRES", CLASS_POLITICS), ("KXSENATE", CLASS_POLITICS),
    ("KXSAY", CLASS_MENTION), ("KXSHOWMENTION", CLASS_MENTION), ("KXLEAVITT", CLASS_POLITICS),
])
def test_classify(ticker, expected):
    assert classify(SeriesInfo.from_api(SERIES[ticker])) == expected


def test_classify_unknown_is_unclassified():
    assert classify(None) == CLASS_UNCLASSIFIED
    assert classify(SeriesInfo(ticker="KXX")) == CLASS_UNCLASSIFIED      # no category
    assert classify(SeriesInfo(ticker="", category="Crypto")) == CLASS_UNCLASSIFIED


def test_classify_reads_the_discovery_list_too():
    s = SeriesInfo(ticker="KXMIX", category="World", categories=("World", "Politics"))
    assert classify(s) == CLASS_POLITICS


# ------------------------------------------------------------------ mandate

def test_mandate_is_deny_by_default_with_explicit_forbids():
    m = build_mandate(profile())
    disp = [g["disposition"] for g in m["grants"]]
    assert disp == [FORBID, FORBID, ALLOW, HOLD]
    assert m["profile"] == "kalshi"
    # a class nobody named has no grant
    r = enforce_check(m, {"action": {"verb": "kalshi_order", "market_class": "sports"},
                          "t_min": 0})
    assert r["verdict"] == "DENY" and "unaddressed" in r["reason"]


def test_politics_can_be_opted_in_explicitly():
    m = build_mandate(profile(forbid_politics=False))
    assert [g["disposition"] for g in m["grants"]] == [FORBID, ALLOW, HOLD, ALLOW, HOLD]


@pytest.mark.parametrize("over,needle", [
    ({"series": ()}, "no series"), ({"categories": ()}, "no category"),
    ({"sides": ("buy",)}, "sides"), ({"max_contracts": "0"}, "max_contracts"),
    ({"min_price": "0.9", "max_price": "0.1"}, "inverted"),
    ({"min_price": "0"}, "price"), ({"hold_above": "30"}, "hold_above"),
    ({"valid_from": "2027-01-01T00:00:00Z"}, "inverted"),
])
def test_bad_profile_is_refused(over, needle):
    with pytest.raises(ValueError, match=needle):
        build_mandate(profile(**over))


# ------------------------------------------------------------------ demo guard

def test_non_demo_base_url_is_refused():
    with pytest.raises(NonDemoEndpoint):
        KalshiClient(KEY_ID, RSA_KEY, base_url="https://external-api.kalshi.com/trade-api/v2")
    with pytest.raises(NonDemoEndpoint):
        KalshiClient(KEY_ID, RSA_KEY, base_url="https://api.elections.kalshi.com/trade-api/v2")
    KalshiClient(KEY_ID, RSA_KEY, base_url="https://api.elections.kalshi.com/trade-api/v2",
                 allow_non_demo=True).close()  # only with the explicit switch


def test_signature_matches_the_documented_scheme():
    sig = sign_request(RSA_KEY, "1703123456789", "get", "/trade-api/v2/portfolio/balance?x=1")
    RSA_KEY.public_key().verify(
        base64.b64decode(sig), b"1703123456789GET/trade-api/v2/portfolio/balance",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256())
    ed = Ed25519PrivateKey.generate()
    sig = sign_request(ed, "1", "POST", "/trade-api/v2/portfolio/events/orders")
    ed.public_key().verify(base64.b64decode(sig), b"1POST/trade-api/v2/portfolio/events/orders")


def test_pem_key_is_accepted():
    pem = RSA_KEY.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    kc, fake = kalshi_client()
    kc2 = KalshiClient(KEY_ID, pem, base_url=DEMO, transport=httpx.MockTransport(fake))
    assert kc2.series_for(market("KXBTC15M")).category == "Crypto"
    assert fake.bad_signatures == 0


# ------------------------------------------------------------------ verdicts

def test_allow_sends_the_order_unchanged(tmp_path):
    g, fake = guard(tmp_path=tmp_path)
    o = order(client_order_id="c-1")
    out = g.place_order(o)
    assert out.disposition == ALLOW and out.sent and out.kalshi_status == 201
    assert fake.orders == [o]
    assert fake.bad_signatures == 0
    assert out.aer_verify(TRUST).ok
    assert out.receipt_path and verify_cli(["--input", out.receipt_path,
                                            "--trust-list", _write(tmp_path, TRUST)]) == 0


@pytest.mark.parametrize("o,needle", [
    (order(count="11"), "count_c 1100 outside 1..1000"),
    (order(price="0.0400"), "price_e4 400 outside 500..9500"),
    (order(price="0.9600"), "price_e4 9600 outside"),
    (order(side="ask"), "side 'ask' not allowed"),
    (order(ticker=market("KXDOGE15M")), "series_ticker 'KXDOGE15M' not allowed"),
    (order(count="abc"), "count_c unknown"),
    (order(price=0.45), "price_e4 unknown"),
    (order(ticker=market("KXPRES")), "politics markets are forbidden"),
    (order(ticker=market("KXSENATE")), "politics markets are forbidden"),
    (order(ticker=market("KXSAY")), "mention markets are forbidden"),
    (order(ticker=market("KXSHOWMENTION")), "mention markets are forbidden"),
    (order(ticker=market("KXNOSUCH")), "unclassified has no grant"),
])
def test_forbid_sends_nothing_and_leaves_a_receipt(o, needle, tmp_path):
    g, fake = guard(tmp_path=tmp_path)
    out = g.place_order(o)
    assert out.disposition == FORBID, out.reason
    assert needle in out.reason
    assert not out.sent and fake.orders == []
    assert out.aer_verify(TRUST).ok and out.aer_verify(TRUST).recomputed_verdict == "DENY"
    receipt = json.loads(open(out.receipt_path).read())
    assert receipt["sent"] is False and receipt["disposition"] == FORBID


def test_exposure_per_market_and_day_caps_repeat_orders():
    g, fake = guard()
    # $4.50 each: allow, allow, allow, allow ($18.00), the fifth would be $22.50 > $20.00
    outs = [g.place_order(order(count="10", price="0.4500")) for _ in range(5)]
    assert [o.disposition for o in outs] == [ALLOW] * 4 + [FORBID]
    assert "market_day_exposure_e6 22500000 outside 0..20000000" in outs[-1].reason
    assert len(fake.orders) == 4
    # another market has its own budget
    assert g.place_order(order(ticker=market("KXBTC15M", 2), count="10")).disposition == ALLOW


def test_ledger_survives_a_restart(tmp_path):
    path = str(tmp_path / "ledger.json")
    g, _ = guard(ledger=ExposureLedger(path))
    g.place_order(order(count="10", price="0.9000"))       # $9.00, hold -> no approver
    g.place_order(order(count="10", price="0.4500"))       # $4.50 allow
    g2, _ = guard(ledger=ExposureLedger(path))
    tx = g2.transaction_for(order(count="1", price="0.5000"), NOW)
    assert tx["market_day_exposure_e6"] == 4_500_000 + 500_000


def test_hold_without_approver_sends_nothing():
    g, fake = guard()
    out = g.place_order(order(count="10", price="0.6000"))     # $6.00 > hold_above $5.00
    assert out.disposition == HOLD and not out.sent and fake.orders == []
    assert out.reason.startswith("hold, no approval")


def _telegram(answer):
    sent = []

    def send(text, buttons):
        sent.append((text, buttons))

    def wait(token, timeout):
        assert sent and sent[-1][1][0][1] == f"kg|approve|{token}"
        return answer
    return sent, TelegramApprover(send, wait, authority_did=APPROVER_DID,
                                  signing_key=APPROVER_KEY, timeout_s=1)


def test_hold_approved_on_telegram_is_ratified_and_sent(tmp_path):
    sent, approver = _telegram("approve")
    g, fake = guard(approver=approver, tmp_path=tmp_path)
    out = g.place_order(order(count="10", price="0.6000"))
    assert out.disposition == HOLD and out.ratification.approved and out.sent
    assert len(fake.orders) == 1 and len(sent) == 1
    assert "KG-ENF hold" in sent[0][0] and "$6.00" in sent[0][0]
    receipt = json.loads(open(out.receipt_path).read())
    assert receipt["ratification"]["status"] == "RATIFIED"
    # the ratification recomputes offline against the same prior record
    client = EnforceClient("https://api.test", api_key="k", transport=kernel_transport())
    v = client.verify_ratification(out.ratification,
                                   {"core": out.verdict.core,
                                    "core_digest": out.verdict.core_digest},
                                   out.mandate, out.authority_proof)
    assert v.ok and v.full_recompute


def test_hold_rejected_or_unanswered_sends_nothing():
    for answer in ("reject", None):
        _, approver = _telegram(answer)
        g, fake = guard(approver=approver)
        out = g.place_order(order(count="10", price="0.6000"))
        assert not out.sent and fake.orders == [], answer


def test_hold_signed_by_a_stranger_is_rejected():
    stranger = Ed25519PrivateKey.generate()
    approver = TelegramApprover(lambda t, b: None, lambda tok, s: "approve",
                                authority_did=APPROVER_DID, signing_key=stranger)
    g, fake = guard(approver=approver)
    out = g.place_order(order(count="10", price="0.6000"))
    assert out.ratification.status == "REJECTED" and not out.sent and fake.orders == []


def test_server_verdict_that_does_not_recompute_is_a_forbid():
    def lying(request):
        body = json.loads(request.content)
        r = enforce_check(body["mandate"], body["transaction"])
        payload = as_endpoint_payload(r)
        payload["verdict"] = "PERMIT"
        return httpx.Response(200, json=payload)
    kc, fake = kalshi_client()
    g = KalshiOrderGuard(EnforceClient("https://api.test", api_key="k",
                                       transport=httpx.MockTransport(lying)),
                         profile(), kc, clock=lambda: NOW)
    out = g.place_order(order(side="ask"))
    assert out.disposition == FORBID and "does not recompute" in out.reason
    assert fake.orders == []


def test_transport_failure_is_a_forbid():
    def down(request):
        raise httpx.ConnectError("down")
    kc, fake = kalshi_client()
    g = KalshiOrderGuard(EnforceClient("https://api.test", api_key="k",
                                       transport=httpx.MockTransport(down)),
                         profile(), kc, clock=lambda: NOW)
    assert g.place_order(order()).disposition == FORBID and fake.orders == []


def test_tampered_receipt_fails_offline(tmp_path):
    g, _ = guard(tmp_path=tmp_path)
    out = g.place_order(order(side="ask"))
    r = json.loads(open(out.receipt_path).read())
    r["transaction"]["side"] = "bid"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(r))
    assert verify_cli(["--input", str(bad), "--trust-list", _write(tmp_path, TRUST)]) == 1


def test_one_check_per_order_and_no_ratify_without_hold():
    calls = []
    g, _ = guard(calls=calls)
    g.place_order(order())
    g.place_order(order(side="ask"))
    assert calls == ["/enforce/check", "/enforce/check"]


def _write(tmp_path, obj):
    p = tmp_path / "trust.json"
    p.write_text(json.dumps(obj))
    return str(p)
