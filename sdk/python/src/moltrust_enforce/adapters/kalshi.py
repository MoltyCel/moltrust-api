"""Mandate check before every Kalshi order.

The adapter sits between an agent and its own Kalshi order call. Before an order leaves the
process it asks `POST /enforce/check` for a verdict and sends the order only when the verdict
is PERMIT, or PENDING with a ratification that came back RATIFIED and APPROVED. A DENY sends
nothing. Every decision leaves a receipt (an AER record and bundle) that `moltrust-verify`
recomputes offline.

    from moltrust_enforce import EnforceClient
    from moltrust_enforce.adapters.kalshi import KalshiClient, KalshiOrderGuard, KalshiProfile

    kalshi = KalshiClient(key_id=KEY_ID, private_key=PEM)          # demo by default
    guard = KalshiOrderGuard(EnforceClient("https://api.moltrust.ch", api_key=KEY),
                             profile, kalshi, attest=attest, receipts_dir="receipts")
    outcome = guard.place_order({"ticker": "KXBTC15M-...", "side": "bid", "count": "1",
                                 "price": "0.4500", "time_in_force": "immediate_or_cancel",
                                 "self_trade_prevention_type": "taker_at_cross"})

Kalshi endpoints (docs.kalshi.com, read 2026-10-20)
---------------------------------------------------
- Orders go to ``POST /portfolio/events/orders`` (Create Order V2): ``side`` is ``bid`` (buy
  YES) or ``ask`` (sell YES), ``count`` and ``price`` are fixed-point strings ("10.00",
  "0.5600"). The legacy ``POST /portfolio/orders`` is deprecated (changelog, 18 June 2026:
  "calls to these endpoints will return `Please switch to the V2 endpoints`").
- Auth: headers ``KALSHI-ACCESS-KEY`` (key id), ``KALSHI-ACCESS-TIMESTAMP`` (ms) and
  ``KALSHI-ACCESS-SIGNATURE``: base64 of an RSA-PSS/SHA-256 signature (or Ed25519) over
  ``timestamp + METHOD + path``, path from the API root without the query string.
- Demo hosts: ``https://external-api.demo.kalshi.co/trade-api/v2`` and
  ``https://demo-api.kalshi.co/trade-api/v2``. `KalshiClient` refuses any other host unless
  ``allow_non_demo=True`` is passed explicitly.
- A market names its ``event_ticker``, an event its ``series_ticker``, and a series carries
  ``category`` (primary), ``categories`` (discovery list), ``tags`` and ``frequency``
  (Get Market, Get Event, Get Series).

Profile -> mandate
------------------
One check per order. The action is ``{"verb": "kalshi_order", "market_class": <class>}``:

- ``standard``  allow up to ``hold_above`` order cost, hold above it up to the exposure cap.
- ``politics``  a ``forbid`` grant unless ``forbid_politics=False``.
- ``mention``   a ``forbid`` grant unless ``forbid_mentions=False``.
- anything the classifier cannot place (``unclassified``) has no grant: unaddressed, DENY.

Every allow/hold grant carries the same constraints: series ticker and category from the
profile's lists, side, contracts per order, price band, exposure per market and UTC day, and
the validity window. A field the adapter cannot derive stays absent and fails its constraint:
unknown means forbid. Nothing is allowed by pattern; the mandate is deny-by-default.

Units are integers so the kernel can compare them: ``count_c`` hundredths of a contract,
``price_e4`` ten-thousandths of a dollar, ``order_cost_e6`` and ``market_day_exposure_e6``
millionths of a dollar. Profile caps are given in dollars as fixed-point strings.

The cost of an order is what it can lose: ``count * price`` for a bid, ``count * (1 - price)``
for an ask (selling YES is buying NO at ``1 - price``). Exposure per market and day is the sum
of the cost of every order this guard sent to that market on that UTC day, plus this order.
It is gross notional: fills, cancels and settlements are not netted, which can only make the
cap bind earlier.

Classification
--------------
`classify(series)` reads the series metadata. Politics: the primary or a discovery category
is ``Politics`` or ``Elections``, or a tag is in `POLITICS_TAGS`. Mention: a category is
``Mentions`` or ``MENTION`` appears in the series ticker. Politics is tested first; both are
forbidden by default, so the order of the two tests changes the class in the record, not the
verdict. The category names are Kalshi's current values, not a published enum (the docs
document the fields, not their values), so a profile can override both sets.

AER
---
Every check carries an AER bundle and an `f_ext` record over the same mandate and
transaction. The package signs nothing: the caller passes ``attest(statement) -> envelope``
backed by its own key (`ed25519_attester` builds one), and the adapter puts one statement per
order into the bundle - query ``{"kind": "kalshi_decision", ...}``, value ``t_min``, window
exactly the decision second.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from .._core import DENY, PENDING, PERMIT, action_digest
from .._ext_core import f_ext
from .._ratify_core import APPROVED, DISAPPROVED, statement_bytes
from ..evidence import build_bundle, make_envelope, make_statement, pae, parse_timestamp
from ..evidence import evidence_payload_bytes
from ..verify import AerVerifyResult, verify_record

VERB = "kalshi_order"
TYPE_FIELDS = ["verb", "market_class"]

ALLOW, HOLD, FORBID = "allow", "hold", "forbid"
_DISPOSITION = {PERMIT: ALLOW, PENDING: HOLD, DENY: FORBID}

CLASS_STANDARD = "standard"
CLASS_POLITICS = "politics"
CLASS_MENTION = "mention"
CLASS_UNCLASSIFIED = "unclassified"

POLITICS_CATEGORIES = frozenset({"Politics", "Elections"})
POLITICS_TAGS = frozenset({
    "US Elections", "International elections", "Foreign Elections", "Other US Elections",
    "Primaries", "Congress", "Senate", "House", "Governor", "Politicians", "Trump",
})
MENTION_CATEGORIES = frozenset({"Mentions"})
MENTION_TICKER_MARK = "MENTION"

DEMO_BASE_URLS = (
    "https://external-api.demo.kalshi.co/trade-api/v2",
    "https://demo-api.kalshi.co/trade-api/v2",
)
DEFAULT_BASE_URL = DEMO_BASE_URLS[0]
ORDER_PATH = "/portfolio/events/orders"
LEGACY_ORDER_PATH = "/portfolio/orders"
BOOK_SIDES = ("bid", "ask")

MAX_COUNT_C = 10 ** 9  # 10 million contracts; anything larger is not a real order here


class KalshiGuardError(Exception):
    """Base class; nothing was sent."""


class NonDemoEndpoint(KalshiGuardError):
    """A base URL outside the Kalshi demo hosts, without ``allow_non_demo=True``."""


# ------------------------------------------------------------------ fixed-point parsing

_FP = re.compile(r"^(\d{1,12})(?:\.(\d{1,6}))?$")


def fixed_point(value: Any, places: int) -> Optional[int]:
    """Fixed-point string (or int) -> integer in units of 10**-places. Exact, no float.

    More decimals than ``places`` that are not zero, a sign, an exponent or a float: None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 10 ** places if value >= 0 else None
    if not isinstance(value, str):
        return None
    m = _FP.match(value.strip())
    if not m:
        return None
    whole, frac = m.group(1), m.group(2) or ""
    if len(frac) > places:
        if frac[places:].strip("0"):
            return None
        frac = frac[:places]
    return int(whole) * 10 ** places + int(frac.ljust(places, "0") or "0")


def price_e4(value: Any) -> Optional[int]:
    """Price in fixed-point dollars -> ten-thousandths of a dollar, only inside (0, 1)."""
    p = fixed_point(value, 4)
    return p if p is not None and 0 < p < 10_000 else None


def count_c(value: Any) -> Optional[int]:
    """Contract count -> hundredths of a contract, only positive."""
    c = fixed_point(value, 2)
    return c if c is not None and 0 < c <= MAX_COUNT_C else None


def dollars_e6(value: Any) -> Optional[int]:
    return fixed_point(value, 6)


def order_cost_e6(side: str, count: int, price: int) -> Optional[int]:
    """What the order can lose, in millionths of a dollar."""
    if side == "bid":
        return count * price
    if side == "ask":
        return count * (10_000 - price)
    return None


# ------------------------------------------------------------------ classification

@dataclass(frozen=True)
class SeriesInfo:
    """The series fields the classifier and the mandate read (Kalshi Get Series)."""

    ticker: str
    category: str = ""
    categories: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    frequency: str = ""

    @classmethod
    def from_api(cls, series: Mapping) -> "SeriesInfo":
        def strings(v):
            return tuple(x for x in (v or ()) if isinstance(x, str))
        return cls(ticker=str(series.get("ticker") or ""),
                   category=series.get("category") if isinstance(series.get("category"), str)
                   else "",
                   categories=strings(series.get("categories")),
                   tags=strings(series.get("tags")),
                   frequency=series.get("frequency")
                   if isinstance(series.get("frequency"), str) else "")


def classify(series: Optional[SeriesInfo], *,
             politics_categories: Iterable[str] = POLITICS_CATEGORIES,
             politics_tags: Iterable[str] = POLITICS_TAGS,
             mention_categories: Iterable[str] = MENTION_CATEGORIES) -> str:
    """Series -> market class. No series, no ticker or no category: ``unclassified``."""
    if series is None or not series.ticker or not (series.category or series.categories):
        return CLASS_UNCLASSIFIED
    cats = {series.category, *series.categories} - {""}
    if cats & set(politics_categories) or set(series.tags) & set(politics_tags):
        return CLASS_POLITICS
    if cats & set(mention_categories) or MENTION_TICKER_MARK in series.ticker.upper():
        return CLASS_MENTION
    return CLASS_STANDARD


# ------------------------------------------------------------------ profile

@dataclass(frozen=True)
class KalshiProfile:
    """The `kalshi` profile of an AAE mandate. Prices and caps as fixed-point dollar strings."""

    mandate_id: str
    principal: Mapping[str, str]
    series: Tuple[str, ...]
    categories: Tuple[str, ...]
    max_contracts: str
    min_price: str
    max_price: str
    max_market_day_exposure: str
    hold_above: str
    valid_from: str
    valid_until: str
    sides: Tuple[str, ...] = ("bid",)
    forbid_politics: bool = True
    forbid_mentions: bool = True
    ratification_authorities: Tuple[Mapping[str, str], ...] = ()

    def problem(self) -> Optional[str]:
        """None if the profile can be turned into a mandate, else the reason."""
        if not self.series:
            return "profile allows no series"
        if not self.categories:
            return "profile allows no category"
        if not self.sides or any(s not in BOOK_SIDES for s in self.sides):
            return f"sides must be a non-empty subset of {BOOK_SIDES}"
        if count_c(self.max_contracts) is None:
            return "max_contracts is not a positive fixed-point count"
        lo, hi = price_e4(self.min_price), price_e4(self.max_price)
        if lo is None or hi is None:
            return "min_price/max_price is not a price strictly between 0 and 1"
        if lo > hi:
            return "price band is inverted"
        cap, hold = dollars_e6(self.max_market_day_exposure), dollars_e6(self.hold_above)
        if cap is None or hold is None:
            return "max_market_day_exposure/hold_above is not a fixed-point dollar amount"
        if hold > cap:
            return "hold_above is above max_market_day_exposure"
        start, end = epoch_minute(self.valid_from), epoch_minute(self.valid_until)
        if start is None or end is None:
            return "valid_from/valid_until is not an RFC 3339 UTC second"
        if start > end:
            return "validity window is inverted"
        return None


def epoch_minute(ts: str) -> Optional[int]:
    seconds = parse_timestamp(ts)
    return None if seconds is None else seconds // 60


def check_action(market_class: str) -> dict:
    return {"verb": VERB, "market_class": market_class}


def build_mandate(profile: KalshiProfile) -> dict:
    """Profile -> AAE mandate for `/enforce/check`. Raises ValueError on a bad profile."""
    problem = profile.problem()
    if problem is not None:
        raise ValueError(problem)
    cap = dollars_e6(profile.max_market_day_exposure)
    hold = dollars_e6(profile.hold_above)
    common = [
        {"type": "enum", "field": "series_ticker", "values": sorted(set(profile.series))},
        {"type": "enum", "field": "category", "values": sorted(set(profile.categories))},
        {"type": "enum", "field": "side", "values": sorted(set(profile.sides))},
        {"type": "range", "field": "count_c", "lo": 1, "hi": count_c(profile.max_contracts)},
        {"type": "range", "field": "price_e4",
         "lo": price_e4(profile.min_price), "hi": price_e4(profile.max_price)},
        {"type": "range", "field": "market_day_exposure_e6", "lo": 0, "hi": cap},
        {"type": "range", "field": "t_min",
         "lo": epoch_minute(profile.valid_from), "hi": epoch_minute(profile.valid_until)},
    ]

    def grant(market_class: str, disposition: str, extra: Iterable[dict] = ()) -> dict:
        constraints = [] if disposition == FORBID else \
            [dict(c) for c in common] + [dict(c) for c in extra]
        return {"action_binding": action_digest(check_action(market_class)),
                "disposition": disposition, "type_fields": list(TYPE_FIELDS),
                "constraints": constraints}

    tradable = [CLASS_STANDARD]
    forbidden = []
    for cls, flag in ((CLASS_POLITICS, profile.forbid_politics),
                      (CLASS_MENTION, profile.forbid_mentions)):
        (forbidden if flag else tradable).append(cls)

    grants: List[dict] = [grant(cls, FORBID) for cls in forbidden]
    for cls in tradable:
        grants.append(grant(cls, ALLOW, [{"type": "range", "field": "order_cost_e6",
                                          "lo": 0, "hi": hold}]))
        if hold < cap:
            grants.append(grant(cls, HOLD, [{"type": "range", "field": "order_cost_e6",
                                             "lo": hold + 1, "hi": cap}]))

    mandate: dict = {"mandate_version": "1.0", "profile": "kalshi",
                     "mandate_id": profile.mandate_id,
                     "principal": dict(profile.principal), "grants": grants}
    if profile.ratification_authorities:
        mandate["ratification_authorities"] = [dict(a) for a in profile.ratification_authorities]
    return mandate


# ------------------------------------------------------------------ exposure ledger

class ExposureLedger:
    """Cost of the orders this guard sent, per market and UTC day, in millionths of a dollar.

    In memory by default; with ``path`` it is read at start and rewritten (temp file and
    rename) after every recorded order, so a restarted agent does not start from zero.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._rows: Dict[str, int] = {}
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or not all(
                    isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                    for k, v in data.items()):
                raise ValueError(f"exposure ledger {path} is not a {{key: int}} object")
            self._rows = dict(data)

    @staticmethod
    def _key(market: str, day: str) -> str:
        return f"{day}|{market}"

    def exposure(self, market: str, day: str) -> int:
        return self._rows.get(self._key(market, day), 0)

    def record(self, market: str, day: str, cost: int) -> None:
        key = self._key(market, day)
        self._rows[key] = self._rows.get(key, 0) + cost
        if self.path:
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._rows, fh, sort_keys=True)
            os.replace(tmp, self.path)


# ------------------------------------------------------------------ Kalshi client

def _is_demo(base_url: str) -> bool:
    return base_url.rstrip("/") in DEMO_BASE_URLS


def load_private_key(pem: Any):
    """PEM bytes/str -> RSA or Ed25519 private key (Kalshi accepts both)."""
    from cryptography.hazmat.primitives import serialization
    data = pem.encode("utf-8") if isinstance(pem, str) else pem
    return serialization.load_pem_private_key(data, password=None)


def sign_request(private_key: Any, timestamp_ms: str, method: str, path: str) -> str:
    """The KALSHI-ACCESS-SIGNATURE value: base64 over ``timestamp + METHOD + path``.

    RSA keys sign with RSA-PSS/SHA-256 and a digest-length salt, Ed25519 keys sign the message
    itself (docs.kalshi.com, Quick Start: Authenticated Requests). The query string is cut.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    message = f"{timestamp_ms}{method.upper()}{path.split('?')[0]}".encode("utf-8")
    if isinstance(private_key, Ed25519PrivateKey):
        signature = private_key.sign(message)
    else:
        signature = private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


class KalshiClient:
    """Minimal signed client for the calls the guard needs. Demo hosts only by default."""

    def __init__(self, key_id: str, private_key: Any, *, base_url: str = DEFAULT_BASE_URL,
                 allow_non_demo: bool = False, timeout: float = 10.0,
                 transport: Any = None,
                 clock_ms: Optional[Callable[[], int]] = None) -> None:
        if not _is_demo(base_url) and not allow_non_demo:
            raise NonDemoEndpoint(
                f"{base_url} is not a Kalshi demo host {DEMO_BASE_URLS}; "
                "pass allow_non_demo=True to use it")
        import httpx
        self.base_url = base_url.rstrip("/")
        self._root = urlparse(self.base_url).path  # "/trade-api/v2"
        self.key_id = key_id
        self.private_key = load_private_key(private_key) \
            if isinstance(private_key, (str, bytes)) else private_key
        self._clock_ms = clock_ms or (lambda: int(_dt.datetime.now(
            _dt.timezone.utc).timestamp() * 1000))
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _headers(self, method: str, path: str) -> dict:
        ts = str(self._clock_ms())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": sign_request(self.private_key, ts, method,
                                                        self._root + path),
                "Content-Type": "application/json"}

    def request(self, method: str, path: str, *, params: Optional[dict] = None,
                body: Optional[dict] = None):
        return self._http.request(method, path, params=params,
                                  content=None if body is None else json.dumps(body),
                                  headers=self._headers(method, path))

    def create_order(self, order: Mapping) -> Tuple[int, Any]:
        """POST /portfolio/events/orders. Returns ``(status_code, parsed body or text)``."""
        r = self.request("POST", ORDER_PATH, body=dict(order))
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    def series_for(self, market_ticker: str) -> Optional[SeriesInfo]:
        """market -> event -> series, three GETs. None on any failure (fail-closed upstream)."""
        try:
            m = self.request("GET", f"/markets/{market_ticker}")
            event = (m.json().get("market") or {}).get("event_ticker") if m.status_code == 200 \
                else None
            if not isinstance(event, str):
                return None
            e = self.request("GET", f"/events/{event}")
            series = (e.json().get("event") or {}).get("series_ticker") \
                if e.status_code == 200 else None
            if not isinstance(series, str):
                return None
            s = self.request("GET", f"/series/{series}")
            data = s.json().get("series") if s.status_code == 200 else None
            return SeriesInfo.from_api(data) if isinstance(data, Mapping) else None
        except Exception:
            return None


# ------------------------------------------------------------------ attest / approve

def ed25519_attester(source_id: str, private_key: Any,
                     keyid: Optional[str] = None) -> Callable[[dict], dict]:
    """``attest(statement) -> envelope`` signed with an Ed25519 key the caller holds.

    The statement's ``source_id`` must be ``source_id``; the trust list that verifies the
    receipt maps that id to the public key (`trust_list_for`).
    """
    def attest(statement: dict) -> dict:
        statement = dict(statement, source_id=source_id)
        payload = evidence_payload_bytes(statement)
        if payload is None:
            raise ValueError("statement is not canonicalizable")
        from ..evidence import PAYLOAD_TYPE
        sig = private_key.sign(pae(PAYLOAD_TYPE, payload))
        entry = {"sig": base64.b64encode(sig).decode("ascii")}
        if keyid:
            entry["keyid"] = keyid
        return make_envelope(statement, [entry])
    return attest


def trust_list_for(source_id: str, public_key: Any, keyid: Optional[str] = None) -> dict:
    """Trust list for `moltrust-verify` naming one Ed25519 source."""
    raw = public_key.public_bytes_raw() if hasattr(public_key, "public_bytes_raw") \
        else bytes(public_key)
    key = {"algorithm": "ed25519", "public_key": base64.b64encode(raw).decode("ascii")}
    if keyid:
        key["keyid"] = keyid
    return {"trust_list_version": 1, "sources": {source_id: {"keys": [key]}}}


class TelegramApprover:
    """Asks a human on Telegram to approve a hold, then signs the ratification.

    Transport-agnostic on purpose: ``send(text, buttons)`` posts the question with inline
    buttons ``[(label, callback_data), ...]`` and ``wait(token, timeout_s)`` returns
    ``"approve"``, ``"reject"`` or None (no answer in time). The package itself never talks to
    Telegram; an operator wires these two callables to its own bot.

    On approve it signs ``APPROVED`` with ``signing_key`` as ``authority_did``, on reject
    ``DISAPPROVED``. On no answer it returns None and the order stays unsent. The authority
    must be the principal or a role named in the mandate, or `/enforce/ratify` rejects it.
    """

    PREFIX = "kg"

    def __init__(self, send: Callable[[str, List[Tuple[str, str]]], Any],
                 wait: Callable[[str, float], Optional[str]], *, authority_did: str,
                 signing_key: Any, timeout_s: float = 300.0) -> None:
        self.send, self.wait = send, wait
        self.authority_did, self.signing_key = authority_did, signing_key
        self.timeout_s = timeout_s

    def __call__(self, outcome: "OrderOutcome") -> Optional[Tuple[str, dict]]:
        token = uuid.uuid4().hex[:16]
        tx = outcome.transaction
        text = (f"KG-ENF hold: Kalshi order needs approval\n"
                f"market {tx.get('market_ticker')} side {tx.get('side')} "
                f"count {tx.get('count_c', 0) / 100:g} price {tx.get('price_e4', 0) / 10000:.4f}\n"
                f"cost ${tx.get('order_cost_e6', 0) / 1e6:,.2f}, "
                f"market/day after ${tx.get('market_day_exposure_e6', 0) / 1e6:,.2f}\n"
                f"reason: {outcome.reason}\n"
                f"record {outcome.verdict.core_digest}")
        self.send(text, [("Approve", f"{self.PREFIX}|approve|{token}"),
                         ("Reject", f"{self.PREFIX}|reject|{token}")])
        answer = self.wait(token, self.timeout_s)
        if answer not in ("approve", "reject"):
            return None
        decision = APPROVED if answer == "approve" else DISAPPROVED
        sig = self.signing_key.sign(
            statement_bytes(outcome.verdict.core_digest, decision, self.authority_did))
        return decision, {"mandate": outcome.mandate, "authority": self.authority_did,
                          "signature": sig.hex()}


# ------------------------------------------------------------------ explanation

def explain(trace: Iterable[Mapping]) -> Optional[str]:
    """Name the constraints that failed in every grant the kernel tried (``t_min`` counts the
    grants, as every allow/hold grant carries the window)."""
    entries = [e for e in trace if isinstance(e, Mapping)]
    tried = sum(1 for e in entries if e.get("field") == "t_min")
    if not tried:
        return None
    fails: Dict[str, List[Mapping]] = {}
    for e in entries:
        if e.get("result") == "FAIL" and isinstance(e.get("field"), str):
            fails.setdefault(e["field"], []).append(e)
    parts = []
    for name, hits in fails.items():
        if len(hits) < tried:
            continue
        value = hits[-1].get("value")
        ranges = [h["bound"] for h in hits if isinstance(h.get("bound"), Mapping)]
        if value is None:
            parts.append(f"{name} unknown")
        elif ranges:
            lo = min(r.get("lo") for r in ranges if isinstance(r.get("lo"), int))
            hi = max(r.get("hi") for r in ranges if isinstance(r.get("hi"), int))
            parts.append(f"{name} {value} outside {lo}..{hi}")
        else:
            parts.append(f"{name} {value!r} not allowed")
    return "; ".join(parts) or None


# ------------------------------------------------------------------ guard

@dataclass
class OrderOutcome:
    order: dict
    transaction: dict
    mandate: dict
    verdict: Any
    disposition: str
    reason: str
    aer_record: Optional[dict] = None
    aer_bundle: Optional[dict] = None
    ratification: Any = None
    authority_proof: Optional[dict] = None
    sent: bool = False
    kalshi_status: Optional[int] = None
    kalshi_response: Any = None
    receipt_path: Optional[str] = None
    check_ms: Optional[float] = None

    def aer_verify(self, trust_list: Mapping) -> AerVerifyResult:
        """Offline V1-V4 over this order's receipt. No network."""
        return verify_record(self.aer_record, self.aer_bundle, self.mandate,
                             self.transaction, trust_list)

    def receipt(self) -> dict:
        """The receipt as `moltrust-verify --input` reads it, plus what happened."""
        out = {"record": self.aer_record, "bundle": self.aer_bundle, "mandate": self.mandate,
               "transaction": self.transaction, "order": self.order,
               "disposition": self.disposition, "reason": self.reason, "sent": self.sent,
               "server_record": {"verdict": getattr(self.verdict, "verdict", None),
                                 "from_server": getattr(self.verdict, "from_server", None),
                                 "reason": getattr(self.verdict, "reason", None),
                                 "core": getattr(self.verdict, "core", None),
                                 "core_digest": getattr(self.verdict, "core_digest", None)}}
        if self.ratification is not None:
            out["ratification"] = {"status": self.ratification.status,
                                   "decision": self.ratification.decision,
                                   "core": self.ratification.core,
                                   "core_digest": self.ratification.core_digest}
            out["authority_proof"] = self.authority_proof
        if self.kalshi_status is not None:
            out["kalshi"] = {"status": self.kalshi_status, "response": self.kalshi_response}
        return out


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _rfc3339(seconds: int) -> str:
    return _dt.datetime.fromtimestamp(seconds, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KalshiOrderGuard:
    """Sends a Kalshi order only when MolTrust's verdict lets it through.

    Parameters
    ----------
    client
        A `moltrust_enforce.EnforceClient` (or anything with the same `check`/`ratify`).
    profile
        The `KalshiProfile`; turned into a mandate once.
    kalshi
        Anything with ``create_order(order) -> (status, body)``; `KalshiClient` for the demo.
    series_for
        ``series_for(market_ticker) -> SeriesInfo | None``; defaults to ``kalshi.series_for``.
        None means unclassified, which is a DENY.
    approver
        Optional ``approver(outcome) -> (decision, authority_proof) | None`` for a hold, e.g.
        `TelegramApprover`. Without one, or without an answer, a hold sends nothing.
    attest
        Optional ``attest(statement) -> envelope`` (see AER above).
    ledger
        `ExposureLedger`; in memory if omitted.
    receipts_dir
        If set, every decision writes ``<dir>/<n>-<client_order_id>.json``.
    clock
        Returns an aware UTC datetime; injectable for tests.
    """

    def __init__(self, client: Any, profile: KalshiProfile, kalshi: Any, *,
                 series_for: Optional[Callable[[str], Optional[SeriesInfo]]] = None,
                 approver: Optional[Callable[["OrderOutcome"],
                                             Optional[Tuple[str, dict]]]] = None,
                 attest: Optional[Callable[[dict], dict]] = None,
                 ledger: Optional[ExposureLedger] = None,
                 receipts_dir: Optional[str] = None,
                 clock: Callable[[], _dt.datetime] = _utc_now,
                 timer: Optional[Callable[[], float]] = None) -> None:
        self.client = client
        self.profile = profile
        self.mandate = build_mandate(profile)
        self.kalshi = kalshi
        self.series_for = series_for or kalshi.series_for
        self.approver = approver
        self.attest = attest
        self.ledger = ledger or ExposureLedger()
        self.receipts_dir = receipts_dir
        self.clock = clock
        import time
        self.timer = timer or time.perf_counter
        self._n = 0

    def transaction_for(self, order: Mapping, now: _dt.datetime) -> dict:
        """Order -> transaction. Every value derived in code; underivable values stay absent."""
        ticker = order.get("ticker") if isinstance(order.get("ticker"), str) else None
        series = None
        if ticker:
            try:
                series = self.series_for(ticker)
            except Exception:
                series = None
        market_class = classify(series)
        tx: Dict[str, Any] = {"action": check_action(market_class),
                              "t_min": int(now.timestamp()) // 60}
        if ticker:
            tx["market_ticker"] = ticker
        if series is not None:
            if series.ticker:
                tx["series_ticker"] = series.ticker
            if series.category:
                tx["category"] = series.category
        side = order.get("side")
        if side in BOOK_SIDES:
            tx["side"] = side
        c, p = count_c(order.get("count")), price_e4(order.get("price"))
        if c is not None:
            tx["count_c"] = c
        if p is not None:
            tx["price_e4"] = p
        if c is not None and p is not None and side in BOOK_SIDES and ticker:
            cost = order_cost_e6(side, c, p)
            tx["order_cost_e6"] = cost
            tx["market_day_exposure_e6"] = \
                self.ledger.exposure(ticker, now.strftime("%Y-%m-%d")) + cost
        return tx

    def decide(self, order: Mapping) -> OrderOutcome:
        """Verdict and receipt only; sends nothing."""
        order = dict(order)
        order.setdefault("client_order_id", str(uuid.uuid4()))
        now = self.clock()
        tx = self.transaction_for(order, now)
        t0 = self.timer()
        verdict = self.client.check(self.mandate, tx)
        check_ms = (self.timer() - t0) * 1000.0
        server = getattr(verdict, "verdict", DENY)
        reason = getattr(verdict, "reason", "")
        if server == DENY:
            if "unaddressed action" in reason:
                reason = (f"market class {tx['action']['market_class']} has no grant "
                          "(not in the mandate)")
            elif "disposition=forbid" in reason:
                reason = f"{tx['action']['market_class']} markets are forbidden by the mandate"
            else:
                reason = explain(getattr(verdict, "trace", ()) or ()) or reason
        out = OrderOutcome(order=order, transaction=tx, mandate=self.mandate, verdict=verdict,
                           disposition=_DISPOSITION.get(server, FORBID), reason=reason,
                           check_ms=check_ms)
        self._attach_aer(out, now)
        if not getattr(verdict, "from_server", True):
            # Transport failure, error status or unreadable answer: the client's local DENY.
            # There is no server record to compare, so say what happened instead of calling
            # it a recompute mismatch.
            out.disposition = FORBID
            out.reason = f"no server verdict: {getattr(verdict, 'reason', '')}"
        elif out.aer_record["verdict"] != server:
            out.disposition = FORBID
            out.reason = (f"server verdict {server} does not recompute "
                          f"(local {out.aer_record['verdict']})")
        return out

    def place_order(self, order: Mapping) -> OrderOutcome:
        """Decide; send ``order`` unchanged only on allow or an approved hold."""
        out = self.decide(order)
        if out.disposition == HOLD:
            answer = self.approver(out) if self.approver is not None else None
            if answer is None:
                out.reason = f"hold, no approval: {out.reason}"
            else:
                decision, proof = answer
                out.authority_proof = proof
                prior = {"core": out.verdict.core, "core_digest": out.verdict.core_digest}
                out.ratification = self.client.ratify(prior, decision, proof)
                if not getattr(out.ratification, "approved", False):
                    out.reason = (f"hold not approved: {decision} "
                                  f"{getattr(out.ratification, 'reason', '')}").strip()
        go = out.disposition == ALLOW or (
            out.disposition == HOLD and getattr(out.ratification, "approved", False))
        if go:
            status, body = self.kalshi.create_order(out.order)
            out.kalshi_status, out.kalshi_response = status, body
            out.sent = True
            if 200 <= status < 300:
                self.ledger.record(out.transaction["market_ticker"],
                                   self.clock().strftime("%Y-%m-%d"),
                                   out.transaction["order_cost_e6"])
        self._write_receipt(out)
        return out

    def _attach_aer(self, out: OrderOutcome, now: _dt.datetime) -> None:
        ts = _rfc3339(int(now.timestamp()))
        items = []
        if self.attest is not None:
            statement = make_statement(
                str(self.profile.principal.get("did", "")),
                {"kind": "kalshi_decision", "mandate": self.profile.mandate_id,
                 "market": out.transaction.get("market_ticker", ""),
                 "client_order_id": str(out.order.get("client_order_id", ""))},
                out.transaction["t_min"], ts, ts,
                f"{out.order.get('client_order_id')}:{out.transaction['t_min']}")
            items.append(self.attest(statement))
        out.aer_bundle = build_bundle(items, self.mandate, out.transaction, ts)
        out.aer_record = f_ext(self.mandate, out.transaction, out.aer_bundle)

    def _write_receipt(self, out: OrderOutcome) -> None:
        if not self.receipts_dir:
            return
        os.makedirs(self.receipts_dir, exist_ok=True)
        self._n += 1
        cid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(out.order.get("client_order_id")))[:64]
        path = os.path.join(self.receipts_dir, f"{self._n:03d}-{out.disposition}-{cid}.json")
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out.receipt(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        out.receipt_path = path


__all__ = [
    "ALLOW", "HOLD", "FORBID", "VERB", "TYPE_FIELDS", "CLASS_STANDARD", "CLASS_POLITICS",
    "CLASS_MENTION", "CLASS_UNCLASSIFIED", "POLITICS_CATEGORIES", "POLITICS_TAGS",
    "MENTION_CATEGORIES", "DEMO_BASE_URLS", "DEFAULT_BASE_URL", "ORDER_PATH",
    "LEGACY_ORDER_PATH", "SeriesInfo", "KalshiProfile", "KalshiClient", "KalshiOrderGuard",
    "OrderOutcome", "ExposureLedger", "TelegramApprover", "KalshiGuardError",
    "NonDemoEndpoint", "build_mandate", "classify", "check_action", "explain",
    "fixed_point", "price_e4", "count_c", "dollars_e6", "order_cost_e6", "sign_request",
    "load_private_key", "ed25519_attester", "trust_list_for", "epoch_minute",
]
