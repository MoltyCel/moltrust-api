"""MolCheck before every Polymarket order is signed.

The adapter sits between an agent and its own order signing. Before an order is signed it
asks `POST /enforce/check` for a verdict and hands the order to the caller's ``submit``
only when the verdict is PERMIT, or PENDING with a ratification that came back RATIFIED and
APPROVED. A DENY hands over nothing, so nothing is signed. Every decision leaves a MolCheck
record (an AER record plus its evidence bundle) that `moltrust-verify` recomputes offline.

    from moltrust_enforce import EnforceClient
    from moltrust_enforce.adapters.polymarket import PolymarketOrderGuard, PolymarketProfile

    guard = PolymarketOrderGuard(EnforceClient("https://api.moltrust.ch", api_key=KEY),
                                 profile, submit=my_sign_and_post, attest=attest,
                                 commitment=CommitmentParties(signer=SIGNER, maker=MAKER),
                                 receipts_dir="receipts")
    outcome = guard.place_order({"token_id": "1099...4702", "side": "BUY",
                                 "price": "0.45", "size": "10"})

`my_sign_and_post(order)` is the caller's own code. It holds the signing key, builds the
EIP-712 order and talks to Polymarket. This package ships no Polymarket client, never sees
a key and sends nothing to a Polymarket endpoint.

Order -> transaction
--------------------
The guard reads four fields of the order the agent wants to place, in the shape of the
CLOB client's order arguments, plus one flag:

- ``token_id``  the outcome token, a decimal string (a uint256 does not fit a JSON number
                the kernel can hash, so it stays a string).
- ``side``      ``BUY`` or ``SELL``.
- ``price``     fixed-point string strictly between 0 and 1, at most four decimals
                (``price_bps``, basis points of one USDC).
- ``size``      fixed-point string of shares, at most two decimals (``size_c``, hundredths
                of a share).
- ``neg_risk``  optional bool; selects the NegRisk exchange instead of the CTF exchange.

Floats are refused: a float price is not a price the guard can compare exactly, so it stays
absent and fails its constraint.

The cost of an order is what it can lose: ``size * price`` for a BUY, ``size * (1 - price)``
for a SELL (selling an outcome share at ``price`` gives up its payout of 1). Units are
millionths of a USDC (``order_cost_e6``). Exposure per market and UTC day is the cost of
every order this guard handed over for that token on that day, plus this order; exposure per
day is the same across all tokens. Both are gross: fills, cancels and settlements are not
netted, which can only make a cap bind earlier.

Profile -> mandate
------------------
One check per order. The action is ``{"verb": "polymarket_order"}``. Two grants:

- ``allow``  order cost up to ``hold_above``.
- ``hold``   order cost above ``hold_above`` up to ``max_market_day_exposure``.

Both carry the same constraints: token id from the profile's market list, side, size per
order, price band, exposure per market and day, exposure per day, exchange and validity
window. A field the adapter cannot derive stays absent and fails its constraint: unknown
means forbid. Nothing outside the profile is allowed; the mandate is deny-by-default.

Commitment (``0x4D54``)
-----------------------
With ``commitment=CommitmentParties(signer, maker)`` the guard also builds the commitment
from the MolTrust Polymarket commitment encoding before the check: a preimage that names the
principal's DID, signer, maker, exchange, token, side, the mandate's price band, a validity
window and a nonce, and ``metadata = 0x4D54 || 0x01 || keccak256(preimage)[0:29]``. The
metadata goes into the transaction, so the MolCheck record binds it, and on allow or an
approved hold into the order handed to ``submit`` as ``metadata``; the order's EIP-712
signature then covers it. The guard does not publish or anchor the preimage: that is the
caller's step, and without it the commitment proves only that the maker fixed a hash when
signing. `verify_commitment` and `check_fill` recompute both sides offline.

AER
---
Every check carries an AER bundle and an `f_ext` record over the same mandate and
transaction. The package signs nothing: the caller passes ``attest(statement) -> envelope``
backed by its own key (`ed25519_attester` builds one), and the adapter puts one statement
per order into the bundle - query ``{"kind": "polymarket_decision", ...}``, value ``t_min``,
window exactly the decision second.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .._core import DENY, PENDING, PERMIT, action_digest
from .._ext_core import f_ext
from .._ratify_core import APPROVED, DISAPPROVED, statement_bytes
from ..evidence import PAYLOAD_TYPE, build_bundle, evidence_payload_bytes, make_envelope
from ..evidence import make_statement, pae, parse_timestamp
from ..verify import AerVerifyResult, verify_record

VERB = "polymarket_order"
TYPE_FIELDS = ["verb"]
ACTION = {"verb": VERB}

ALLOW, HOLD, FORBID = "allow", "hold", "forbid"
_DISPOSITION = {PERMIT: ALLOW, PENDING: HOLD, DENY: FORBID}

SIDES = ("BUY", "SELL")
CHAIN = "eip155:137"
CTF_EXCHANGE = "0xe111180000d2663c0091e4f400237545b87b996b"
NEG_RISK_EXCHANGE = "0xe2222d279d744050d28e00520010520000310f59"
EXCHANGES = (CTF_EXCHANGE, NEG_RISK_EXCHANGE)

COMMIT_MAGIC = bytes.fromhex("4d54")
COMMIT_VERSION = 0x01
COMMIT_PREFIX = "0x4d5401"
PREIMAGE_HEADER = "moltrust-pm-commit/1"
PREIMAGE_FIELDS = ("did", "signer", "maker", "chain", "exchange", "tokenId", "side",
                   "priceMinBps", "priceMaxBps", "notBeforeMs", "notAfterMs", "nonce")

MAX_SIZE_C = 10 ** 9  # 10 million shares; anything larger is not a real order here
_TOKEN_RE = re.compile(r"^[1-9][0-9]{0,77}$")
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_NONCE_RE = re.compile(r"^0x[0-9a-f]{2,64}$")


class PolymarketGuardError(Exception):
    """Base class; nothing was handed over."""


# ------------------------------------------------------------------ keccak256

_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_ROT = (
    (0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56), (27, 20, 39, 8, 14),
)
_M64 = (1 << 64) - 1


def _rol(v: int, n: int) -> int:
    return ((v << n) | (v >> (64 - n))) & _M64 if n else v


def _keccak_f(a: List[List[int]]) -> None:
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Keccak-256 as Ethereum uses it (original padding 0x01, not SHA3-256's 0x06)."""
    rate = 136
    msg = bytearray(data)
    msg.append(0x01)
    while len(msg) % rate:
        msg.append(0)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            a[x][y] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out


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


def price_bps(value: Any) -> Optional[int]:
    """Price in fixed-point USDC -> basis points, only strictly inside (0, 1)."""
    p = fixed_point(value, 4)
    return p if p is not None and 0 < p < 10_000 else None


def size_c(value: Any) -> Optional[int]:
    """Share count -> hundredths of a share, only positive."""
    c = fixed_point(value, 2)
    return c if c is not None and 0 < c <= MAX_SIZE_C else None


def usdc_e6(value: Any) -> Optional[int]:
    return fixed_point(value, 6)


def order_cost_e6(side: str, size: int, price: int) -> Optional[int]:
    """What the order can lose, in millionths of a USDC."""
    if side == "BUY":
        return size * price
    if side == "SELL":
        return size * (10_000 - price)
    return None


def token_id(value: Any) -> Optional[str]:
    """Outcome token id as a canonical decimal string (no sign, no leading zero), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not _TOKEN_RE.match(value):
        return None
    return value if int(value) < 2 ** 256 else None


def exchange_for(order: Mapping) -> Optional[str]:
    neg = order.get("neg_risk", False)
    if not isinstance(neg, bool):
        return None
    return NEG_RISK_EXCHANGE if neg else CTF_EXCHANGE


# ------------------------------------------------------------------ profile

@dataclass(frozen=True)
class PolymarketProfile:
    """The `polymarket` profile of an AAE mandate. Prices, sizes and caps as fixed-point
    strings: prices and caps in USDC, sizes in shares."""

    mandate_id: str
    principal: Mapping[str, str]
    markets: Tuple[str, ...]
    max_size: str
    min_price: str
    max_price: str
    max_market_day_exposure: str
    max_day_exposure: str
    hold_above: str
    valid_from: str
    valid_until: str
    sides: Tuple[str, ...] = ("BUY",)
    exchanges: Tuple[str, ...] = EXCHANGES
    ratification_authorities: Tuple[Mapping[str, str], ...] = ()

    def problem(self) -> Optional[str]:
        """None if the profile can be turned into a mandate, else the reason."""
        if not self.markets:
            return "profile allows no market"
        if any(token_id(m) != m for m in self.markets):
            return "markets must be outcome token ids as canonical decimal strings"
        if not self.sides or any(s not in SIDES for s in self.sides):
            return f"sides must be a non-empty subset of {SIDES}"
        if not self.exchanges or any(e not in EXCHANGES for e in self.exchanges):
            return f"exchanges must be a non-empty subset of {EXCHANGES}"
        if size_c(self.max_size) is None:
            return "max_size is not a positive fixed-point share count"
        lo, hi = price_bps(self.min_price), price_bps(self.max_price)
        if lo is None or hi is None:
            return "min_price/max_price is not a price strictly between 0 and 1"
        if lo > hi:
            return "price band is inverted"
        cap, day, hold = (usdc_e6(self.max_market_day_exposure), usdc_e6(self.max_day_exposure),
                          usdc_e6(self.hold_above))
        if cap is None or day is None or hold is None:
            return ("max_market_day_exposure/max_day_exposure/hold_above is not a fixed-point "
                    "USDC amount")
        if hold > cap:
            return "hold_above is above max_market_day_exposure"
        if cap > day:
            return "max_market_day_exposure is above max_day_exposure"
        start, end = epoch_minute(self.valid_from), epoch_minute(self.valid_until)
        if start is None or end is None:
            return "valid_from/valid_until is not an RFC 3339 UTC second"
        if start > end:
            return "validity window is inverted"
        return None


def epoch_minute(ts: str) -> Optional[int]:
    seconds = parse_timestamp(ts)
    return None if seconds is None else seconds // 60


def build_mandate(profile: PolymarketProfile) -> dict:
    """Profile -> AAE mandate for `/enforce/check`. Raises ValueError on a bad profile."""
    problem = profile.problem()
    if problem is not None:
        raise ValueError(problem)
    cap = usdc_e6(profile.max_market_day_exposure)
    hold = usdc_e6(profile.hold_above)
    common = [
        {"type": "enum", "field": "token_id", "values": sorted(set(profile.markets))},
        {"type": "enum", "field": "side", "values": sorted(set(profile.sides))},
        {"type": "enum", "field": "exchange", "values": sorted(set(profile.exchanges))},
        {"type": "range", "field": "size_c", "lo": 1, "hi": size_c(profile.max_size)},
        {"type": "range", "field": "price_bps",
         "lo": price_bps(profile.min_price), "hi": price_bps(profile.max_price)},
        {"type": "range", "field": "market_day_exposure_e6", "lo": 0, "hi": cap},
        {"type": "range", "field": "day_exposure_e6", "lo": 0,
         "hi": usdc_e6(profile.max_day_exposure)},
        {"type": "range", "field": "t_min",
         "lo": epoch_minute(profile.valid_from), "hi": epoch_minute(profile.valid_until)},
    ]

    def grant(disposition: str, lo: int, hi: int) -> dict:
        return {"action_binding": action_digest(dict(ACTION)), "disposition": disposition,
                "type_fields": list(TYPE_FIELDS),
                "constraints": [dict(c) for c in common]
                + [{"type": "range", "field": "order_cost_e6", "lo": lo, "hi": hi}]}

    grants = [grant(ALLOW, 0, hold)]
    if hold < cap:
        grants.append(grant(HOLD, hold + 1, cap))
    mandate: dict = {"mandate_version": "1.0", "profile": "polymarket",
                     "mandate_id": profile.mandate_id,
                     "principal": dict(profile.principal), "grants": grants}
    if profile.ratification_authorities:
        mandate["ratification_authorities"] = [dict(a) for a in profile.ratification_authorities]
    return mandate


# ------------------------------------------------------------------ commitment

@dataclass(frozen=True)
class CommitmentParties:
    """The two addresses a commitment names: ``signer`` authorises the order, ``maker`` is
    the proxy wallet that appears on-chain. ``ttl_s`` is the commitment's validity window."""

    signer: str
    maker: str
    ttl_s: int = 3600

    def problem(self) -> Optional[str]:
        if not (isinstance(self.signer, str) and _ADDR_RE.match(self.signer)):
            return "signer is not a 20-byte hex address"
        if not (isinstance(self.maker, str) and _ADDR_RE.match(self.maker)):
            return "maker is not a 20-byte hex address"
        if isinstance(self.ttl_s, bool) or not isinstance(self.ttl_s, int) or self.ttl_s <= 0:
            return "ttl_s is not a positive number of seconds"
        return None


def preimage_text(fields: Mapping[str, Any]) -> str:
    """The commitment preimage: header line, then ``key=value`` in the fixed order.

    UTF-8, LF, no trailing newline, NFC; addresses lowercase, integers decimal. Raises
    ValueError on a missing field or a value that cannot be written on one line.
    """
    lines = [PREIMAGE_HEADER]
    for key in PREIMAGE_FIELDS:
        if key not in fields:
            raise ValueError(f"preimage field {key} is missing")
        value = fields[key]
        if isinstance(value, bool):
            raise ValueError(f"preimage field {key} is a bool")
        text = str(value)
        if key in ("signer", "maker", "exchange"):
            text = text.lower()
        if not text or text != text.strip() or "\n" in text or "\r" in text:
            raise ValueError(f"preimage field {key} is empty or not one clean line")
        lines.append(f"{key}={text}")
    return unicodedata.normalize("NFC", "\n".join(lines))


def parse_preimage(text: str) -> Dict[str, str]:
    """Preimage text -> fields. Raises ValueError unless the text is in canonical form."""
    lines = text.split("\n")
    if not lines or lines[0] != PREIMAGE_HEADER or len(lines) != len(PREIMAGE_FIELDS) + 1:
        raise ValueError("not a moltrust-pm-commit/1 preimage")
    fields: Dict[str, str] = {}
    for key, line in zip(PREIMAGE_FIELDS, lines[1:]):
        name, sep, value = line.partition("=")
        if name != key or not sep:
            raise ValueError(f"expected field {key}, got {line[:40]!r}")
        fields[key] = value
    if preimage_text(fields) != text:
        raise ValueError("preimage is not in canonical form")
    return fields


def commitment_metadata(preimage: str) -> str:
    """``bytes32 metadata`` as 0x-hex: magic 0x4D54, version 0x01, keccak256[0:29]."""
    digest = keccak256(preimage.encode("utf-8"))
    return "0x" + (COMMIT_MAGIC + bytes([COMMIT_VERSION]) + digest[:29]).hex()


def verify_commitment(preimage: str, metadata: str) -> bool:
    """True when ``metadata`` carries the MolTrust prefix and the digest of ``preimage``."""
    if not isinstance(metadata, str) or not isinstance(preimage, str):
        return False
    m = metadata.lower()
    if not re.fullmatch(r"0x[0-9a-f]{64}", m) or not m.startswith(COMMIT_PREFIX):
        return False
    return commitment_metadata(preimage) == m


def build_commitment(parties: CommitmentParties, did: str, exchange: str, token: str,
                     side: str, band: Tuple[int, int], not_before_ms: int,
                     nonce: str) -> Dict[str, str]:
    """Preimage and metadata for one order. Raises ValueError on a bad input."""
    problem = parties.problem()
    if problem is not None:
        raise ValueError(problem)
    if not _NONCE_RE.match(nonce):
        raise ValueError("nonce is not lowercase 0x-hex")
    fields = {"did": did, "signer": parties.signer, "maker": parties.maker, "chain": CHAIN,
              "exchange": exchange, "tokenId": token, "side": side,
              "priceMinBps": band[0], "priceMaxBps": band[1],
              "notBeforeMs": not_before_ms,
              "notAfterMs": not_before_ms + parties.ttl_s * 1000, "nonce": nonce}
    text = preimage_text(fields)
    return {"preimage": text, "metadata": commitment_metadata(text)}


@dataclass(frozen=True)
class FillCheck:
    ok: bool
    problems: Tuple[str, ...]


def check_fill(preimage: str, *, maker: str, token: Any, side: str,
               maker_amount_filled: int, taker_amount_filled: int,
               fill_timestamp_ms: int, metadata: Optional[str] = None) -> FillCheck:
    """Steps 2 and 4 to 6 of the commitment verification against one ``OrderFilled``.

    The implied price is ``maker/taker`` for a BUY and ``taker/maker`` for a SELL; the band
    is compared by cross-multiplication, so no division and no rounding. Anchoring (step 7)
    and the signature level (step 8) are not checked here.
    """
    problems: List[str] = []
    try:
        f = parse_preimage(preimage)
    except ValueError as exc:
        return FillCheck(False, (f"preimage: {exc}",))
    if metadata is not None and not verify_commitment(preimage, metadata):
        problems.append("metadata does not carry this preimage")
    if not isinstance(maker, str) or maker.lower() != f["maker"]:
        problems.append("maker differs")
    if token_id(token) != f["tokenId"]:
        problems.append("tokenId differs")
    if side != f["side"]:
        problems.append("side differs")
    amounts = (maker_amount_filled, taker_amount_filled)
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in amounts):
        problems.append("fill amounts are not positive integers")
    elif side in SIDES:
        usdc, shares = amounts if side == "BUY" else amounts[::-1]
        lo, hi = int(f["priceMinBps"]), int(f["priceMaxBps"])
        if not lo * shares <= usdc * 10_000 <= hi * shares:
            problems.append("implied price outside the band")
    if not int(f["notBeforeMs"]) <= fill_timestamp_ms <= int(f["notAfterMs"]):
        problems.append("fill outside the commitment window")
    return FillCheck(not problems, tuple(problems))


# ------------------------------------------------------------------ exposure ledger

class ExposureLedger:
    """Cost of the orders this guard handed over, per token and UTC day, in millionths of a
    USDC.

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

    def day_total(self, day: str) -> int:
        prefix = f"{day}|"
        return sum(v for k, v in self._rows.items() if k.startswith(prefix))

    def record(self, market: str, day: str, cost: int) -> None:
        key = self._key(market, day)
        self._rows[key] = self._rows.get(key, 0) + cost
        if self.path:
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._rows, fh, sort_keys=True)
            os.replace(tmp, self.path)


# ------------------------------------------------------------------ attest / approve

def ed25519_attester(source_id: str, private_key: Any,
                     keyid: Optional[str] = None) -> Callable[[dict], dict]:
    """``attest(statement) -> envelope`` signed with an Ed25519 key the caller holds.

    The statement's ``source_id`` must be ``source_id``; the trust list that verifies the
    MolCheck record maps that id to the public key (`trust_list_for`).
    """
    def attest(statement: dict) -> dict:
        statement = dict(statement, source_id=source_id)
        payload = evidence_payload_bytes(statement)
        if payload is None:
            raise ValueError("statement is not canonicalizable")
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


def ratification_proof(outcome: "OrderOutcome", approve: bool, authority_did: str,
                       signing_key: Any) -> Tuple[str, dict]:
    """``(decision, authority_proof)`` for a hold, signed by the approver's Ed25519 key.

    The authority must be the principal or a role named in the mandate, or
    `/enforce/ratify` rejects it. Wire it into ``approver`` behind whatever asks the human.
    """
    decision = APPROVED if approve else DISAPPROVED
    sig = signing_key.sign(statement_bytes(outcome.verdict.core_digest, decision,
                                           authority_did))
    return decision, {"mandate": outcome.mandate, "authority": authority_did,
                      "signature": sig.hex()}


# ------------------------------------------------------------------ explanation

def explain(trace: Iterable[Mapping]) -> Optional[str]:
    """Name the constraints that failed in every grant the kernel tried (``t_min`` counts the
    grants, as every grant carries the window)."""
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
    commitment: Optional[Dict[str, str]] = None
    ratification: Any = None
    authority_proof: Optional[dict] = None
    sent: bool = False
    submit_result: Any = None
    receipt_path: Optional[str] = None
    check_ms: Optional[float] = None

    @property
    def molcheck_record(self) -> Optional[dict]:
        """The MolCheck record of this decision: the AER record."""
        return self.aer_record

    def aer_verify(self, trust_list: Mapping) -> AerVerifyResult:
        """Offline V1-V4 over this order's MolCheck record. No network."""
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
        if self.commitment is not None:
            out["commitment"] = dict(self.commitment)
        if self.ratification is not None:
            out["ratification"] = {"status": self.ratification.status,
                                   "decision": self.ratification.decision,
                                   "core": self.ratification.core,
                                   "core_digest": self.ratification.core_digest}
            out["authority_proof"] = self.authority_proof
        if self.sent:
            out["submit_result"] = self.submit_result
        return out


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _rfc3339(seconds: int) -> str:
    return _dt.datetime.fromtimestamp(seconds, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _random_nonce() -> str:
    return "0x" + secrets.token_hex(4)


class PolymarketOrderGuard:
    """Hands a Polymarket order to the caller's signer only when MolTrust's verdict lets it
    through.

    Parameters
    ----------
    client
        A `moltrust_enforce.EnforceClient` (or anything with the same `check`/`ratify`).
    profile
        The `PolymarketProfile`; turned into a mandate once.
    submit
        ``submit(order) -> Any``: the caller's own sign-and-post. Called at most once per
        order, only on allow or an approved hold, with the order unchanged except for
        ``client_order_id`` and, with a commitment, ``metadata``.
    commitment
        Optional `CommitmentParties`; with it every order carries a ``0x4D54`` commitment.
    approver
        Optional ``approver(outcome) -> (decision, authority_proof) | None`` for a hold, e.g.
        built with `ratification_proof`. Without one, or without an answer, a hold hands over
        nothing.
    attest
        Optional ``attest(statement) -> envelope`` (see AER above).
    ledger
        `ExposureLedger`; in memory if omitted.
    receipts_dir
        If set, every decision writes ``<dir>/<n>-<disposition>-<client_order_id>.json``.
    clock
        Returns an aware UTC datetime; injectable for tests.
    nonce
        Returns the commitment nonce as lowercase 0x-hex; random 4 bytes by default.
    """

    def __init__(self, client: Any, profile: PolymarketProfile, submit: Callable[[dict], Any],
                 *, commitment: Optional[CommitmentParties] = None,
                 approver: Optional[Callable[["OrderOutcome"],
                                             Optional[Tuple[str, dict]]]] = None,
                 attest: Optional[Callable[[dict], dict]] = None,
                 ledger: Optional[ExposureLedger] = None,
                 receipts_dir: Optional[str] = None,
                 clock: Callable[[], _dt.datetime] = _utc_now,
                 nonce: Callable[[], str] = _random_nonce,
                 timer: Optional[Callable[[], float]] = None) -> None:
        if commitment is not None:
            problem = commitment.problem()
            if problem is not None:
                raise ValueError(problem)
        self.client = client
        self.profile = profile
        self.mandate = build_mandate(profile)
        self.submit = submit
        self.commitment = commitment
        self.approver = approver
        self.attest = attest
        self.ledger = ledger or ExposureLedger()
        self.receipts_dir = receipts_dir
        self.clock = clock
        self.nonce = nonce
        import time
        self.timer = timer or time.perf_counter
        self._n = 0

    def transaction_for(self, order: Mapping, now: _dt.datetime) -> dict:
        """Order -> transaction. Every value derived in code; underivable values stay absent."""
        tx: Dict[str, Any] = {"action": dict(ACTION), "t_min": int(now.timestamp()) // 60}
        token = token_id(order.get("token_id"))
        if token is not None:
            tx["token_id"] = token
        side = order.get("side")
        if side in SIDES:
            tx["side"] = side
        exchange = exchange_for(order)
        if exchange is not None:
            tx["exchange"] = exchange
        s, p = size_c(order.get("size")), price_bps(order.get("price"))
        if s is not None:
            tx["size_c"] = s
        if p is not None:
            tx["price_bps"] = p
        if s is not None and p is not None and side in SIDES and token is not None:
            cost = order_cost_e6(side, s, p)
            day = now.strftime("%Y-%m-%d")
            tx["order_cost_e6"] = cost
            tx["market_day_exposure_e6"] = self.ledger.exposure(token, day) + cost
            tx["day_exposure_e6"] = self.ledger.day_total(day) + cost
        return tx

    def _commit(self, tx: dict, now: _dt.datetime) -> Optional[Dict[str, str]]:
        if self.commitment is None or not all(k in tx for k in ("token_id", "side",
                                                                 "exchange")):
            return None
        band = (price_bps(self.profile.min_price), price_bps(self.profile.max_price))
        return build_commitment(self.commitment, str(self.profile.principal.get("did", "")),
                                tx["exchange"], tx["token_id"], tx["side"], band,
                                int(now.timestamp()) * 1000, self.nonce())

    def decide(self, order: Mapping) -> OrderOutcome:
        """Verdict and MolCheck record only; hands over nothing."""
        order = dict(order)
        order.setdefault("client_order_id", str(uuid.uuid4()))
        now = self.clock()
        tx = self.transaction_for(order, now)
        commitment = self._commit(tx, now)
        if commitment is not None:
            tx["commitment"] = commitment["metadata"]
        t0 = self.timer()
        verdict = self.client.check(self.mandate, tx)
        check_ms = (self.timer() - t0) * 1000.0
        server = getattr(verdict, "verdict", DENY)
        reason = getattr(verdict, "reason", "")
        if server == DENY:
            if "unaddressed action" in reason:
                reason = "polymarket_order has no grant (not in the mandate)"
            else:
                reason = explain(getattr(verdict, "trace", ()) or ()) or reason
        out = OrderOutcome(order=order, transaction=tx, mandate=self.mandate, verdict=verdict,
                           disposition=_DISPOSITION.get(server, FORBID), reason=reason,
                           commitment=commitment, check_ms=check_ms)
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
        """Decide; hand the order to ``submit`` only on allow or an approved hold."""
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
        try:
            if go:
                if out.commitment is not None:
                    out.order["metadata"] = out.commitment["metadata"]
                # Recorded before the hand-over: an order that may have left counts, which
                # can only make a cap bind earlier.
                self.ledger.record(out.transaction["token_id"],
                                   self.clock().strftime("%Y-%m-%d"),
                                   out.transaction["order_cost_e6"])
                out.sent = True
                out.submit_result = self.submit(dict(out.order))
        finally:
            self._write_receipt(out)
        return out

    def _attach_aer(self, out: OrderOutcome, now: _dt.datetime) -> None:
        ts = _rfc3339(int(now.timestamp()))
        items = []
        if self.attest is not None:
            statement = make_statement(
                str(self.profile.principal.get("did", "")),
                {"kind": "polymarket_decision", "mandate": self.profile.mandate_id,
                 "market": out.transaction.get("token_id", ""),
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
            json.dump(out.receipt(), fh, indent=2, sort_keys=True, default=str)
        os.replace(tmp, path)
        out.receipt_path = path


__all__ = [
    "ALLOW", "HOLD", "FORBID", "VERB", "TYPE_FIELDS", "ACTION", "SIDES", "CHAIN",
    "CTF_EXCHANGE", "NEG_RISK_EXCHANGE", "EXCHANGES", "COMMIT_PREFIX", "PREIMAGE_HEADER",
    "PREIMAGE_FIELDS", "PolymarketProfile", "PolymarketOrderGuard", "OrderOutcome",
    "CommitmentParties", "ExposureLedger", "FillCheck", "PolymarketGuardError",
    "build_mandate", "explain", "fixed_point", "price_bps", "size_c", "usdc_e6",
    "order_cost_e6", "token_id", "exchange_for", "epoch_minute", "keccak256",
    "preimage_text", "parse_preimage", "commitment_metadata", "verify_commitment",
    "build_commitment", "check_fill", "ed25519_attester", "trust_list_for",
    "ratification_proof",
]
