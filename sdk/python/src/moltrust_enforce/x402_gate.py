"""Receiver-side gate for x402 payments.

A merchant or an x402 facilitator registers one hook before verify and settle. The hook
aborts every payment that does not carry a MolTrust verdict bound to exactly this payment.
It makes no network call: the verdict is recomputed locally from the mandate the agent
sends and the transaction derived from the payment itself.

    from moltrust_enforce.x402_gate import require_moltrust_verdict, trust_mandates

    hook = require_moltrust_verdict(accept_mandate=trust_mandates(MANDATE_DIGEST))
    server.on_before_verify(hook)      # x402ResourceServer, or an x402Facilitator
    server.on_before_settle(hook)

What the agent sends
--------------------
The receipt travels inside the x402 payment payload, under ``extensions["moltrust"]``,
because that is the part of a payment both hooks see::

    {
      "version": "moltrust-x402/v1",
      "mandate": {...},                      # the mandate the verdict was made under
      "record":  {"core": {...}, "core_digest": "sha256:..."},   # from /enforce/check
      "ancestors": [...]                     # optional, parent mandates, root first
    }

The receipt carries no transaction. The transaction is always derived from the payment
(:func:`x402_transaction`), so an agent cannot describe one payment and pay another.

Derivation rule
---------------
Only x402 version 2, scheme ``exact``, an ``eip155:*`` network and an EIP-3009
authorization are accepted. From ``payload.authorization`` (what the payer signed and what
settles) and ``accepted`` (the requirements the payer chose)::

    {
      "action": {"verb": "x402.pay", "scheme": accepted.scheme,
                 "network": accepted.network, "asset": lower(accepted.asset)},
      "from":   lower(authorization.from),
      "to":     lower(authorization.to),
      "amount": int(authorization.value),
      "nonce":  lower(authorization.nonce)
    }

Before deriving, ``authorization.to`` must equal ``accepted.payTo`` and
``authorization.value`` must equal ``accepted.amount``; when the hook context carries the
server's requirements, scheme, network, asset, amount and payTo must equal those too.
Addresses and the nonce are lowercased, so mandate constraints on ``to`` or ``from`` are
written in lowercase.

What passes
-----------
A payment passes only when all of these hold, and every other path aborts, including a
receipt that cannot be parsed:

1. the receipt is present and well-formed;
2. ``accept_mandate`` accepts the mandate (the merchant decides which mandates count;
   without that, an agent could bring a mandate that allows everything);
3. ``core_digest`` is the digest of the ``core`` shipped with it;
4. recomputing the verdict from the mandate and the derived transaction gives PERMIT and
   exactly that ``core_digest``.

PENDING (a grant with ``disposition: "hold"``) aborts. DENY (``forbid``, or nothing
matches) aborts.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from ._core import (
    PERMIT,
    _TAG_MANDATE,
    _ct_eq,
    _digest,
    core_digest,
    enforce_check,
)

__all__ = [
    "EXTENSION_KEY",
    "RECEIPT_VERSION",
    "X402_VERB",
    "Abort",
    "PaymentDecision",
    "ReceiptError",
    "build_x402_headers",
    "build_receipt",
    "check_payment",
    "mandate_digest",
    "require_moltrust_verdict",
    "trust_mandates",
    "x402_transaction",
]

EXTENSION_KEY = "moltrust"
RECEIPT_VERSION = "moltrust-x402/v1"
X402_VERB = "x402.pay"
PAYMENT_HEADER = "PAYMENT-SIGNATURE"

_DECIMAL = re.compile(r"^(0|[1-9][0-9]{0,15})$")
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_NONCE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
# Same ceiling as the JCS canonicalization: an integer beyond 2**53-1 has no exact form.
_MAX_SAFE_INT = 2 ** 53 - 1


class ReceiptError(Exception):
    """The payment or its receipt cannot be checked; the message says why."""


@dataclass(frozen=True)
class PaymentDecision:
    """Result of :func:`check_payment`. ``allowed`` is never True by omission."""

    allowed: bool
    reason: str
    detail: str = ""
    verdict: Optional[str] = None
    core_digest: Optional[str] = None
    mandate_digest: Optional[str] = None
    transaction: Optional[Mapping[str, Any]] = None

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(frozen=True)
class Abort:
    """Stand-in for ``x402.schemas.AbortResult`` when the x402 package is not installed."""

    reason: str


# --------------------------------------------------------------------------- input shape

def _as_wire(obj: Any, what: str) -> dict:
    """A payment payload or requirements object as its camelCase wire dict.

    Accepts a plain dict (as decoded from the header) or an x402 pydantic model.
    """
    if isinstance(obj, Mapping):
        return dict(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            out = dump(by_alias=True, exclude_none=True)
        except Exception as exc:  # noqa: BLE001 - reported as an abort reason
            raise ReceiptError(f"{what} cannot be read: {type(exc).__name__}") from exc
        if isinstance(out, dict):
            return out
    raise ReceiptError(f"{what} is not an object")


def _str_field(obj: Mapping[str, Any], key: str, what: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise ReceiptError(f"{what}.{key} missing or not a string")
    return value


def _amount(value: Any, what: str) -> int:
    if not isinstance(value, str) or not _DECIMAL.match(value):
        raise ReceiptError(f"{what} is not a decimal integer string")
    n = int(value)
    if n > _MAX_SAFE_INT:
        raise ReceiptError(f"{what} exceeds 2**53-1")
    return n


def _address(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _ADDRESS.match(value):
        raise ReceiptError(f"{what} is not a 20-byte hex address")
    return value.lower()


# --------------------------------------------------------------------------- derivation

def x402_transaction(payment_payload: Any, requirements: Any = None) -> dict:
    """Derive the transaction to recompute from an x402 payment payload.

    The rule is in the module docstring and in the README. Raises :class:`ReceiptError`
    for anything outside it. ``requirements`` are the server's own requirements; when
    given, the payload's ``accepted`` must match them.
    """
    p = _as_wire(payment_payload, "payment payload")
    if p.get("x402Version") != 2:
        raise ReceiptError("only x402 version 2 payloads are supported")
    accepted = p.get("accepted")
    if not isinstance(accepted, Mapping):
        raise ReceiptError("payment payload has no accepted requirements")
    scheme = _str_field(accepted, "scheme", "accepted")
    network = _str_field(accepted, "network", "accepted")
    asset = _str_field(accepted, "asset", "accepted")
    pay_to = _address(accepted.get("payTo"), "accepted.payTo")
    accepted_amount = _amount(accepted.get("amount"), "accepted.amount")
    if scheme != "exact":
        raise ReceiptError(f"scheme {scheme!r} is not supported, only 'exact'")
    if not network.startswith("eip155:"):
        raise ReceiptError(f"network {network!r} is not supported, only eip155:*")
    asset = _address(asset, "accepted.asset")

    inner = p.get("payload")
    if not isinstance(inner, Mapping):
        raise ReceiptError("payment payload has no scheme payload")
    auth = inner.get("authorization")
    if not isinstance(auth, Mapping):
        raise ReceiptError("payload has no EIP-3009 authorization")
    payer = _address(auth.get("from"), "authorization.from")
    to = _address(auth.get("to"), "authorization.to")
    value = _amount(auth.get("value"), "authorization.value")
    nonce = auth.get("nonce")
    if not isinstance(nonce, str) or not _NONCE.match(nonce):
        raise ReceiptError("authorization.nonce is not a 32-byte hex value")

    if to != pay_to:
        raise ReceiptError("authorization.to differs from accepted.payTo")
    if value != accepted_amount:
        raise ReceiptError("authorization.value differs from accepted.amount")

    if requirements is not None:
        r = _as_wire(requirements, "requirements")
        try:
            same = (r.get("scheme") == scheme and r.get("network") == network
                    and _address(r.get("asset"), "requirements.asset") == asset
                    and _amount(r.get("amount"), "requirements.amount") == accepted_amount
                    and _address(r.get("payTo"), "requirements.payTo") == pay_to)
        except ReceiptError:
            same = False
        if not same:
            raise ReceiptError("accepted requirements differ from the server's requirements")

    return {
        "action": {"verb": X402_VERB, "scheme": scheme, "network": network, "asset": asset},
        "from": payer,
        "to": to,
        "amount": value,
        "nonce": nonce.lower(),
    }


def mandate_digest(mandate: Any) -> Optional[str]:
    """The digest a merchant pins to accept a mandate (same as ``core.mandate_digest``)."""
    return _digest(_TAG_MANDATE, mandate)


def trust_mandates(*digests: str) -> Callable[[Mapping[str, Any], str, str], bool]:
    """An ``accept_mandate`` callable that accepts exactly the given mandate digests."""
    pinned = tuple(d for d in digests if isinstance(d, str) and _DIGEST.match(d))
    if not pinned:
        raise ValueError("trust_mandates needs at least one sha256:<64 hex> digest")

    def accept(mandate: Mapping[str, Any], digest: str, payer: str) -> bool:
        return any(_ct_eq(digest, d) for d in pinned)

    return accept


# --------------------------------------------------------------------------- check

def _deny(reason: str, detail: str = "", **kw: Any) -> PaymentDecision:
    return PaymentDecision(False, reason, detail, **kw)


def check_payment(payment_payload: Any, requirements: Any = None, *,
                  accept_mandate: Callable[[Mapping[str, Any], str, str], bool]
                  ) -> PaymentDecision:
    """Check one x402 payment against the MolTrust receipt it carries. Offline.

    ``accept_mandate(mandate, mandate_digest, payer)`` returns True for a mandate the
    merchant accepts for this payer. Any exception anywhere is an abort.
    """
    try:
        return _check(payment_payload, requirements, accept_mandate)
    except ReceiptError as exc:
        return _deny("payment_unreadable", str(exc))
    except Exception as exc:  # noqa: BLE001 - fail closed on anything unexpected
        return _deny("check_failed", type(exc).__name__)


def _check(payment_payload: Any, requirements: Any,
           accept_mandate: Callable[[Mapping[str, Any], str, str], bool]) -> PaymentDecision:
    p = _as_wire(payment_payload, "payment payload")
    tx = x402_transaction(p, requirements)

    ext = p.get("extensions")
    receipt = ext.get(EXTENSION_KEY) if isinstance(ext, Mapping) else None
    if receipt is None:
        return _deny("receipt_missing",
                     f"no extensions.{EXTENSION_KEY} in the payment payload", transaction=tx)
    if not isinstance(receipt, Mapping):
        return _deny("receipt_invalid", "receipt is not an object", transaction=tx)
    if receipt.get("version") != RECEIPT_VERSION:
        return _deny("receipt_invalid", f"receipt version is not {RECEIPT_VERSION}",
                     transaction=tx)
    mandate = receipt.get("mandate")
    record = receipt.get("record")
    ancestors = receipt.get("ancestors")
    if not isinstance(mandate, Mapping):
        return _deny("receipt_invalid", "receipt has no mandate", transaction=tx)
    if not isinstance(record, Mapping):
        return _deny("receipt_invalid", "receipt has no record", transaction=tx)
    if ancestors is not None and not isinstance(ancestors, list):
        return _deny("receipt_invalid", "receipt.ancestors is not a list", transaction=tx)
    core = record.get("core")
    claimed = record.get("core_digest")
    if not isinstance(core, Mapping) or not isinstance(claimed, str) \
            or not _DIGEST.match(claimed):
        return _deny("receipt_invalid", "record needs core and a sha256 core_digest",
                     transaction=tx)

    m_digest = mandate_digest(mandate)
    if m_digest is None:
        return _deny("receipt_invalid", "mandate not canonicalizable", transaction=tx)
    if accept_mandate(mandate, m_digest, tx["from"]) is not True:
        return _deny("mandate_not_accepted",
                     "this receiver does not accept the mandate for this payer",
                     mandate_digest=m_digest, transaction=tx)

    shipped = core_digest(dict(core))
    if shipped is None or not _ct_eq(shipped, claimed):
        return _deny("receipt_tampered", "core_digest is not the digest of core",
                     mandate_digest=m_digest, transaction=tx)

    prev = core.get("prev_core_digest")
    fresh = enforce_check(dict(mandate), tx, prev_core_digest=prev, ancestors=ancestors)
    if fresh["core_digest"] is None or not _ct_eq(fresh["core_digest"], claimed):
        return _deny("receipt_not_bound",
                     "the receipt was made for a different payment or mandate",
                     verdict=fresh["verdict"], mandate_digest=m_digest, transaction=tx)
    if fresh["verdict"] != PERMIT:
        return _deny("verdict_not_permit", f"verdict is {fresh['verdict']}: {fresh['reason']}",
                     verdict=fresh["verdict"], core_digest=claimed,
                     mandate_digest=m_digest, transaction=tx)
    return PaymentDecision(True, "ok", "", verdict=PERMIT, core_digest=claimed,
                           mandate_digest=m_digest, transaction=tx)


# --------------------------------------------------------------------------- hook

def _abort_type() -> Callable[[str], Any]:
    try:
        from x402.schemas import AbortResult  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - x402 not installed: standalone use
        return Abort
    return AbortResult


def require_moltrust_verdict(*, accept_mandate: Callable[[Mapping[str, Any], str, str], bool],
                             on_decision: Optional[Callable[[PaymentDecision], None]] = None,
                             ) -> Callable[[Any], Any]:
    """Build a before-verify / before-settle hook for the x402 V2 Python SDK.

    The hook takes the hook context (``payment_payload`` and ``requirements``) and returns
    ``None`` to let the payment continue or an abort result with the reason. With the x402
    package installed that is ``x402.schemas.AbortResult``; without it, :class:`Abort`.

    ``on_decision`` is called with every :class:`PaymentDecision` for logging; its return
    value and its exceptions are ignored.
    """
    if not callable(accept_mandate):
        raise TypeError("accept_mandate is required: the receiver decides which mandates count")
    abort = _abort_type()

    def hook(context: Any) -> Any:
        try:
            payload = getattr(context, "payment_payload", None)
            reqs = getattr(context, "requirements", None)
            if payload is None and isinstance(context, Mapping):
                payload = context.get("payment_payload")
                reqs = context.get("requirements")
            decision = check_payment(payload, reqs, accept_mandate=accept_mandate)
        except Exception as exc:  # noqa: BLE001 - fail closed
            decision = _deny("check_failed", type(exc).__name__)
        if on_decision is not None:
            try:
                on_decision(decision)
            except Exception:  # noqa: BLE001 - logging never decides
                pass
        if decision.allowed:
            return None
        return abort(reason=f"moltrust:{decision.reason}: {decision.detail}".rstrip(": "))

    return hook


# --------------------------------------------------------------------------- agent side

def _record_from_response(check_response: Any) -> dict:
    """``{"core", "core_digest"}`` from a /enforce/check response or a ``Verdict``."""
    if isinstance(check_response, Mapping):
        rec = check_response.get("record")
        if isinstance(rec, Mapping):
            core, digest = rec.get("core"), rec.get("core_digest")
        else:
            core, digest = check_response.get("core"), check_response.get("core_digest")
    else:
        core = getattr(check_response, "core", None)
        digest = getattr(check_response, "core_digest", None)
    if not isinstance(core, Mapping) or not isinstance(digest, str):
        raise ReceiptError("check response carries no record (core, core_digest)")
    return {"core": dict(core), "core_digest": digest}


def build_receipt(mandate: Mapping[str, Any], payment_payload: Any, check_response: Any, *,
                  ancestors: Optional[Sequence[Mapping[str, Any]]] = None) -> dict:
    """The ``extensions["moltrust"]`` object for one payment.

    Refuses (``ReceiptError``) unless the check response is a PERMIT bound to exactly this
    payment, recomputed locally. Sending a receipt the receiver will reject is a wasted
    payment attempt.
    """
    record = _record_from_response(check_response)
    receipt: dict = {"version": RECEIPT_VERSION, "mandate": dict(mandate), "record": record}
    if ancestors:
        receipt["ancestors"] = [dict(a) for a in ancestors]
    p = _as_wire(payment_payload, "payment payload")
    trial = dict(p)
    trial["extensions"] = {**(p.get("extensions") or {}), EXTENSION_KEY: receipt}
    decision = check_payment(trial, accept_mandate=lambda *_: True)
    if not decision.allowed:
        raise ReceiptError(f"receipt would be rejected: {decision.reason}: {decision.detail}")
    return receipt


def build_x402_headers(mandate: Mapping[str, Any], payment_payload: Any, check_response: Any,
                       *, ancestors: Optional[Sequence[Mapping[str, Any]]] = None
                       ) -> dict:
    """Headers for the x402 request, with the receipt inside the payment payload.

    ``payment_payload`` is the signed x402 V2 payload as the x402 client built it (dict or
    model). Returns ``{"PAYMENT-SIGNATURE": base64(JSON payload)}``, the V2 transport
    header, with ``extensions["moltrust"]`` added.
    """
    receipt = build_receipt(mandate, payment_payload, check_response, ancestors=ancestors)
    p = _as_wire(payment_payload, "payment payload")
    p["extensions"] = {**(p.get("extensions") or {}), EXTENSION_KEY: receipt}
    body = json.dumps(p, separators=(",", ":"), ensure_ascii=False)
    return {PAYMENT_HEADER: base64.b64encode(body.encode("utf-8")).decode("ascii")}


def decode_payment_header(value: str) -> dict:
    """Inverse of the header encoding, for receivers that do not use the x402 package."""
    try:
        data = json.loads(base64.b64decode(value, validate=True).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ReceiptError(f"payment header is not base64 JSON: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ReceiptError("payment header does not hold an object")
    return data


__all__.append("decode_payment_header")
__all__.append("PAYMENT_HEADER")
