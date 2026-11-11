"""Leak canary: present a delegated token, and check a leak event offline.

Two operations, both through ``check``:

    from moltrust_enforce import canary

    # A relying party that accepts a delegated token checks it at the token
    # endpoint, POST /delegation/verify. The answer is that token's
    # verification result, the same for every token.
    result = canary.check(token, base_url="https://api.moltrust.ch", api_key=KEY)
    if not result.valid:
        ...

    # The issuer of a canary checks a leak event from GET /canary/events
    # offline, with the trust list from GET /canary/trust-list.
    report = canary.check(event, trust_list=trust_list)
    assert report.ok, report.failures

The offline check needs no connection to MolTrust. It verifies the event
signature (Ed25519 over ``moltrust:leak-canary-event:v1`` 0x00 + JCS of the event
without ``signature``), recomputes the AER decision the event carries with
``verify_record``, checks that the signed evidence names this event's canary,
counterparty and attribution, and, for attribution ``counterparty``, checks the
holder's request signature over the presented body. The same check holds for
events from the verifier (``trigger`` ``verifier_presentation``) and from the
token endpoint (``token_presentation``); the two differ in nothing else.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from ._core import PERMIT
from ._jcs import canonicalize
from .evidence import envelope_statement
from .verify import _key_bytes, _verify_ed25519, trust_list_problem, verify_record

__all__ = ["check", "verify_event", "present_token", "event_signing_input",
           "LeakEventReport", "TokenCheck", "EVENT_TAG", "EVENT_FIELDS", "SOURCE_ID",
           "TRIGGER_PATHS", "TOKEN_PATH"]

EVENT_TAG = b"moltrust:leak-canary-event:v1\x00"
EVENT_VERSION = 1
SOURCE_ID = "did:web:api.moltrust.ch#leak-canary"
QUERY_KIND = "leak_canary_presented"
HOLD_VERB = "leak_canary_hold"
ATTRIBUTIONS = ("counterparty", "open")
TOKEN_PATH = "/delegation/verify"
# The path a holder proof has to name, by the event's trigger.
TRIGGER_PATHS = {"verifier_presentation": "/credentials/verify",
                 "token_presentation": TOKEN_PATH}
# Every leak event carries exactly these members, whatever its trigger.
EVENT_FIELDS = frozenset({
    "event_version", "event_id", "canary_id", "issuer_did", "counterparty_did", "mode",
    "trigger", "observed_at", "presenter_did", "credential_valid", "canary_ref",
    "presentation", "attribution", "aer", "signature",
})
DEFAULT_TIMEOUT = 5.0


# ----------------------------------------------------------------------- offline

@dataclass(frozen=True)
class LeakEventReport:
    """Result of the offline check. ``ok`` only when every check holds."""

    ok: bool
    failures: Tuple[str, ...] = ()
    trigger: Optional[str] = None
    attribution: Optional[str] = None
    recomputed_verdict: Optional[str] = None

    def __bool__(self) -> bool:
        return self.ok


def event_signing_input(event: Mapping[str, Any]) -> bytes:
    """The bytes the registry key signs: tag, then JCS of the event without `signature`."""
    return EVENT_TAG + canonicalize({k: v for k, v in event.items() if k != "signature"})


def _source_keys(trust_list: Any) -> Dict[str, bytes]:
    entry = (trust_list.get("sources") or {}).get(SOURCE_ID) or {}
    out = {}
    for key in entry.get("keys") or []:
        raw = _key_bytes(key.get("public_key"))
        if raw is not None and isinstance(key.get("keyid"), str):
            out[key["keyid"]] = raw
    return out


def _signature_holds(event: Mapping[str, Any], keys: Dict[str, bytes]) -> Optional[str]:
    sig = event.get("signature")
    if not isinstance(sig, Mapping) or sig.get("alg") != "Ed25519":
        return "signature is not an Ed25519 signature object"
    key = keys.get(sig.get("kid")) if isinstance(sig.get("kid"), str) else None
    if key is None:
        return "signature kid is not a key of the leak-canary source in the trust list"
    try:
        raw = bytes.fromhex(sig.get("sig"))
        message = event_signing_input(event)
    except (TypeError, ValueError):
        return "signature or event cannot be decoded"
    if not _verify_ed25519(key, raw, message):
        return "event signature does not verify"
    return None


def _holder_proof_holds(holder_proof: Any, path: str) -> bool:
    """The holder's Ed25519 signature over the base, and the base names the DID,
    the path and the SHA-256 of the carried body. Fail-closed."""
    try:
        rec = holder_proof["record"]
        base = bytes.fromhex(rec["signature_base_hex"])
        body = bytes.fromhex(rec["request_body_hex"])
        if not _verify_ed25519(bytes.fromhex(rec["public_key_hex"]),
                               bytes.fromhex(rec["signature_hex"]), base):
            return False
        lines = base.decode("utf-8").split("\n")
        digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
        return (f'"x-moltrust-did": {holder_proof["did"]}' in lines
                and f'"@path": {path}' in lines
                and any(line.startswith('"content-digest": ') and digest in line
                        for line in lines))
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error):
        return False


def verify_event(event: Any, trust_list: Any) -> LeakEventReport:
    """Check a leak event offline against the trust list of GET /canary/trust-list."""
    failures = []
    if not isinstance(event, Mapping):
        return LeakEventReport(False, ("event is not an object",))
    problem = trust_list_problem(trust_list)
    if problem is not None:
        return LeakEventReport(False, (f"trust list: {problem}",))
    trigger = event.get("trigger")
    attribution = event.get("attribution")
    if set(event) != EVENT_FIELDS:
        failures.append("event members differ from a leak event: missing "
                        f"{sorted(EVENT_FIELDS - set(event))}, extra {sorted(set(event) - EVENT_FIELDS)}")
        return LeakEventReport(False, tuple(failures), trigger, attribution)
    if event["event_version"] != EVENT_VERSION:
        failures.append(f"event_version is not {EVENT_VERSION}")
    if trigger not in TRIGGER_PATHS:
        failures.append(f"unknown trigger {trigger!r}")
    if attribution not in ATTRIBUTIONS:
        failures.append(f"unknown attribution {attribution!r}")

    problem = _signature_holds(event, _source_keys(trust_list))
    if problem is not None:
        failures.append(problem)

    verdict = None
    aer = event["aer"]
    if not isinstance(aer, Mapping) or not all(k in aer for k in ("record", "bundle", "mandate",
                                                                  "transaction")):
        failures.append("aer does not carry record, bundle, mandate and transaction")
    else:
        result = verify_record(aer["record"], aer["bundle"], aer["mandate"], aer["transaction"],
                               trust_list)
        verdict = result.recomputed_verdict
        if not result.ok:
            failures.extend(f"aer {f}" for f in result.failures)
        elif verdict != PERMIT:
            failures.append(f"aer recomputes to {verdict}, not {PERMIT}")
        expected_query = {"kind": QUERY_KIND, "canary_ref": event["canary_ref"],
                          "counterparty_did": event["counterparty_did"],
                          "attribution": attribution}
        items = aer["bundle"].get("items") if isinstance(aer["bundle"], Mapping) else None
        statement = envelope_statement(items[0])[0] if isinstance(items, list) and len(items) == 1 else None
        if statement is None:
            failures.append("aer does not carry exactly one readable evidence item")
        else:
            if statement.get("source_id") != SOURCE_ID:
                failures.append("aer evidence is not from the leak-canary source")
            if statement.get("query") != expected_query:
                failures.append("aer evidence names another canary, counterparty or attribution")
            if statement.get("nonce") != event["event_id"] or statement.get("valid_from") != event["observed_at"]:
                failures.append("aer evidence belongs to another event")
        tx = aer["transaction"]
        if not isinstance(tx, Mapping) or tx.get("event_id") != event["event_id"] \
                or tx.get("counterparty_did") != event["counterparty_did"] \
                or tx.get("action") != {"verb": HOLD_VERB}:
            failures.append("aer transaction belongs to another event")

    if attribution == "counterparty" and trigger in TRIGGER_PATHS:
        presentation = event["presentation"] if isinstance(event["presentation"], Mapping) else {}
        hp = presentation.get("holder_proof")
        if not (isinstance(hp, Mapping) and hp.get("valid") is True
                and hp.get("did") == event["counterparty_did"]
                and _holder_proof_holds(hp, TRIGGER_PATHS[trigger])):
            failures.append("attribution counterparty without a holder proof by the counterparty")

    return LeakEventReport(not failures, tuple(failures), trigger, attribution, verdict)


# ------------------------------------------------------------------------ online

@dataclass(frozen=True)
class TokenCheck:
    """The token endpoint's answer. ``from_server=False``: no answer was read
    (transport failure, error status, unreadable body), and ``valid`` is False."""

    valid: bool
    checks: Dict[str, Any]
    errors: Tuple[str, ...]
    presentation: Optional[Dict[str, Any]] = None
    from_server: bool = True

    def __bool__(self) -> bool:
        return self.valid


def _fail(reason: str) -> TokenCheck:
    return TokenCheck(valid=False, checks={}, errors=(reason,), from_server=False)


def present_token(token: str, *, base_url: str, api_key: str,
                  expected_audience: Optional[str] = None,
                  signed_request: Optional[Tuple[bytes, Mapping[str, str]]] = None,
                  timeout: float = DEFAULT_TIMEOUT, transport: Any = None) -> TokenCheck:
    """POST the token to the token endpoint and return its answer. Fail-closed.

    `signed_request` forwards a request the holder signed (RFC 9421, the profile
    of POST /credentials/verify): the exact body bytes and the holder's headers.
    The body has to carry this token; it is sent unchanged.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - extra `client` declares it
        raise ImportError("canary.check(token) needs httpx: pip install "
                          "'moltrust-enforce[client]'") from exc
    headers = {"Content-Type": "application/json", "X-API-Key": api_key}
    if signed_request is not None:
        body, extra = signed_request
        import json
        try:
            carried = json.loads(body)
        except ValueError:
            raise ValueError("signed_request body is not JSON") from None
        if not isinstance(carried, dict) or carried.get("token") != token:
            raise ValueError("signed_request body does not carry this token")
        headers.update(extra)
        headers["X-API-Key"] = api_key
        content = body
    else:
        payload: Dict[str, Any] = {"token": token}
        if expected_audience is not None:
            payload["expected_audience"] = expected_audience
        content = canonicalize(payload)
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                          transport=transport) as client:
            response = client.post(TOKEN_PATH, content=content, headers=headers)
    except httpx.HTTPError as exc:
        return _fail(f"transport failure: {type(exc).__name__}")
    if response.status_code != 200:
        return _fail(f"server returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        return _fail("response is not JSON")
    if not isinstance(data, dict) or not isinstance(data.get("valid"), bool):
        return _fail("response carries no boolean valid")
    errors = data.get("errors") if isinstance(data.get("errors"), list) else []
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    presentation = data.get("presentation") if isinstance(data.get("presentation"), dict) else None
    return TokenCheck(valid=data["valid"], checks=checks,
                      errors=tuple(str(e) for e in errors), presentation=presentation)


# ------------------------------------------------------------------------ entry

def check(subject: Any, **kwargs: Any):
    """`check(token, base_url=..., api_key=...)` presents a token (`present_token`);
    `check(event, trust_list=...)` checks a leak event offline (`verify_event`)."""
    if isinstance(subject, str):
        return present_token(subject, **kwargs)
    if isinstance(subject, Mapping):
        if set(kwargs) != {"trust_list"}:
            raise TypeError("check(event, trust_list=...) takes the trust list and nothing else")
        return verify_event(subject, kwargs["trust_list"])
    raise TypeError("check() takes a token (str) or a leak event (mapping)")
