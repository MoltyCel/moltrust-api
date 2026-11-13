"""Polymarket adapter against the real kernel.

/enforce/check and /enforce/ratify are answered by `enforce_check` and `ratify` behind an
httpx.MockTransport, in the endpoint's response shape. The signer is a recording stub: no
test signs an order, holds a wallet key or talks to Polymarket.
"""
import datetime as dt
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from moltrust_enforce import enforce_check
from moltrust_enforce._ratify_core import ratify
from moltrust_enforce.adapters.polymarket import (
    ALLOW, COMMIT_PREFIX, CTF_EXCHANGE, FORBID, HOLD, NEG_RISK_EXCHANGE, CommitmentParties,
    ExposureLedger, PolymarketOrderGuard, PolymarketProfile, build_commitment, build_mandate,
    check_fill, commitment_metadata, ed25519_attester, fixed_point, keccak256,
    order_cost_e6, parse_preimage, preimage_text, price_bps, ratification_proof, size_c,
    token_id, trust_list_for, verify_commitment,
)
from moltrust_enforce.cli import main as verify_cli
from moltrust_enforce.client import EnforceClient

from conftest import as_endpoint_payload

PRINCIPAL_KEY = Ed25519PrivateKey.from_private_bytes(b"\x21" * 32)
APPROVER_KEY = Ed25519PrivateKey.from_private_bytes(b"\x22" * 32)
PRINCIPAL_DID = "did:moltrust:pppppppppppppppp"
APPROVER_DID = "did:moltrust:qqqqqqqqqqqqqqqq"
NOW = dt.datetime(2026, 11, 13, 12, 0, 30, tzinfo=dt.timezone.utc)
YES = "109940776822972326436736251138122529787304609190841333433010412811874399854702"
NO = "48331043336612883890938759509493159234755048973500640148014422747788308965732"
OTHER = "21742633143463906290569050155826241533067272736897614950488156847949938836455"
SIGNER = "0x1111111111111111111111111111111111111111"
MAKER = "0x2222222222222222222222222222222222222222"

# The example preimage from the commitment encoding, byte for byte.
SPEC_PREIMAGE = (
    "moltrust-pm-commit/1\n"
    "did=did:moltrust:157224190be24072\n"
    "signer=0x0000000000000000000000000000000000000000\n"
    "maker=0x0000000000000000000000000000000000000000\n"
    "chain=eip155:137\n"
    "exchange=0xe111180000d2663c0091e4f400237545b87b996b\n"
    "tokenId=" + YES + "\n"
    "side=BUY\n"
    "priceMinBps=4000\n"
    "priceMaxBps=6000\n"
    "notBeforeMs=1789930000000\n"
    "notAfterMs=1789933600000\n"
    "nonce=0x9f2c1a77"
)


def pub_hex(key):
    return key.public_key().public_bytes_raw().hex()


def profile(**over):
    p = dict(
        mandate_id="pm-test-1",
        principal={"did": PRINCIPAL_DID, "public_key": pub_hex(PRINCIPAL_KEY)},
        ratification_authorities=({"did": APPROVER_DID, "public_key": pub_hex(APPROVER_KEY),
                                   "role": "risk_desk"},),
        markets=(YES, NO),
        sides=("BUY",),
        max_size="100",
        min_price="0.05",
        max_price="0.95",
        max_market_day_exposure="20.00",
        max_day_exposure="30.00",
        hold_above="5.00",
        valid_from="2026-11-01T00:00:00Z",
        valid_until="2026-12-31T23:59:59Z",
    )
    p.update(over)
    return PolymarketProfile(**p)


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


class Signer:
    """Stands in for the caller's sign-and-post; records what it was handed."""

    def __init__(self):
        self.orders = []

    def __call__(self, order):
        self.orders.append(order)
        return {"success": True, "orderID": f"0x{len(self.orders):064x}"}


def guard(p=None, approver=None, tmp_path=None, ledger=None, calls=None, commitment=None,
          transport=None):
    signer = Signer()
    g = PolymarketOrderGuard(
        EnforceClient("https://api.test", api_key="k",
                      transport=transport or kernel_transport(calls)),
        p or profile(), signer, commitment=commitment, approver=approver,
        attest=ed25519_attester(PRINCIPAL_DID, PRINCIPAL_KEY, keyid="op-1"),
        ledger=ledger, receipts_dir=str(tmp_path) if tmp_path else None, clock=lambda: NOW,
        nonce=lambda: "0x9f2c1a77")
    return g, signer


def order(token=YES, side="BUY", price="0.45", size="10", **kw):
    o = {"token_id": token, "side": side, "price": price, "size": size}
    o.update(kw)
    return o


TRUST = trust_list_for(PRINCIPAL_DID, PRINCIPAL_KEY.public_key(), keyid="op-1")


def _write(tmp_path, obj):
    p = tmp_path / "trust.json"
    p.write_text(json.dumps(obj))
    return str(p)


# ------------------------------------------------------------------ units

def test_keccak256_matches_ethereum():
    assert keccak256(b"").hex() == \
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    typehash = ("Order(uint256 salt,address maker,address signer,uint256 tokenId,"
                "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
                "uint256 timestamp,bytes32 metadata,bytes32 builder)")
    # ORDER_TYPEHASH of the deployed exchange, as the commitment encoding records it
    assert keccak256(typehash.encode()).hex() == \
        "bb86318a2138f5fa8ae32fbe8e659f8fcf13cc6ae4014a707893055433818589"


def test_fixed_point_units_are_exact_and_strict():
    assert price_bps("0.45") == 4500 and price_bps("0.0001") == 1
    assert price_bps("0") is None and price_bps("1") is None and price_bps(0.45) is None
    assert size_c("10") == 1000 and size_c("0.01") == 1 and size_c("0.001") is None
    assert size_c("0") is None
    assert fixed_point("1e2", 2) is None and fixed_point("-1", 2) is None
    assert order_cost_e6("BUY", 1000, 4500) == 4_500_000     # 10 shares at 0.45 = $4.50
    assert order_cost_e6("SELL", 1000, 4500) == 5_500_000    # gives up 10 x 0.55
    assert order_cost_e6("bid", 1000, 4500) is None


def test_token_id_is_a_canonical_decimal_string():
    assert token_id(YES) == YES
    assert token_id(int(NO)) == NO
    for bad in ("0", "007", "-1", "0x10", "", " 1", str(2 ** 256), True, 1.5, None):
        assert token_id(bad) is None, bad


# ------------------------------------------------------------------ commitment

def test_spec_preimage_round_trips_and_carries_the_prefix():
    fields = parse_preimage(SPEC_PREIMAGE)
    assert preimage_text(fields) == SPEC_PREIMAGE
    meta = commitment_metadata(SPEC_PREIMAGE)
    assert meta.startswith(COMMIT_PREFIX) and len(meta) == 66
    assert meta[8:] == keccak256(SPEC_PREIMAGE.encode()).hex()[:58]
    assert verify_commitment(SPEC_PREIMAGE, meta)
    assert verify_commitment(SPEC_PREIMAGE, meta.upper().replace("0X", "0x"))
    assert not verify_commitment(SPEC_PREIMAGE.replace("BUY", "SELL"), meta)
    # someone else's full-entropy hash in the field is not a MolTrust commitment
    assert not verify_commitment(SPEC_PREIMAGE, "0x7072656400" + meta[12:])


def test_build_commitment_reproduces_the_spec_example():
    zero = "0x" + "0" * 40
    c = build_commitment(CommitmentParties(signer=zero, maker=zero), "did:moltrust:157224190be24072",
                         CTF_EXCHANGE, YES, "BUY", (4000, 6000), 1789930000000, "0x9f2c1a77")
    assert c["preimage"] == SPEC_PREIMAGE
    assert c["metadata"] == commitment_metadata(SPEC_PREIMAGE)


@pytest.mark.parametrize("text", [
    SPEC_PREIMAGE + "\n",                                  # trailing newline
    SPEC_PREIMAGE.replace("\n", "\r\n"),                   # CRLF
    SPEC_PREIMAGE.replace("side=BUY", "side=BUY "),        # trailing whitespace
    SPEC_PREIMAGE.replace("exchange=0xe111", "exchange=0xE111"),   # checksum casing
    SPEC_PREIMAGE.replace("moltrust-pm-commit/1", "moltrust-pm-commit/2"),
])
def test_non_canonical_preimage_is_refused(text):
    with pytest.raises(ValueError):
        parse_preimage(text)


def test_bad_commitment_parties_are_refused():
    for parties in (CommitmentParties("0x12", MAKER), CommitmentParties(SIGNER, "maker"),
                    CommitmentParties(SIGNER, MAKER, ttl_s=0)):
        with pytest.raises(ValueError):
            guard(commitment=parties)


def test_check_fill_follows_the_verification_steps():
    meta = commitment_metadata(SPEC_PREIMAGE)
    zero = "0x" + "0" * 40
    ok = dict(maker=zero, token=YES, side="BUY", maker_amount_filled=4_500_000,
              taker_amount_filled=10_000_000, fill_timestamp_ms=1789930000500, metadata=meta)
    assert check_fill(SPEC_PREIMAGE, **ok).ok
    # BUY at 0.45 inside 0.40..0.60; at 0.65 it is a finding, not an error
    r = check_fill(SPEC_PREIMAGE, **dict(ok, maker_amount_filled=6_500_000))
    assert not r.ok and r.problems == ("implied price outside the band",)
    assert check_fill(SPEC_PREIMAGE, **dict(ok, maker_amount_filled=6_000_000)).ok  # edge
    assert "maker differs" in check_fill(SPEC_PREIMAGE, **dict(ok, maker=MAKER)).problems
    assert "tokenId differs" in check_fill(SPEC_PREIMAGE, **dict(ok, token=NO)).problems
    assert "fill outside the commitment window" in check_fill(
        SPEC_PREIMAGE, **dict(ok, fill_timestamp_ms=1789933600001)).problems
    assert "metadata does not carry this preimage" in check_fill(
        SPEC_PREIMAGE, **dict(ok, metadata=COMMIT_PREFIX + "00" * 29)).problems
    sell = SPEC_PREIMAGE.replace("side=BUY", "side=SELL")
    # SELL: price is taker/maker, i.e. USDC received per share given
    assert check_fill(sell, **dict(ok, side="SELL", metadata=None,
                                   maker_amount_filled=10_000_000,
                                   taker_amount_filled=5_000_000)).ok
    assert not check_fill(sell, **dict(ok, side="SELL", metadata=None,
                                       maker_amount_filled=10_000_000,
                                       taker_amount_filled=3_000_000)).ok


# ------------------------------------------------------------------ mandate

def test_mandate_is_deny_by_default():
    m = build_mandate(profile())
    assert [g["disposition"] for g in m["grants"]] == [ALLOW, HOLD]
    assert m["profile"] == "polymarket"
    r = enforce_check(m, {"action": {"verb": "transfer"}, "t_min": 0})
    assert r["verdict"] == "DENY" and "unaddressed" in r["reason"]


def test_hold_grant_is_left_out_when_hold_equals_the_cap():
    m = build_mandate(profile(hold_above="20.00"))
    assert [g["disposition"] for g in m["grants"]] == [ALLOW]


@pytest.mark.parametrize("over,needle", [
    ({"markets": ()}, "no market"), ({"markets": ("007",)}, "token ids"),
    ({"sides": ("bid",)}, "sides"), ({"exchanges": ("0xabc",)}, "exchanges"),
    ({"max_size": "0"}, "max_size"), ({"min_price": "0.9", "max_price": "0.1"}, "inverted"),
    ({"min_price": "0"}, "price"), ({"hold_above": "30"}, "hold_above"),
    ({"max_day_exposure": "10"}, "max_day_exposure"),
    ({"valid_from": "2027-01-01T00:00:00Z"}, "inverted"),
])
def test_bad_profile_is_refused(over, needle):
    with pytest.raises(ValueError, match=needle):
        build_mandate(profile(**over))


# ------------------------------------------------------------------ verdicts

def test_allow_hands_over_the_order_and_the_record_recomputes(tmp_path):
    g, signer = guard(tmp_path=tmp_path)
    o = order(client_order_id="c-1")
    out = g.place_order(o)
    assert out.disposition == ALLOW and out.sent
    assert signer.orders == [o]
    assert out.molcheck_record is out.aer_record and out.molcheck_record["verdict"] == "PERMIT"
    assert out.aer_verify(TRUST).ok
    assert out.receipt_path and verify_cli(["--input", out.receipt_path,
                                            "--trust-list", _write(tmp_path, TRUST)]) == 0


@pytest.mark.parametrize("o,needle", [
    (order(size="101"), "size_c 10100 outside 1..10000"),
    (order(price="0.04"), "price_bps 400 outside 500..9500"),
    (order(price="0.96"), "price_bps 9600 outside"),
    (order(side="SELL"), "side 'SELL' not allowed"),
    (order(token=OTHER), f"token_id '{OTHER}' not allowed"),
    (order(size="abc"), "size_c unknown"),
    (order(price=0.45), "price_bps unknown"),
    (order(token="0x01"), "token_id unknown"),
    (order(neg_risk="yes"), "exchange unknown"),
    (order(size="100", price="0.25"), "order_cost_e6 25000000 outside 0..20000000"),
])
def test_forbid_hands_over_nothing_and_leaves_a_record(o, needle, tmp_path):
    g, signer = guard(tmp_path=tmp_path)
    out = g.place_order(o)
    assert out.disposition == FORBID, out.reason
    assert needle in out.reason
    assert not out.sent and signer.orders == []
    assert out.aer_verify(TRUST).ok and out.aer_verify(TRUST).recomputed_verdict == "DENY"
    receipt = json.loads(open(out.receipt_path).read())
    assert receipt["sent"] is False and receipt["disposition"] == FORBID


def test_neg_risk_exchange_can_be_excluded():
    g, signer = guard(p=profile(exchanges=(CTF_EXCHANGE,)))
    out = g.place_order(order(neg_risk=True))
    assert out.disposition == FORBID and "exchange" in out.reason and signer.orders == []
    assert g.place_order(order(neg_risk=False)).disposition == ALLOW


def test_exposure_per_market_and_day_caps_repeat_orders():
    g, signer = guard()
    # $4.50 each: four fit under $20.00 per market, the fifth would be $22.50
    outs = [g.place_order(order(size="10", price="0.45")) for _ in range(5)]
    assert [o.disposition for o in outs] == [ALLOW] * 4 + [FORBID]
    assert "market_day_exposure_e6 22500000 outside 0..20000000" in outs[-1].reason
    assert len(signer.orders) == 4


def test_exposure_per_day_caps_across_markets():
    g, _ = guard()
    for _ in range(4):
        assert g.place_order(order(token=YES, size="10", price="0.45")).disposition == ALLOW
    # $18.00 spent on YES; NO has its own market budget but the day has $12.00 left
    assert g.place_order(order(token=NO, size="10", price="0.45")).disposition == ALLOW
    assert g.place_order(order(token=NO, size="10", price="0.45")).disposition == ALLOW
    out = g.place_order(order(token=NO, size="10", price="0.45"))
    assert out.disposition == FORBID
    assert "day_exposure_e6 31500000 outside 0..30000000" in out.reason


def test_ledger_survives_a_restart(tmp_path):
    path = str(tmp_path / "ledger.json")
    g, _ = guard(ledger=ExposureLedger(path))
    g.place_order(order(size="10", price="0.90"))        # $9.00 hold, no approver: not sent
    g.place_order(order(size="10", price="0.45"))        # $4.50 allow
    g2, _ = guard(ledger=ExposureLedger(path))
    tx = g2.transaction_for(order(size="1", price="0.50"), NOW)
    assert tx["market_day_exposure_e6"] == 4_500_000 + 500_000
    assert tx["day_exposure_e6"] == 4_500_000 + 500_000


def test_hold_without_approver_hands_over_nothing():
    g, signer = guard()
    out = g.place_order(order(size="10", price="0.60"))       # $6.00 > hold_above $5.00
    assert out.disposition == HOLD and not out.sent and signer.orders == []
    assert out.reason.startswith("hold, no approval")


def _approver(approve, key=APPROVER_KEY):
    asked = []

    def approver(outcome):
        asked.append(outcome.transaction["order_cost_e6"])
        if approve is None:
            return None
        return ratification_proof(outcome, approve, APPROVER_DID, key)
    return asked, approver


def test_hold_approved_is_ratified_and_handed_over(tmp_path):
    asked, approver = _approver(True)
    g, signer = guard(approver=approver, tmp_path=tmp_path)
    out = g.place_order(order(size="10", price="0.60"))
    assert out.disposition == HOLD and out.ratification.approved and out.sent
    assert len(signer.orders) == 1 and asked == [6_000_000]
    receipt = json.loads(open(out.receipt_path).read())
    assert receipt["ratification"]["status"] == "RATIFIED"
    client = EnforceClient("https://api.test", api_key="k", transport=kernel_transport())
    v = client.verify_ratification(out.ratification,
                                   {"core": out.verdict.core,
                                    "core_digest": out.verdict.core_digest},
                                   out.mandate, out.authority_proof)
    assert v.ok and v.full_recompute


def test_hold_rejected_or_unanswered_hands_over_nothing():
    for answer in (False, None):
        _, approver = _approver(answer)
        g, signer = guard(approver=approver)
        out = g.place_order(order(size="10", price="0.60"))
        assert not out.sent and signer.orders == [], answer


def test_hold_signed_by_a_stranger_is_rejected():
    _, approver = _approver(True, key=Ed25519PrivateKey.generate())
    g, signer = guard(approver=approver)
    out = g.place_order(order(size="10", price="0.60"))
    assert out.ratification.status == "REJECTED" and not out.sent and signer.orders == []


# ------------------------------------------------------------------ commitment in the guard

def test_commitment_is_bound_into_the_record_and_the_signed_order(tmp_path):
    g, signer = guard(tmp_path=tmp_path, commitment=CommitmentParties(SIGNER, MAKER))
    out = g.place_order(order(client_order_id="c-7"))
    assert out.disposition == ALLOW
    meta = out.commitment["metadata"]
    assert out.transaction["commitment"] == meta
    assert signer.orders[0]["metadata"] == meta
    assert verify_commitment(out.commitment["preimage"], meta)
    f = parse_preimage(out.commitment["preimage"])
    assert f["did"] == PRINCIPAL_DID and f["tokenId"] == YES and f["side"] == "BUY"
    assert f["exchange"] == CTF_EXCHANGE and (f["priceMinBps"], f["priceMaxBps"]) == \
        ("500", "9500")
    assert int(f["notAfterMs"]) - int(f["notBeforeMs"]) == 3_600_000
    assert f["notBeforeMs"] == str(int(NOW.timestamp()) * 1000)
    # the receipt carries the preimage; changing the commitment breaks the recompute
    r = json.loads(open(out.receipt_path).read())
    assert r["commitment"]["preimage"] == out.commitment["preimage"]
    r["transaction"]["commitment"] = COMMIT_PREFIX + "00" * 29
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(r))
    assert verify_cli(["--input", str(bad), "--trust-list", _write(tmp_path, TRUST)]) == 1


def test_neg_risk_commitment_names_the_neg_risk_exchange():
    g, _ = guard(commitment=CommitmentParties(SIGNER, MAKER))
    out = g.decide(order(neg_risk=True))
    assert parse_preimage(out.commitment["preimage"])["exchange"] == NEG_RISK_EXCHANGE


def test_forbidden_order_gets_no_metadata_handed_over():
    g, signer = guard(commitment=CommitmentParties(SIGNER, MAKER))
    out = g.place_order(order(side="SELL"))
    assert out.disposition == FORBID and signer.orders == []
    assert "metadata" not in out.order


# ------------------------------------------------------------------ failure paths

def test_server_verdict_that_does_not_recompute_is_a_forbid():
    def lying(request):
        body = json.loads(request.content)
        payload = as_endpoint_payload(enforce_check(body["mandate"], body["transaction"]))
        payload["verdict"] = "PERMIT"
        return httpx.Response(200, json=payload)
    g, signer = guard(transport=httpx.MockTransport(lying))
    out = g.place_order(order(side="SELL"))
    assert out.disposition == FORBID and "does not recompute" in out.reason
    assert signer.orders == []


def test_transport_failure_is_a_forbid():
    def down(request):
        raise httpx.ConnectError("down")
    g, signer = guard(transport=httpx.MockTransport(down))
    out = g.place_order(order())
    assert out.disposition == FORBID and signer.orders == []
    assert out.reason.startswith("no server verdict: transport failure")
    assert out.receipt()["server_record"]["from_server"] is False


def test_error_status_is_a_forbid_with_its_reason():
    g, signer = guard(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, text="slow down")))
    out = g.place_order(order())
    assert out.disposition == FORBID and signer.orders == []
    assert "HTTP 429" in out.reason and "does not recompute" not in out.reason


def test_signer_failure_still_counts_the_exposure_and_writes_the_receipt(tmp_path):
    def broken(order):
        raise RuntimeError("venue down")
    g = PolymarketOrderGuard(
        EnforceClient("https://api.test", api_key="k", transport=kernel_transport()),
        profile(), broken, receipts_dir=str(tmp_path), clock=lambda: NOW)
    with pytest.raises(RuntimeError):
        g.place_order(order(size="10", price="0.45"))
    assert g.ledger.exposure(YES, "2026-11-13") == 4_500_000
    files = list(tmp_path.glob("*-allow-*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["sent"] is True


def test_tampered_receipt_fails_offline(tmp_path):
    g, _ = guard(tmp_path=tmp_path)
    out = g.place_order(order(side="SELL"))
    r = json.loads(open(out.receipt_path).read())
    r["transaction"]["side"] = "BUY"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(r))
    assert verify_cli(["--input", str(bad), "--trust-list", _write(tmp_path, TRUST)]) == 1


def test_one_check_per_order_and_no_ratify_without_hold():
    calls = []
    g, _ = guard(calls=calls)
    g.place_order(order())
    g.place_order(order(side="SELL"))
    assert calls == ["/enforce/check", "/enforce/check"]
