"""Conformance reader for the Agent Audit Record format.

Written from the text of draft-gilda-audit-agent-audit-record-03 (the draft),
Sections 4 and 5, and nothing else of the format. Every refusal below names
the rule it comes from as "-03 L<first>-<last>", the line range of the draft
text as published (draft-03.txt, 3976 lines).

The reader takes the bytes of one corpus member and a VerifierConfig. The
config holds only what a verifier holds before it sees any record: the
observer's public key, the payload type, the predicate type URIs, the skew
tolerance, and the anchor trust material (tree heads and block headers the
verifier holds independently, -03 L851-857 and L859-863). It never sees what
any member is expected to yield.

Three shapes of member are judged:

  envelope   a DSSE envelope; judged under Section 4.6 (judge_envelope)
  retained   {"envelope", "retention"}; the envelope is judged as above and
             the retention entry gets the dating check of Section 4.7.1,
             reported beside the verdict and never folded into it
             (-03 L597-606, L773)
  set        {"records": [...]}; each record judged on its own, then the
             checks across records of Section 5.9 (consumption) and 5.10
             (sequence) run over the set (-03 L1827-1844, L1915-1929)

Only Python's standard library, `cryptography` (Ed25519) and `jcs`
(RFC 8785) are used.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import jcs
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VALID = "valid"
MALFORMED = "malformed"
INDETERMINATE = "indeterminate"

# -03 L285-290: skew tolerance is 300 seconds; a relying party MUST NOT apply
# a larger one.
SKEW_TOLERANCE_MAX = 300
# -03 L869-870: comparisons in 4.7.1 that use an opentimestamps time allow
# 7200 seconds in place of the skew tolerance.
OTS_TOLERANCE = 7200

PREDICATE_V1 = "https://probityai.github.io/agent-evidence-vectors/predicate/v1/agent-audit-record"
# -03 L2194-2202: the v2 URI is the v1 URI with v1 read as v2; it admits
# principal-standing-source and is otherwise the same.
PREDICATE_V2 = "https://probityai.github.io/agent-evidence-vectors/predicate/v2/agent-audit-record"
INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
INTOTO_PAYLOAD_TYPE = "application/vnd.in-toto+json"  # -03 L436-438

# -03 L1078-1081 and L1047-1051: one hashAlgorithm governs every digest; a
# verifier that does not implement it has no verdict to reach. This reader
# implements sha256 only.
HASH_ALGORITHMS = {"sha256": (hashlib.sha256, 64)}
# -03 L1197-1200: the empty-tree constant for sha256.
EMPTY_TREE = {"sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}

# Section 5 (-03 L309-312, L917-1039): the seventeen required members.
REQUIRED_MEMBERS = (
    "recordId", "tier", "hashAlgorithm", "agent", "delegation", "resource",
    "decision", "evaluation", "effect", "agreement", "correlation", "posture",
    "remediation", "observation", "fieldEvidence", "doesNotAssert", "issuedAt",
)

# Closed vocabularies (-03 L320-321: a value outside one is refused).
AUTHENTICATION = {"wimse-wpt", "http-message-signature", "mtls",
                  "oauth-access-token", "api-key", "none"}            # L1087-1089
SUBJECT_KIND = {"user", "system", "none"}                          # L1102
RESOURCE_KIND = {"path", "uri", "tool"}                            # L1137
BINDING = {"digest-bound", "not-bindable"}                         # L1139-1141
REPORTED = {"permit", "deny", "permit-with-conditions"}            # L1150
PERMITS = {"permit", "permit-with-conditions"}
OBSERVED = {"occurred", "none", "unknown"}                         # L1170-1171
AGREEMENT = {"agree", "disagree", "not-exercised", "indeterminate",
             "one-sided"}                                          # L1207-1208
STATUS = {"evaluated", "not-evaluated"}                            # L1279-1280
INPUTS_V1 = {"standing-source", "key-source", "consumption-state",
             "policy-source"}                                      # L1418-1450
INPUTS_V2 = INPUTS_V1 | {"principal-standing-source"}              # L1439-1447
OVERSIGHT_ACT = {"observation", "check", "decision", "release"}    # L1527-1528
CORRELATION_SCOPE = {"producer", "cross-party"}                    # L1539
ANCHOR_KIND = {"rfc3161", "transparency-log", "opentimestamps"}    # L1546-1547
POSTURE = {"no_network", "allowlist", "sinkhole",
           "unsafe_bypass_egress"}                                 # L1560-1564
REMEDIATION_CAUSE = {"session-revoked", "risk-elevated", "subject-disabled",
                     "token-replay-suspected", "policy-changed",
                     "operator-action"}                            # L1578-1580
ENFORCEMENT = {"access-attenuated", "session-terminated", "tokens-discarded",
               "privileges-reduced", "reevaluated", "not-enforced"}  # L1580-1582
VANTAGE = {"below-observed", "peer", "self"}                       # L1598
FIELD_EVIDENCE_KEYS = {"agent", "correlation", "decision", "delegation",
                       "posture", "remediation", "resource"}       # L1643, Table 1
FIELD_EVIDENCE = {"substrate-covered", "producer-asserted"}        # L1643-1649
TIER = {"voluntary", "authoritative"}                              # L1666
ORIGIN = {"retainer", "received"}                                  # L688

ENTRY_MEMBERS = {"input", "lastResult", "maxAge"}                  # L1317-1332
CONSUMPTION_MEMBERS = {"limitDigest", "unit", "limit", "consumed", "reserved",
                       "requested", "held", "stateRevision"}       # L1765-1788
HELD_MEMBERS = {"operationKey", "requestDigest", "amount"}         # L1781-1784
PRIOR_COMMITMENT_MEMBERS = ("committedAt", "witnessNonce", "commitmentDigest",
                            "keyid", "sig")                        # L1020-1026, L1715
GLOB_METACHARACTERS = set("*?[]{}")                                # L1202-1203

MAX_DEPTH = 128          # -03 L371-372
SAFE_INTEGER = 2 ** 53   # -03 L364-365


class Refusal(Exception):
    """A rule of the draft refuses the record."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


class Unreadable(Exception):
    """The record cannot be read by this verifier (-03 L1078-1081)."""


@dataclass
class VerifierConfig:
    observer_public_key: bytes
    payload_type: str = INTOTO_PAYLOAD_TYPE
    predicate_types: Tuple[str, ...] = (PREDICATE_V1, PREDICATE_V2)
    skew: int = SKEW_TOLERANCE_MAX
    tree_heads: List[dict] = field(default_factory=list)
    block_headers: Dict[int, bytes] = field(default_factory=dict)

    @classmethod
    def from_manifest_trust(cls, manifest: dict) -> "VerifierConfig":
        """Take the verifier-held trust material and parameters from the
        top-level members of a corpus manifest. Member entries (vectors) are
        never read here."""
        alg = manifest.get("algorithm", {})
        if alg.get("envelope", "ed25519") != "ed25519":
            raise ValueError("only Ed25519 envelope signatures are implemented")
        key = bytes.fromhex(manifest["keys"]["observer"]["publicKey"])
        skew = min(int(manifest.get("skewTolerance", SKEW_TOLERANCE_MAX)), SKEW_TOLERANCE_MAX)
        ptypes = [PREDICATE_V1, PREDICATE_V2]
        mp = manifest.get("predicateType")
        if mp and mp not in ptypes:
            ptypes.append(mp)
        trust = manifest.get("anchorTrust", {})
        headers = {}
        for h in trust.get("blockHeaders", []):
            headers[int(h["height"])] = bytes.fromhex(h["header"])
        return cls(
            observer_public_key=key,
            payload_type=manifest.get("payloadType", INTOTO_PAYLOAD_TYPE),
            predicate_types=tuple(ptypes),
            skew=skew,
            tree_heads=list(trust.get("treeHeads", [])),
            block_headers=headers,
        )


# ---------------------------------------------------------------------------
# JSON profile: Section 4.1 (-03 L359-372), applied while parsing (L547-555)
# ---------------------------------------------------------------------------

def _pairs_no_duplicates(pairs):
    # -03 L369-371 (T3): a member name repeated at any depth is malformed; no
    # occurrence is retained.
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise Refusal("json-profile", f"duplicate member name {k!r} (-03 L369-371)")
        obj[k] = v
    return obj


def _parse_int(s: str) -> int:
    v = int(s)
    # -03 L363-365 (T5): magnitude at or above 2^53 is malformed.
    if abs(v) >= SAFE_INTEGER:
        raise Refusal("json-profile", "integer outside the I-JSON safe range (-03 L363-365)")
    return v


def _parse_constant(s: str):
    raise Refusal("json-profile", f"{s} is not JSON (-03 L363-365, RFC 7493)")


def _depth(v: Any) -> int:
    """Nesting depth: every object or array is one level, scalars none."""
    best = 0
    stack = [(v, 1)]
    while stack:
        node, d = stack.pop()
        if isinstance(node, dict):
            best = max(best, d)
            stack.extend((c, d + 1) for c in node.values())
        elif isinstance(node, list):
            best = max(best, d)
            stack.extend((c, d + 1) for c in node)
    return best


def parse_profile(data: bytes) -> Any:
    """Parse bytes under the JSON profile of Section 4.1."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal("json-profile", "not UTF-8 (-03 L361-365, RFC 8785/7493)")
    try:
        value = json.loads(text, object_pairs_hook=_pairs_no_duplicates,
                           parse_int=_parse_int, parse_constant=_parse_constant)
    except Refusal:
        raise
    except RecursionError:
        raise Refusal("json-profile", "nesting deeper than 128 levels (-03 L371-372)")
    except ValueError as exc:
        raise Refusal("json-profile", f"not JSON: {exc}")
    # -03 L371-372 (T4): deeper than 128 levels is rejected.
    if _depth(value) > MAX_DEPTH:
        raise Refusal("json-profile", "nesting deeper than 128 levels (-03 L371-372)")
    return value


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


def parse_time(value: Any, where: str) -> float:
    """-03 L420 (Section 4.3): every time value MUST be an RFC 3339 date-time."""
    if not isinstance(value, str):
        raise Refusal("membership", f"{where} is not an RFC 3339 date-time (-03 L420)")
    m = _RFC3339.match(value)
    if not m:
        raise Refusal("membership", f"{where} is not an RFC 3339 date-time (-03 L420)")
    y, mo, d, hh, mm, ss, frac, off = m.groups()
    try:
        base = datetime(int(y), int(mo), int(d), int(hh), int(mm),
                        min(int(ss), 59), tzinfo=timezone.utc)
    except ValueError:
        raise Refusal("membership", f"{where} is not an RFC 3339 date-time (-03 L420)")
    if int(hh) > 23 or int(mm) > 59 or int(ss) > 60:
        raise Refusal("membership", f"{where} is not an RFC 3339 date-time (-03 L420)")
    t = base.timestamp() + (int(ss) - min(int(ss), 59))
    if frac:
        t += float(frac)
    if off not in ("Z", "z"):
        sign = 1 if off[0] == "+" else -1
        oh, om = int(off[1:3]), int(off[4:6])
        if oh > 23 or om > 59:
            raise Refusal("membership", f"{where} is not an RFC 3339 date-time (-03 L420)")
        t -= sign * (oh * 3600 + om * 60)
    return t


def is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def require_obj(parent: dict, name: str, where: str) -> dict:
    v = parent.get(name)
    if not isinstance(v, dict):
        raise Refusal("membership", f"{where}.{name} absent or not an object (-03 L309-312)")
    return v


def require_member(parent: dict, name: str, where: str) -> Any:
    # -03 L309-312 (F1, EV3): no member has a default; an absent one is not
    # supplied by the verifier.
    if name not in parent:
        raise Refusal("membership", f"{where}.{name} absent (-03 L309-312)")
    return parent[name]


def require_closed(value: Any, vocab: set, where: str, line: str) -> str:
    # -03 L320-321, L346-348 (V1): a value outside a closed vocabulary is
    # refused, and the member is never ignored.
    if not isinstance(value, str) or value not in vocab:
        raise Refusal("membership", f"{where} {value!r} outside the closed set (-03 {line}, L320-321)")
    return value


def require_digest(value: Any, alg: str, where: str) -> str:
    # -03 L296-297, L1047-1051: every digest is lowercase hex under the one
    # declared hashAlgorithm.
    _, hexlen = HASH_ALGORITHMS[alg]
    if not isinstance(value, str) or len(value) != hexlen or not re.fullmatch(r"[0-9a-f]+", value):
        raise Refusal("membership", f"{where} is not a lowercase hex {alg} digest (-03 L296-297, L1047-1051)")
    return value


def jcs_digest(obj: Any, alg: str) -> str:
    fn, _ = HASH_ALGORITHMS[alg]
    return fn(jcs.canonicalize(obj)).hexdigest()


def b64decode_strict(s: Any) -> bytes:
    if not isinstance(s, str):
        raise ValueError("not a string")
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def path_under(path: str, scope_member: str) -> bool:
    """A path lies under a pathScope member (-03 L1202-1205, L1603-1604)."""
    if scope_member == "/":
        return path.startswith("/")
    if path == scope_member:
        return True
    prefix = scope_member if scope_member.endswith("/") else scope_member + "/"
    return path.startswith(prefix)


def in_scope(path: str, path_scope: List[str]) -> bool:
    return any(path_under(path, m) for m in path_scope)


# ---------------------------------------------------------------------------
# Envelope: Section 4.6, checks 1 and 2
# ---------------------------------------------------------------------------

def pae(payload_type: str, body: bytes) -> bytes:
    # -03 L436-438: DSSE PAE over the JCS bytes of the Statement.
    pt = payload_type.encode("utf-8")
    return b"DSSEv1 %d %s %d %s" % (len(pt), pt, len(body), body)


@dataclass
class EnvelopeResult:
    verdict: str
    stage: str = ""
    reason: str = ""
    statement: Optional[dict] = None
    payload_bytes: Optional[bytes] = None
    derived: Dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        if self.verdict == VALID:
            return "all checks passed"
        return f"{self.stage}: {self.reason}"


def judge_envelope(envelope: Any, cfg: VerifierConfig) -> EnvelopeResult:
    """Reach a verdict on one DSSE envelope (Section 4.6, -03 L540-630)."""
    payload_bytes = None
    statement = None
    try:
        if not isinstance(envelope, dict):
            raise Refusal("envelope", "not a DSSE envelope object")
        ptype = envelope.get("payloadType")
        if ptype != cfg.payload_type or ptype != INTOTO_PAYLOAD_TYPE:
            raise Refusal("envelope", f"payloadType {ptype!r} is not {INTOTO_PAYLOAD_TYPE} (-03 L301-302, L436-438)")
        try:
            payload_bytes = b64decode_strict(envelope.get("payload"))
        except (ValueError, binascii.Error):
            raise Refusal("envelope", "payload is not base64")
        # Check 1, the JSON profile while parsing (-03 L547-555).
        statement = parse_profile(payload_bytes)
        if not isinstance(statement, dict):
            raise Refusal("json-profile", "the Statement is not a JSON object (-03 L301)")
        # Check 2, the signature over JCS(statement), never over carried
        # bytes (-03 L361-363, L565-570; T2).
        verify_signature(envelope, statement, cfg)
        derived: Dict[str, Any] = {}
        check_statement(statement, cfg, derived)
        return EnvelopeResult(VALID, statement=statement, payload_bytes=payload_bytes, derived=derived)
    except Refusal as r:
        return EnvelopeResult(MALFORMED, r.stage, r.reason, statement, payload_bytes)
    except Unreadable as u:
        return EnvelopeResult(INDETERMINATE, "hash-algorithm", str(u), statement, payload_bytes)


def verify_signature(envelope: dict, statement: dict, cfg: VerifierConfig) -> None:
    sigs = envelope.get("signatures")
    if not isinstance(sigs, list) or not sigs:
        raise Refusal("signature", "no signature (-03 L565-570)")
    try:
        body = jcs.canonicalize(statement)
    except Exception as exc:  # noqa: BLE001 - any failure to canonicalize refuses
        raise Refusal("signature", f"no RFC 8785 form: {exc} (-03 L361-363)")
    message = pae(INTOTO_PAYLOAD_TYPE, body)
    key = Ed25519PublicKey.from_public_bytes(cfg.observer_public_key)
    for s in sigs:
        if not isinstance(s, dict):
            continue
        try:
            raw = b64decode_strict(s.get("sig"))
            key.verify(raw, message)
            return
        except (InvalidSignature, ValueError, binascii.Error):
            continue
    raise Refusal("signature", "no signature verifies over PAE(JCS(statement)) under the observer key (-03 L565-570, L432-438)")


# ---------------------------------------------------------------------------
# Statement: membership and recomputes (Section 4.6 checks 3 onward, Section 5)
# ---------------------------------------------------------------------------

def check_statement(st: dict, cfg: VerifierConfig, derived: Dict[str, Any]) -> None:
    # -03 L301-307: an in-toto Statement with the predicate type of Section 4
    # (or the v2 URI of Section 8, L2194-2202).
    if st.get("_type") != INTOTO_STATEMENT_TYPE:
        raise Refusal("membership", "not an in-toto Statement v1 (-03 L301)")
    ptype = st.get("predicateType")
    if ptype not in cfg.predicate_types or ptype not in (PREDICATE_V1, PREDICATE_V2):
        raise Refusal("membership", f"predicateType {ptype!r} is not the type of Section 4 (-03 L301-307, L2194-2202)")
    inputs_vocab = INPUTS_V2 if ptype == PREDICATE_V2 else INPUTS_V1
    p = st.get("predicate")
    if not isinstance(p, dict):
        raise Refusal("membership", "predicate absent (-03 L301)")

    # Membership: the seventeen required members (-03 L309-312, L572-575).
    for name in REQUIRED_MEMBERS:
        require_member(p, name, "predicate")

    # hashAlgorithm first: every digest is read under it (-03 L1047-1051).
    alg = p["hashAlgorithm"]
    if not isinstance(alg, str) or not alg:
        raise Refusal("membership", "hashAlgorithm is not a name (-03 L1047)")
    if alg not in HASH_ALGORITHMS:
        # -03 L1078-1081: no verdict to reach on such a record.
        raise Unreadable(f"hashAlgorithm {alg!r} not implemented; no verdict reached (-03 L1078-1081)")

    if not isinstance(p["recordId"], str):
        raise Refusal("membership", "recordId is not a string (-03 L1043)")

    agent = check_agent(p, alg)
    delegation = check_delegation(p, alg)
    resource = check_resource(p, alg)
    decision = check_decision(p, alg)
    effect, interval = check_effect_shape(p, alg)
    evaluation = check_evaluation_shape(p, alg, inputs_vocab)
    check_oversight(p, alg)
    check_correlation(p, alg)
    check_posture(p, alg)
    check_remediation(p)
    observation = check_observation_shape(p, alg)
    check_field_evidence(p, observation)
    check_does_not_assert(p)
    check_sequence(p)
    tier_declared = require_closed(p["tier"], TIER, "tier", "L1666")
    require_closed(p["agreement"], AGREEMENT, "agreement", "L1207-1208")
    issued_at = parse_time(p["issuedAt"], "issuedAt")

    # Subject convention (-03 L376-404) and request digest (L1150-1157).
    check_subject_and_request_digest(st, p, alg, resource, decision, interval)
    # Write chain (-03 L1188-1200) and inScope (L1202-1205).
    check_write_chain(effect, interval, alg)
    # effect.observed derived from the write set (-03 L1170-1186).
    check_observed(effect, decision)
    # agreement and posture.agreement (-03 L1207-1212, L1237-1269).
    check_agreement(p, decision, effect)
    # issuedAt at or after sealedAt (-03 L1069-1076).
    if interval is not None:
        sealed = parse_time(interval.get("sealedAt"), "effect.interval.sealedAt")
        if issued_at < sealed:
            raise Refusal("recompute", "issuedAt earlier than effect.interval.sealedAt (-03 L1069-1076)")
    # Prior commitment (-03 L585-588, L1629-1634).
    check_prior_commitment(p, observation, agent, delegation, interval, alg)
    # Freshness and unavailable inputs (-03 L588-591, L1334-1385).
    check_freshness(evaluation, decision)
    # Consumption (-03 L1753-1818).
    derived["withinLimit"] = check_consumption(evaluation, decision, alg)
    # Coverage (-03 L1600-1609) and tier (-03 L1666-1694).
    derived["tier"] = check_tier(p, tier_declared, observation, effect, resource)


def check_agent(p: dict, alg: str) -> dict:
    agent = require_obj(p, "agent", "predicate")
    for m in ("id", "credentialDigest", "authentication", "signers"):
        require_member(agent, m, "agent")  # -03 L482-483, L1085-1100
    if not isinstance(agent["id"], str):
        raise Refusal("membership", "agent.id is not a string (-03 L1085)")
    require_digest(agent["credentialDigest"], alg, "agent.credentialDigest")
    require_closed(agent["authentication"], AUTHENTICATION, "agent.authentication", "L1087-1089")
    if not isinstance(agent["signers"], list) or not all(isinstance(s, str) for s in agent["signers"]):
        raise Refusal("membership", "agent.signers is not an array of key identifiers (-03 L1099-1100)")
    return agent


def check_delegation(p: dict, alg: str) -> dict:
    d = require_obj(p, "delegation", "predicate")
    for m in ("subject", "subjectKind", "authorityDigest"):
        require_member(d, m, "delegation")  # -03 L485-487, L1102-1111
    kind = require_closed(d["subjectKind"], SUBJECT_KIND, "delegation.subjectKind", "L1102")
    if not isinstance(d["subject"], str):
        raise Refusal("membership", "delegation.subject is not a string (-03 L1102-1105)")
    # -03 L1103-1108 (F2): subjectKind of none beside any other subject value
    # is malformed.
    if kind == "none" and d["subject"] != "none":
        raise Refusal("membership", "subjectKind none beside a named subject (-03 L1107-1108)")
    require_digest(d["authorityDigest"], alg, "delegation.authorityDigest")  # L1109-1111
    if "rootDigest" in d:
        require_digest(d["rootDigest"], alg, "delegation.rootDigest")  # L1113-1116
    return d


def check_resource(p: dict, alg: str) -> dict:
    r = require_obj(p, "resource", "predicate")
    for m in ("kind", "id", "binding"):
        require_member(r, m, "resource")  # -03 L489-490
    require_closed(r["kind"], RESOURCE_KIND, "resource.kind", "L1137")
    if not isinstance(r["id"], str):
        raise Refusal("membership", "resource.id is not a string (-03 L489-490)")
    binding = require_closed(r["binding"], BINDING, "resource.binding", "L1139-1141")
    # -03 L1139-1141 (F3, F3b, F3c): digest-bound MUST carry argumentsDigest,
    # not-bindable MUST NOT carry it in any spelling.
    if binding == "digest-bound":
        if "argumentsDigest" not in r:
            raise Refusal("membership", "digest-bound without argumentsDigest (-03 L1139-1141)")
        require_digest(r["argumentsDigest"], alg, "resource.argumentsDigest")
    elif "argumentsDigest" in r:
        raise Refusal("membership", "not-bindable carrying argumentsDigest (-03 L1139-1141)")
    return r


def check_decision(p: dict, alg: str) -> dict:
    d = require_obj(p, "decision", "predicate")
    for m in ("action", "requestDigest", "reported", "decisionPointId", "policyDigest"):
        require_member(d, m, "decision")  # -03 L939-946
    if not isinstance(d["action"], str):
        raise Refusal("membership", "decision.action is not a string (-03 L1150-1153)")
    require_closed(d["reported"], REPORTED, "decision.reported", "L1150")
    require_digest(d["requestDigest"], alg, "decision.requestDigest")
    require_digest(d["policyDigest"], alg, "decision.policyDigest")  # L1306-1311
    if not isinstance(d["decisionPointId"], str):
        raise Refusal("membership", "decision.decisionPointId is not a string (-03 L943)")
    # -03 L1334-1336: decision.reportedAt is the reference time of every
    # entry, so it is required.
    if "reportedAt" not in d:
        raise Refusal("membership", "decision.reportedAt absent: no reference time (-03 L1334-1336)")
    parse_time(d["reportedAt"], "decision.reportedAt")
    if "operationKey" in d and not isinstance(d["operationKey"], str):
        raise Refusal("membership", "decision.operationKey is not a string (-03 L1797-1799)")
    return d


def check_effect_shape(p: dict, alg: str) -> Tuple[dict, Optional[dict]]:
    e = require_obj(p, "effect", "predicate")
    for m in ("observed", "pathScope", "writes"):
        require_member(e, m, "effect")  # -03 L964-983
    require_closed(e["observed"], OBSERVED, "effect.observed", "L1170-1171")
    scope = e["pathScope"]
    if not isinstance(scope, list) or not all(isinstance(s, str) for s in scope):
        raise Refusal("membership", "effect.pathScope is not an array of paths (-03 L1202-1203)")
    for s in scope:
        # -03 L1202-1203 (V2): no glob metacharacter in a pathScope member.
        if any(c in GLOB_METACHARACTERS for c in s):
            raise Refusal("membership", f"pathScope member {s!r} carries a glob metacharacter (-03 L1202-1203)")
    writes = e["writes"]
    if not isinstance(writes, list):
        raise Refusal("membership", "effect.writes is not an array (-03 L1188-1194)")
    for i, w in enumerate(writes):
        if not isinstance(w, dict):
            raise Refusal("membership", f"effect.writes[{i}] is not an object")
        for m in ("path", "preStateDigest", "postStateDigest", "requestDigest", "inScope"):
            require_member(w, m, f"effect.writes[{i}]")  # -03 L975-981
        if not isinstance(w["path"], str):
            raise Refusal("membership", f"effect.writes[{i}].path is not a string")
        require_digest(w["preStateDigest"], alg, f"effect.writes[{i}].preStateDigest")
        require_digest(w["postStateDigest"], alg, f"effect.writes[{i}].postStateDigest")
        # -03 L1164-1168: a request digest or the literal unattributed.
        if w["requestDigest"] != "unattributed":
            require_digest(w["requestDigest"], alg, f"effect.writes[{i}].requestDigest")
        if not isinstance(w["inScope"], bool):
            raise Refusal("membership", f"effect.writes[{i}].inScope is not a boolean (-03 L1204-1205)")
        # -03 L1204-1205 (V3): inScope is derived from the path, never a
        # producer opinion.
        if w["inScope"] != in_scope(w["path"], scope):
            raise Refusal("recompute", f"effect.writes[{i}].inScope differs from the value its path derives (-03 L1204-1205)")
    interval = e.get("interval")
    if interval is not None:
        if not isinstance(interval, dict):
            raise Refusal("membership", "effect.interval is not an object")
        for m in ("beforeRoot", "afterRoot", "baseResolution", "openedAt", "sealedAt"):
            require_member(interval, m, "effect.interval")  # -03 L966-972
        parse_time(interval["openedAt"], "effect.interval.openedAt")
        parse_time(interval["sealedAt"], "effect.interval.sealedAt")
        if interval["baseResolution"] == "empty-tree":
            # -03 L1196-1200 (E3): empty-tree carries the empty-tree constant
            # of the declared algorithm.
            if interval["beforeRoot"] != EMPTY_TREE[alg]:
                raise Refusal("recompute", "baseResolution empty-tree without the empty-tree constant (-03 L1196-1200)")
        require_digest(interval["beforeRoot"], alg, "effect.interval.beforeRoot")
        require_digest(interval["afterRoot"], alg, "effect.interval.afterRoot")
    return e, interval


def check_evaluation_shape(p: dict, alg: str, inputs_vocab: set) -> dict:
    ev = p["evaluation"]
    if not isinstance(ev, dict):
        raise Refusal("membership", "evaluation is not an object (-03 L1278)")
    # -03 L1382-1384 (EV12): no member other than status, inputs and
    # unavailableInput.
    extra = set(ev) - {"status", "inputs", "unavailableInput"}
    if extra:
        raise Refusal("membership", f"evaluation carries undefined member(s) {sorted(extra)} (-03 L1382-1384)")
    status = require_closed(require_member(ev, "status", "evaluation"), STATUS,
                            "evaluation.status", "L1279-1280")
    # -03 L1317 (EV13): inputs under both statuses.
    if "inputs" not in ev:
        raise Refusal("membership", "evaluation.inputs absent (-03 L1317)")
    inputs = ev["inputs"]
    if not isinstance(inputs, list):
        raise Refusal("membership", "evaluation.inputs is not an array (-03 L1317-1319)")
    seen = set()
    for i, ent in enumerate(inputs):
        where = f"evaluation.inputs[{i}]"
        if not isinstance(ent, dict):
            raise Refusal("membership", f"{where} is not an object (-03 L1318-1319)")
        # -03 L1320-1322 (FR5, FR6): input, lastResult, maxAge each present.
        for m in ("input", "lastResult", "maxAge"):
            if m not in ent:
                raise Refusal("membership", f"{where}.{m} absent (-03 L1320-1322)")
        # -03 L1321-1322 (FR11): no other member except consumption.
        extra = set(ent) - ENTRY_MEMBERS - {"consumption"}
        if extra:
            raise Refusal("membership", f"{where} carries undefined member(s) {sorted(extra)} (-03 L1320-1322)")
        name = require_closed(ent["input"], inputs_vocab, f"{where}.input", "L1324-1325, L1418-1450")
        # -03 L1318-1319 (FR10): each input named at most once.
        if name in seen:
            raise Refusal("membership", f"input {name} named in two entries (-03 L1318-1319)")
        seen.add(name)
        # -03 L1327-1329: lastResult is a time or the literal none.
        if ent["lastResult"] != "none":
            parse_time(ent["lastResult"], f"{where}.lastResult")
        # -03 L1331-1332 (FR13): maxAge is a non-negative integer.
        if not is_int(ent["maxAge"]) or ent["maxAge"] < 0:
            raise Refusal("membership", f"{where}.maxAge is not a non-negative integer (-03 L1331-1332)")
        # -03 L1760-1763 (CS6): consumption only on a consumption-state entry.
        if "consumption" in ent and name != "consumption-state":
            raise Refusal("membership", f"{where} carries consumption on input {name} (-03 L1760-1763)")
    if status == "not-evaluated":
        # -03 L1376-1379 (EV1, EV5, EV6, EV7, EV4): a non-empty array naming
        # inputs from the closed set, each once.
        if "unavailableInput" not in ev:
            raise Refusal("membership", "not-evaluated without unavailableInput (-03 L1376-1379)")
        ua = ev["unavailableInput"]
        if not isinstance(ua, list) or not ua:
            raise Refusal("membership", "unavailableInput is not a non-empty array (-03 L1376-1379)")
        for v in ua:
            require_closed(v, inputs_vocab, "unavailableInput[]", "L1376-1379")
        if len(set(ua)) != len(ua):
            raise Refusal("membership", "unavailableInput names one input twice (-03 L1377-1378)")
    elif "unavailableInput" in ev:
        # -03 L1379-1381 (EV2, EV8, EV9, EV10): in no spelling under evaluated.
        raise Refusal("membership", "evaluated carrying unavailableInput (-03 L1379-1381)")
    return ev


def check_oversight(p: dict, alg: str) -> None:
    if "oversight" not in p:
        return  # -03 L1519: optional
    o = p["oversight"]
    if not isinstance(o, dict):
        raise Refusal("membership", "oversight is not an object (-03 L1519)")
    # -03 L1532-1533 (OV2): no other member.
    extra = set(o) - {"act", "recordDigest"}
    if extra:
        raise Refusal("membership", f"oversight carries undefined member(s) {sorted(extra)} (-03 L1531-1533)")
    # -03 L1527-1528 (OV1, OV3): act required, from the closed set.
    if "act" not in o:
        raise Refusal("membership", "oversight without act (-03 L1527-1528)")
    require_closed(o["act"], OVERSIGHT_ACT, "oversight.act", "L1527-1528")
    if "recordDigest" in o:
        require_digest(o["recordDigest"], alg, "oversight.recordDigest")  # L1529-1531


def check_correlation(p: dict, alg: str) -> None:
    c = require_obj(p, "correlation", "predicate")
    for m in ("id", "scope", "timeBasis"):
        require_member(c, m, "correlation")  # -03 L985-990
    require_closed(c["scope"], CORRELATION_SCOPE, "correlation.scope", "L1539")
    tb = c["timeBasis"]
    # -03 L1545-1546 (F5): beacon-anchored MUST carry externalAnchor, asserted
    # MUST NOT.
    if tb == "beacon-anchored":
        if "externalAnchor" not in c:
            raise Refusal("membership", "beacon-anchored without externalAnchor (-03 L1545-1546)")
    elif tb == "asserted":
        if "externalAnchor" in c:
            raise Refusal("membership", "asserted carrying externalAnchor (-03 L1545-1546)")
    else:
        raise Refusal("membership", f"correlation.timeBasis {tb!r} is neither beacon-anchored nor asserted (-03 L1545-1546)")
    if "externalAnchor" in c:
        xa = c["externalAnchor"]
        if not isinstance(xa, dict):
            raise Refusal("membership", "externalAnchor is not an object (-03 L1546-1550)")
        require_closed(require_member(xa, "kind", "correlation.externalAnchor"), ANCHOR_KIND,
                       "correlation.externalAnchor.kind", "L1546-1547")
        require_digest(require_member(xa, "digest", "correlation.externalAnchor"), alg,
                       "correlation.externalAnchor.digest")
        # -03 L1549-1552 (N1): a verifier holding only the record accepts it
        # and does not date it from this anchor. Nothing more is checked.


def check_posture(p: dict, alg: str) -> None:
    po = require_obj(p, "posture", "predicate")
    for m in ("reported", "reportedDigest", "observed", "observedDigest", "assessedAt", "agreement"):
        require_member(po, m, "posture")  # -03 L500-509, L991-998
    require_closed(po["reported"], POSTURE, "posture.reported", "L1560-1564")
    require_closed(po["observed"], POSTURE, "posture.observed", "L1560-1564")
    require_digest(po["reportedDigest"], alg, "posture.reportedDigest")
    require_digest(po["observedDigest"], alg, "posture.observedDigest")
    parse_time(po["assessedAt"], "posture.assessedAt")
    # -03 L1210-1212 (F6): posture.agreement derives from the two postures:
    # agree when equal byte for byte, disagree when unequal.
    want = "agree" if po["reported"] == po["observed"] else "disagree"
    if po["agreement"] != want:
        raise Refusal("recompute", f"posture.agreement {po['agreement']!r}, the postures derive {want!r} (-03 L1210-1212)")


def check_remediation(p: dict) -> None:
    rem = p["remediation"]
    if not isinstance(rem, list):
        raise Refusal("membership", "remediation is not an array (-03 L1578)")
    for i, ev in enumerate(rem):
        where = f"remediation[{i}]"
        if not isinstance(ev, dict):
            raise Refusal("membership", f"{where} is not an object (-03 L1578)")
        for m in ("cause", "signalReceivedAt", "enforcedAt", "enforcement"):
            require_member(ev, m, where)  # -03 L1000-1014, L1578-1583
        require_closed(ev["cause"], REMEDIATION_CAUSE, f"{where}.cause", "L1578-1580")
        require_closed(ev["enforcement"], ENFORCEMENT, f"{where}.enforcement", "L1580-1582")
        sig = parse_time(ev["signalReceivedAt"], f"{where}.signalReceivedAt")
        enf = parse_time(ev["enforcedAt"], f"{where}.enforcedAt")
        # -03 L1582-1583 (F7): enforcedAt at or after signalReceivedAt. No
        # bound on the gap (-03 L1589-1594, L2046-2053; N2).
        if enf < sig:
            raise Refusal("recompute", f"{where}.enforcedAt before signalReceivedAt (-03 L1582-1583)")


def check_observation_shape(p: dict, alg: str) -> dict:
    ob = require_obj(p, "observation", "predicate")
    require_closed(require_member(ob, "vantage", "observation"), VANTAGE, "observation.vantage", "L1598")
    cov = ob.get("coverage")
    if not isinstance(cov, dict):
        raise Refusal("membership", "observation.coverage absent (-03 L1600-1602)")
    if not isinstance(cov.get("scopeComplete"), bool):
        raise Refusal("membership", "coverage.scopeComplete is not a boolean (-03 L1600-1601)")
    gaps = cov.get("gaps")
    if not isinstance(gaps, list) or not all(isinstance(g, str) for g in gaps):
        raise Refusal("membership", "coverage.gaps is not an array of paths (-03 L1602)")
    if "priorCommitment" in ob and not isinstance(ob["priorCommitment"], dict):
        raise Refusal("membership", "priorCommitment is not an object (-03 L1612-1614)")
    return ob


def check_field_evidence(p: dict, observation: dict) -> None:
    fe = p["fieldEvidence"]
    if not isinstance(fe, dict):
        raise Refusal("membership", "fieldEvidence is not an object (-03 L1643)")
    # -03 L1643, L1663 (FE1): exactly the seven keys.
    if set(fe) != FIELD_EVIDENCE_KEYS:
        raise Refusal("membership", "fieldEvidence does not carry exactly the seven keys (-03 L1643, L1663)")
    for k, v in fe.items():
        require_closed(v, FIELD_EVIDENCE, f"fieldEvidence.{k}", "L1643-1649")
    # -03 L1663-1664 (FE2): no substrate-covered while vantage is self.
    if observation["vantage"] == "self" and "substrate-covered" in fe.values():
        raise Refusal("membership", "substrate-covered declared beside vantage self (-03 L1663-1664)")


def check_does_not_assert(p: dict) -> None:
    dna = p["doesNotAssert"]
    # -03 L1636-1638: an array of explicit statements.
    if not isinstance(dna, list) or not all(isinstance(s, str) for s in dna):
        raise Refusal("membership", "doesNotAssert is not an array of statements (-03 L1636-1638)")


def check_sequence(p: dict) -> None:
    if "sequence" not in p:
        return  # -03 L1890: optional
    s = p["sequence"]
    if not isinstance(s, dict):
        raise Refusal("membership", "sequence is not an object (-03 L1890-1895)")
    # -03 L1893-1895 (SQ2): exactly boundary and seq.
    if set(s) != {"boundary", "seq"}:
        raise Refusal("membership", "sequence does not carry exactly boundary and seq (-03 L1893-1895)")
    if not isinstance(s["boundary"], str):
        raise Refusal("membership", "sequence.boundary is not a string (-03 L1894-1895)")
    # -03 L1895 (SQ3): seq is a positive integer.
    if not is_int(s["seq"]) or s["seq"] < 1:
        raise Refusal("membership", "sequence.seq is not a positive integer (-03 L1895)")


def check_subject_and_request_digest(st, p, alg, resource, decision, interval) -> None:
    subj = st.get("subject")
    if not isinstance(subj, list) or not subj:
        raise Refusal("membership", "subject absent (-03 L376-380)")
    for i, s in enumerate(subj):
        if not isinstance(s, dict) or not isinstance(s.get("digest"), dict) or not isinstance(s.get("name"), str):
            raise Refusal("membership", f"subject[{i}] has no name and digest map (-03 L376-380)")
    # -03 L402-403 (S1, I1r): the second entry iff an interval is carried.
    want = 2 if interval is not None else 1
    if len(subj) != want:
        raise Refusal("recompute", f"{len(subj)} subject entries where the interval requires {want} (-03 L376-380, L402-404)")
    # -03 L397-399: bind on the digest map, under the declared algorithm.
    first = subj[0]["digest"].get(alg)
    # -03 L1150-1157 (F4, A6): requestDigest equals the first subject digest
    # and the RFC 8785 digest over action, argumentsDigest (only when
    # digest-bound), resourceId and resourceKind.
    if first != decision["requestDigest"]:
        raise Refusal("recompute", "decision.requestDigest differs from the first subject digest (-03 L1151-1152)")
    pre = {"action": decision["action"], "resourceId": resource["id"], "resourceKind": resource["kind"]}
    if resource["binding"] == "digest-bound":
        pre["argumentsDigest"] = resource["argumentsDigest"]
    if jcs_digest(pre, alg) != decision["requestDigest"]:
        raise Refusal("recompute", "request digest does not recompute from action, arguments and resource (-03 L1151-1157)")
    if interval is not None:
        # -03 L403-404 (S2): second entry's digest is the after-state root.
        if subj[1]["digest"].get(alg) != interval["afterRoot"]:
            raise Refusal("recompute", "second subject digest differs from afterRoot (-03 L403-404)")


def check_write_chain(effect: dict, interval: Optional[dict], alg: str) -> None:
    writes = effect["writes"]
    if interval is None:
        if writes:
            raise Refusal("recompute", "writes with no interval to replay them from (-03 L1188-1194)")
        return
    # -03 L1188-1194 (E2, E4, E5, E6): replay the ordered chain.
    running = interval["beforeRoot"]
    for i, w in enumerate(writes):
        if w["preStateDigest"] != running:
            raise Refusal("recompute", f"effect.writes[{i}].preStateDigest is not the running root (-03 L1190-1193)")
        running = w["postStateDigest"]
    if running != interval["afterRoot"]:
        raise Refusal("recompute", "write chain does not reproduce afterRoot (-03 L1193-1194)")


def derive_observed(effect: dict, decision: dict) -> str:
    # -03 L1172-1184: occurred when a write is attributed to this request;
    # none when none is and no unattributed write lies inside pathScope;
    # unknown otherwise. inScope is derived from the path.
    scope = effect["pathScope"]
    if any(w["requestDigest"] == decision["requestDigest"] for w in effect["writes"]):
        return "occurred"
    if not any(w["requestDigest"] == "unattributed" and in_scope(w["path"], scope) for w in effect["writes"]):
        return "none"
    return "unknown"


def check_observed(effect: dict, decision: dict) -> None:
    want = derive_observed(effect, decision)
    # -03 L1172-1186 (E1, T1, A7): effect.observed MUST be derivable.
    if effect["observed"] != want:
        raise Refusal("recompute", f"effect.observed {effect['observed']!r}, the write set derives {want!r} (-03 L1172-1186)")


AGREEMENT_TABLE = {  # -03 L1237-1253, Table 2
    ("permit", "occurred"): "agree",
    ("permit-with-conditions", "occurred"): "agree",
    ("deny", "none"): "agree",
    ("deny", "occurred"): "disagree",
    ("permit", "none"): "not-exercised",
    ("permit-with-conditions", "none"): "not-exercised",
}


def check_agreement(p: dict, decision: dict, effect: dict) -> None:
    obs = effect["observed"]
    want = "indeterminate" if obs == "unknown" else AGREEMENT_TABLE[(decision["reported"], obs)]
    # -03 L1207-1209, L1264-1269 (D1, D2, D3): derivable by Table 2, which
    # never yields one-sided.
    if p["agreement"] != want:
        raise Refusal("recompute", f"agreement {p['agreement']!r}, Table 2 derives {want!r} (-03 L1207-1209, L1264-1269)")


def check_prior_commitment(p, observation, agent, delegation, interval, alg) -> None:
    pc = observation.get("priorCommitment")
    if pc is None:
        return
    if "committedAt" in pc and interval is not None:
        # -03 L1629-1630 (C2): strictly before openedAt.
        if parse_time(pc["committedAt"], "priorCommitment.committedAt") >= parse_time(interval["openedAt"], "openedAt"):
            raise Refusal("recompute", "priorCommitment.committedAt not strictly before openedAt (-03 L1629-1630)")
    if "commitmentDigest" in pc and interval is not None:
        # -03 L1630-1631 (C3): RFC 8785 digest over authorityDigest,
        # beforeRoot, recordId and witnessNonce.
        pre = {"authorityDigest": delegation["authorityDigest"], "beforeRoot": interval["beforeRoot"],
               "recordId": p["recordId"], "witnessNonce": pc.get("witnessNonce")}
        if "witnessNonce" not in pc or jcs_digest(pre, alg) != pc["commitmentDigest"]:
            raise Refusal("recompute", "commitmentDigest does not recompute (-03 L1630-1631)")
    if "keyid" in pc:
        # -03 L1632-1634 (C1): keyid absent from agent.signers.
        if pc["keyid"] in agent["signers"]:
            raise Refusal("recompute", "priorCommitment.keyid appears in agent.signers (-03 L1632-1634)")
    # priorCommitment.sig is carried and not checked: the draft defines no
    # preimage for it (-03 L1020-1026 names the member only).


def check_freshness(evaluation: dict, decision: dict) -> None:
    ref = parse_time(decision["reportedAt"], "decision.reportedAt")
    unavailable = set()
    for ent in evaluation["inputs"]:
        if ent["lastResult"] == "none":
            # -03 L1338-1339 (A10, FR4): never established.
            unavailable.add(ent["input"])
            continue
        last = parse_time(ent["lastResult"], "lastResult")
        # -03 L1339-1350 (FR9): later than reportedAt by more than the skew
        # tolerance of 300 seconds is malformed.
        if last - ref > SKEW_TOLERANCE_MAX:
            raise Refusal("recompute", f"{ent['input']}.lastResult later than reportedAt by more than 300 s (-03 L1339-1350)")
        # -03 L1334-1338 (FR2, FR3): age = reportedAt - lastResult; fresh when
        # at most maxAge, stale when greater.
        if ref - last > ent["maxAge"]:
            unavailable.add(ent["input"])
    status = evaluation["status"]
    # -03 L1373-1378 (FR8, FR7, FR15): derived set empty under evaluated;
    # under not-evaluated unavailableInput names exactly that set.
    if status == "evaluated":
        if unavailable:
            raise Refusal("recompute", f"evaluated beside stale or never-established input(s) {sorted(unavailable)} (-03 L1373-1376)")
    else:
        if set(evaluation["unavailableInput"]) != unavailable:
            raise Refusal("recompute", f"unavailableInput {evaluation['unavailableInput']} differs from the derived set {sorted(unavailable)} (-03 L1376-1379)")


def check_consumption(evaluation: dict, decision: dict, alg: str) -> Optional[bool]:
    within = None
    for ent in evaluation["inputs"]:
        if "consumption" not in ent:
            continue
        c = ent["consumption"]
        if not isinstance(c, dict):
            raise Refusal("membership", "consumption is not an object (-03 L1765-1766)")
        # -03 L1765-1766 (CS9): each member and no other.
        if set(c) != CONSUMPTION_MEMBERS:
            raise Refusal("membership", "consumption does not carry exactly its eight members (-03 L1765-1788)")
        require_digest(c["limitDigest"], alg, "consumption.limitDigest")  # L1768-1770
        if not isinstance(c["unit"], str):
            raise Refusal("membership", "consumption.unit is not a string (-03 L1772-1773)")
        # -03 L1775-1779 (CS5) and L1786: non-negative integers.
        for m in ("limit", "consumed", "reserved", "requested", "stateRevision"):
            if not is_int(c[m]) or c[m] < 0:
                raise Refusal("membership", f"consumption.{m} is not a non-negative integer (-03 L1775-1779, L1786)")
        held = c["held"]
        if not isinstance(held, list):
            raise Refusal("membership", "consumption.held is not an array (-03 L1781-1784)")
        keys = []
        for i, h in enumerate(held):
            if not isinstance(h, dict) or set(h) != HELD_MEMBERS:
                raise Refusal("membership", f"held[{i}] does not carry operationKey, requestDigest and amount (-03 L1781-1784)")
            if not isinstance(h["operationKey"], str):
                raise Refusal("membership", f"held[{i}].operationKey is not a string (-03 L1797-1799)")
            require_digest(h["requestDigest"], alg, f"held[{i}].requestDigest")
            if not is_int(h["amount"]) or h["amount"] < 0:
                raise Refusal("membership", f"held[{i}].amount is not a non-negative integer (-03 L1775-1784)")
            keys.append(h["operationKey"])
        # -03 L1782-1784 (CS3): held amounts sum to reserved.
        if sum(h["amount"] for h in held) != c["reserved"]:
            raise Refusal("recompute", "held amounts do not sum to reserved (-03 L1782-1783)")
        # -03 L1784 (CS13): no two held entries share an operationKey.
        if len(set(keys)) != len(keys):
            raise Refusal("recompute", "two held entries carry one operationKey (-03 L1784)")
        # -03 L1800-1801 (CS14): operationKey required beside consumption.
        if "operationKey" not in decision:
            raise Refusal("membership", "consumption without decision.operationKey (-03 L1800-1801)")
        op = decision["operationKey"]
        match = [h for h in held if h["operationKey"] == op]
        if match:
            # -03 L1803-1806 (CS4, CS10): a retry's requestDigest and
            # requested equal the held entry's; it is not counted again.
            h = match[0]
            if h["requestDigest"] != decision["requestDigest"] or h["amount"] != c["requested"]:
                raise Refusal("recompute", "operationKey held for another request or amount (-03 L1803-1806)")
            total = c["consumed"] + c["reserved"]
        else:
            # -03 L1806-1808 (CS11, CS12): a key not held is a new operation.
            total = c["consumed"] + c["reserved"] + c["requested"]
        # -03 L1812-1815 (CS2): withinLimit is derived and never a refusal.
        within = total <= c["limit"]
    return within


def check_tier(p, declared, observation, effect, resource) -> str:
    cov = observation["coverage"]
    scope = effect["pathScope"]
    gap_inside = any(in_scope(g, scope) for g in cov["gaps"])
    # -03 L1603-1606 (V4, V5): a gap inside pathScope beside scopeComplete of
    # true is malformed whatever tier is declared.
    if cov["scopeComplete"] and gap_inside:
        raise Refusal("recompute", "a gap inside pathScope beside scopeComplete of true (-03 L1603-1606)")
    pc = observation.get("priorCommitment")
    clauses = {
        "vantage below-observed (TI1)": observation["vantage"] == "below-observed",
        "priorCommitment present and complete (TI2)": isinstance(pc, dict) and all(m in pc for m in PRIOR_COMMITMENT_MEMBERS),
        "pathScope non-empty (TI3)": len(scope) > 0,
        "scopeComplete true (TI4, TI6)": cov["scopeComplete"] is True,
        "binding digest-bound (TI5)": resource["binding"] == "digest-bound",
    }
    # -03 L1671-1676: authoritative iff all five clauses hold.
    tier = "authoritative" if all(clauses.values()) else "voluntary"
    # -03 L1685-1687: declared authoritative failing a clause is malformed and
    # never downgraded. L1690-1692: the opposite mismatch is well formed.
    if declared == "authoritative" and tier != "authoritative":
        failed = [k for k, v in clauses.items() if not v]
        raise Refusal("tier", f"authoritative declared, clause(s) failed: {failed} (-03 L1666-1687)")
    return tier


# ---------------------------------------------------------------------------
# Retained records: Section 4.7 and the dating check (4.7.1, 4.7.2)
# ---------------------------------------------------------------------------

@dataclass
class Anchor:
    index: int
    kind: str
    origin: str
    valid: bool
    time: Optional[float] = None
    height: Optional[int] = None  # opentimestamps only
    note: str = ""


def _anchor_before(a: Anchor, b: Anchor) -> bool:
    # -03 L877-880, L2110-2111: two opentimestamps times order by height.
    if a.kind == "opentimestamps" and b.kind == "opentimestamps":
        return (a.height, a.index) < (b.height, b.index)
    return (a.time, a.index) < (b.time, b.index)


def _earliest(anchors: List[Anchor]) -> Optional[Anchor]:
    best = None
    for a in anchors:
        if best is None or _anchor_before(a, best):
            best = a
    return best


def _tolerance(cfg: VerifierConfig, anchor: Anchor) -> int:
    # -03 L285-290, L869-870: 7200 s in place of the skew tolerance for a
    # comparison with an opentimestamps time.
    return OTS_TOLERANCE if anchor.kind == "opentimestamps" else cfg.skew


def validate_transparency_log(proof: bytes, digest: bytes, cfg: VerifierConfig) -> Tuple[bool, Optional[float], str]:
    """-03 L851-857: an inclusion proof that recomputes from envelopeDigest to
    a tree head the verifier holds by a means other than its signature is a
    hash commitment; its time is the tree head's timestamp. The proof is an
    RFC 9162 TransItem of type inclusion_proof_v2 (0x0106), leaf hash
    SHA-256(0x00 || envelopeDigest), path recomputed per RFC 9162 2.1.3.2.
    The signed-token basis (tree head signature under a log key) is not
    implemented: the verifier holds no log key."""
    try:
        pos = 0
        vt = int.from_bytes(proof[0:2], "big"); pos = 2
        if vt != 0x0106:
            return False, None, "not an inclusion_proof_v2 TransItem"
        n = proof[pos]; pos += 1
        log_id = proof[pos:pos + n]; pos += n
        tree_size = int.from_bytes(proof[pos:pos + 8], "big"); pos += 8
        leaf_index = int.from_bytes(proof[pos:pos + 8], "big"); pos += 8
        plen = int.from_bytes(proof[pos:pos + 2], "big"); pos += 2
        end = pos + plen
        path = []
        while pos < end:
            hl = proof[pos]; pos += 1
            path.append(proof[pos:pos + hl]); pos += hl
        if pos != end or end != len(proof):
            return False, None, "inclusion proof has trailing or short bytes"
    except (IndexError, ValueError):
        return False, None, "inclusion proof does not decode"
    if leaf_index >= tree_size:
        return False, None, "leaf index outside the tree"
    h = hashlib.sha256
    r = h(b"\x00" + digest).digest()
    fn, sn = leaf_index, tree_size - 1
    for p in path:
        if sn == 0:
            return False, None, "inclusion path too long"
        if fn & 1 or fn == sn:
            r = h(b"\x01" + p + r).digest()
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = h(b"\x01" + r + p).digest()
        fn >>= 1
        sn >>= 1
    if sn != 0:
        return False, None, "inclusion path too short"
    for head in cfg.tree_heads:
        if (bytes.fromhex(head["logId"]) == log_id and int(head["treeSize"]) == tree_size
                and bytes.fromhex(head["rootHash"]) == r):
            return True, parse_time(head["timestamp"], "treeHead.timestamp"), ""
    return False, None, "recomputes to no tree head the verifier holds"


OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_BITCOIN_TAG = bytes.fromhex("0588960d73d71901")
OTS_PENDING_TAG = bytes.fromhex("83dfe30d2ef90c8e")


class _Reader:
    def __init__(self, b: bytes):
        self.b, self.i = b, 0

    def byte(self) -> int:
        if self.i >= len(self.b):
            raise ValueError("truncated")
        v = self.b[self.i]
        self.i += 1
        return v

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise ValueError("truncated")
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def varuint(self) -> int:
        v, shift = 0, 0
        while True:
            c = self.byte()
            v |= (c & 0x7F) << shift
            if not c & 0x80:
                return v
            shift += 7

    def varbytes(self) -> bytes:
        return self.take(self.varuint())


def _ots_walk(rd: _Reader, msg: bytes, out: List[Tuple[bytes, bytes, bytes]], depth: int = 0) -> None:
    """Walk an OpenTimestamps timestamp tree, collecting (tag, payload, msg)
    for every attestation."""
    if depth > 256:
        raise ValueError("timestamp tree too deep")

    def one(tag: int, m: bytes) -> None:
        if tag == 0x00:
            atag = rd.take(8)
            out.append((atag, rd.varbytes(), m))
            return
        if tag == 0x08:
            m2 = hashlib.sha256(m).digest()
        elif tag == 0x02:
            m2 = hashlib.sha1(m).digest()
        elif tag == 0x03:
            m2 = hashlib.new("ripemd160", m).digest()
        elif tag == 0xF0:
            m2 = m + rd.varbytes()
        elif tag == 0xF1:
            m2 = rd.varbytes() + m
        elif tag == 0xF2:
            m2 = m[::-1]
        elif tag == 0xF3:
            m2 = m.hex().encode()
        else:
            raise ValueError(f"unknown op 0x{tag:02x}")
        _ots_walk(rd, m2, out, depth + 1)

    tag = rd.byte()
    while tag == 0xFF:
        one(rd.byte(), msg)
        tag = rd.byte()
    one(tag, msg)


def validate_opentimestamps(proof: bytes, digest: bytes, cfg: VerifierConfig) -> Tuple[bool, Optional[float], Optional[int], str]:
    """-03 L859-863: validates when the proof recomputes from envelopeDigest
    to the Merkle root in a block header the verifier holds; its time is that
    header's timestamp. Proof is a DetachedTimestampFile. A pending
    attestation dates nothing (-03 L682-684, R8/R12)."""
    try:
        rd = _Reader(proof)
        if rd.take(len(OTS_MAGIC)) != OTS_MAGIC:
            return False, None, None, "not a DetachedTimestampFile"
        if rd.varuint() != 1:
            return False, None, None, "unknown OpenTimestamps version"
        if rd.byte() != 0x08:
            return False, None, None, "file hash is not sha256"
        if rd.take(32) != digest:
            return False, None, None, "proof is over another digest"
        atts: List[Tuple[bytes, bytes, bytes]] = []
        _ots_walk(rd, digest, atts)
        if rd.i != len(proof):
            return False, None, None, "trailing bytes after the timestamp"
    except (ValueError, IndexError) as exc:
        return False, None, None, f"proof does not decode: {exc}"
    best = None
    for tag, payload, msg in atts:
        if tag != OTS_BITCOIN_TAG:
            continue  # pending or other attestations never validate offline
        try:
            height = _Reader(payload).varuint()
        except ValueError:
            continue
        header = cfg.block_headers.get(height)
        if header is None or len(header) != 80:
            continue
        if header[36:68] != msg:
            continue
        t = float(int.from_bytes(header[68:72], "little"))
        if best is None or height < best[1]:
            best = (t, height)
    if best is None:
        return False, None, None, "no Bitcoin attestation recomputes to a held block header"
    return True, best[0], best[1], ""


@dataclass
class DatingResult:
    entry: str = "well-formed"
    reason: str = ""
    dated_at: Optional[float] = None
    receipt: Optional[str] = None
    delegation_root: str = "not-established"
    delegation_root_algorithm: Optional[str] = None
    anchors: List[Anchor] = field(default_factory=list)


def _jwk_algorithm(material: bytes) -> Optional[str]:
    try:
        doc = json.loads(material)
    except ValueError:
        return None
    keys = doc.get("keys") if isinstance(doc, dict) else None
    if not keys:
        return None
    k = keys[0]
    if k.get("kty") == "OKP":
        return k.get("crv")
    return k.get("alg") or k.get("kty")


def dating_check(retained: dict, env: EnvelopeResult, cfg: VerifierConfig) -> DatingResult:
    """The dating check of Section 4.7.1 (-03 L748-812) with the anchor rules
    of Section 4.7.2 (-03 L814-880), and the delegation root of Section 4.6
    (-03 L608-625). The result sits beside the envelope's verdict."""
    res = DatingResult()
    entry = retained.get("retention")
    envelope = retained.get("envelope")
    st = env.statement if isinstance(env.statement, dict) else {}
    pred = st.get("predicate") if isinstance(st.get("predicate"), dict) else {}
    alg = pred.get("hashAlgorithm", "sha256")
    if alg not in HASH_ALGORITHMS:
        res.entry, res.reason = "unreadable", "hashAlgorithm not implemented (-03 L1078-1081)"
        return res

    def malformed(reason: str) -> None:
        if res.entry == "well-formed":
            res.entry, res.reason = "malformed", reason

    if not isinstance(entry, dict):
        res.entry, res.reason = "malformed", "retention entry is not an object (-03 L636-637)"
        return res

    # -03 L663-666: envelopeDigest is the digest of the RFC 8785 bytes of the
    # envelope object; every anchor is over this value.
    computed = HASH_ALGORITHMS[alg][0](jcs.canonicalize(envelope)).digest()
    if entry.get("envelopeDigest") != computed.hex():
        malformed("envelopeDigest does not name the envelope beside it (-03 L663-666)")

    # Anchors (-03 L677-694) validated under Section 4.7.2.
    anchors_raw = entry.get("anchors")
    if not isinstance(anchors_raw, list):
        malformed("anchors is not an array (-03 L677)")
        anchors_raw = []
    for i, a in enumerate(anchors_raw):
        if not isinstance(a, dict):
            malformed(f"anchors[{i}] is not an object (-03 L686-689)")
            continue
        kind, origin = a.get("kind"), a.get("origin")
        if kind not in ANCHOR_KIND:
            malformed(f"anchors[{i}].kind outside the closed set (-03 L686-687)")
            continue
        if origin not in ORIGIN:
            malformed(f"anchors[{i}].origin outside retainer, received (-03 L688)")
            continue
        try:
            proof = b64decode_strict(a.get("proof"))
        except (ValueError, binascii.Error):
            res.anchors.append(Anchor(i, kind, origin, False, note="proof is not base64"))
            continue
        if kind == "transparency-log":
            ok, t, note = validate_transparency_log(proof, computed, cfg)
            res.anchors.append(Anchor(i, kind, origin, ok, t, note=note))
        elif kind == "opentimestamps":
            ok, t, h, note = validate_opentimestamps(proof, computed, cfg)
            res.anchors.append(Anchor(i, kind, origin, ok, t, h, note=note))
        else:
            # rfc3161: signed token (-03 L845-849). Not implemented: the
            # verifier holds no TSA trust anchor, so it never validates here.
            res.anchors.append(Anchor(i, kind, origin, False, note="rfc3161 validation not implemented"))

    # validationData (-03 L696-711): at most one delegation-root element, and
    # only for a record carrying delegation.rootDigest.
    vd = entry.get("validationData", [])
    root_elems = []
    if not isinstance(vd, list):
        malformed("validationData is not an array (-03 L696-705)")
        vd = []
    for i, el in enumerate(vd):
        if not isinstance(el, dict):
            malformed(f"validationData[{i}] is not an object (-03 L703-705)")
            continue
        f = el.get("for")
        if not (f in ("envelope", "delegation-root") or is_int(f)):
            malformed(f"validationData[{i}].for names nothing defined (-03 L703-705)")
        if f == "delegation-root":
            root_elems.append(el)
    delegation = pred.get("delegation") if isinstance(pred.get("delegation"), dict) else {}
    root_digest = delegation.get("rootDigest")
    if len(root_elems) > 1 or (root_elems and root_digest is None):
        malformed("delegation-root element more than once or beside no rootDigest (-03 L706-708)")
    elif root_elems and root_digest is not None:
        # -03 L608-625, L1133-1135: digest over the material's bytes as the
        # element carries them equals rootDigest; algorithm derived from it.
        try:
            material = b64decode_strict(root_elems[0].get("value"))
        except (ValueError, binascii.Error):
            material = b""
        if HASH_ALGORITHMS[alg][0](material).hexdigest() == root_digest:
            res.delegation_root = "established"
            res.delegation_root_algorithm = _jwk_algorithm(material)
        else:
            malformed("delegation-root element's digest differs from delegation.rootDigest (-03 L612-621)")
    # Otherwise not established, never a pass (-03 L621-625, R14).

    # Dating: earliest validating anchor of either origin (-03 L750-754).
    valid = [a for a in res.anchors if a.valid]
    earliest = _earliest(valid)
    if earliest is not None:
        res.dated_at = earliest.time

    # received / receivedAt (-03 L733-738, R9).
    received = entry.get("received")
    if not isinstance(received, bool):
        malformed("received is not a boolean (-03 L733-734)")
        received = False
    received_at = None
    if received and "receivedAt" not in entry:
        malformed("received of true without receivedAt (-03 L735-738)")
    elif not received and "receivedAt" in entry:
        malformed("receivedAt beside received of false (-03 L735-738)")
    elif received:
        try:
            received_at = parse_time(entry["receivedAt"], "receivedAt")
        except Refusal as r:
            malformed(r.reason)
    within = entry.get("anchorWithin")
    if not is_int(within) or within < 0:
        malformed("anchorWithin is not a number of seconds (-03 L738-741)")
        within = None

    retainer_all = [a for a in res.anchors if a.origin == "retainer"]
    # -03 L756-759 (R4): received of true and no anchor of origin retainer.
    if received and not retainer_all:
        malformed("received of true and no anchor of origin retainer (-03 L756-759)")
    retention = _earliest([a for a in valid if a.origin == "retainer"])  # L759-760
    issued_at = None
    try:
        issued_at = parse_time(pred.get("issuedAt"), "issuedAt")
    except Refusal:
        pass
    if received and received_at is not None and issued_at is not None:
        # -03 L763-764 (R6).
        if received_at < issued_at - cfg.skew:
            malformed("receivedAt earlier than issuedAt by more than the skew tolerance (-03 L763-764)")
    if retention is not None:
        tol = _tolerance(cfg, retention)
        if received and received_at is not None:
            # -03 L766-767 (R3, R11); 6.9 L2113-2116: a received anchor never
            # forms the upper edge.
            if received_at > retention.time + tol:
                malformed("receivedAt later than the retention time by more than the tolerance (-03 L766-767, L869-870)")
        base = received_at if received else issued_at
        if base is not None and within is not None:
            # -03 L769-771 (R7).
            if retention.time > base + within + tol:
                malformed("retention time later than anchorWithin plus the tolerance (-03 L769-771)")
    # -03 L793-795 (R5): a receipt that passes is asserted and consistent,
    # never established.
    if received and res.entry == "well-formed" and retention is not None:
        res.receipt = "asserted-consistent"
    return res


# ---------------------------------------------------------------------------
# Sets: Sections 5.9 and 5.10, checks across records
# ---------------------------------------------------------------------------

@dataclass
class SetResult:
    verdict: str = VALID
    reason: str = ""
    missing: List[dict] = field(default_factory=list)
    within_limit: List[Optional[bool]] = field(default_factory=list)


def _consumption_of(pred: dict) -> Optional[dict]:
    for ent in pred.get("evaluation", {}).get("inputs", []):
        if isinstance(ent, dict) and isinstance(ent.get("consumption"), dict):
            return ent["consumption"]
    return None


def set_checks(results: List[EnvelopeResult], envelopes: List[dict], cfg: VerifierConfig) -> SetResult:
    out = SetResult()
    out.within_limit = [r.derived.get("withinLimit") for r in results]
    # Only well-formed records enter the checks across records.
    recs = [(r, e) for r, e in zip(results, envelopes) if r.verdict == VALID]

    def fail(reason: str) -> None:
        if out.verdict == VALID:
            out.verdict, out.reason = MALFORMED, reason

    # Section 5.9 (-03 L1827-1844): group by limitDigest, order by
    # stateRevision, follow permits by operationKey.
    groups: Dict[str, list] = {}
    for r, _ in recs:
        pred = r.statement["predicate"]
        c = _consumption_of(pred)
        if c is None:
            continue
        groups.setdefault(c["limitDigest"], []).append((c["stateRevision"], pred, c))
    for _, rows in groups.items():
        rows.sort(key=lambda x: x[0])
        settled = {}  # operationKey -> True when a record of it shows none or occurred
        for _, pred, _c in rows:
            if pred["effect"]["observed"] in ("none", "occurred"):
                settled[pred["decision"]["operationKey"]] = True
        # CS7, CS17 (-03 L1831-1838): a permit that read a stateRevision older
        # than a record holding an unsettled permit, and omits it from its
        # own held, read a state that had not counted it.
        for rev_r, pred_r, c_r in rows:
            if pred_r["decision"]["reported"] not in PERMITS:
                continue
            own_key = pred_r["decision"]["operationKey"]
            held_r = {h["operationKey"] for h in c_r["held"]}
            for rev_q, _pred_q, c_q in rows:
                if not rev_r < rev_q:
                    continue
                for h in c_q["held"]:
                    k = h["operationKey"]
                    if k != own_key and k not in held_r:
                        fail(f"a permit read stateRevision {rev_r}, older than a record holding {k!r}, "
                             f"and omits it from its held (-03 L1831-1836)")
        # CS8 (-03 L1820-1825, L1835-1836): a permit leaves held only through
        # a record of that operation showing none or occurred.
        for i, (rev_a, pred_a, c_a) in enumerate(rows):
            for rev_b, _pred_b, c_b in rows[i + 1:]:
                if not rev_a < rev_b:
                    continue
                held_b = {h["operationKey"] for h in c_b["held"]}
                for h in c_a["held"]:
                    k = h["operationKey"]
                    if k not in held_b and not settled.get(k):
                        fail(f"{k!r} released from held with no record of it showing none or occurred (-03 L1820-1825, L1835-1836)")

    # Section 5.10 (-03 L1896-1929): one series = same boundary and same
    # signing key; every record here verified under the observer key.
    series: Dict[str, Dict[int, set]] = {}
    for r, _ in recs:
        seq = r.statement["predicate"].get("sequence")
        if not isinstance(seq, dict):
            continue
        ident = hashlib.sha256(r.payload_bytes).hexdigest()  # -03 L1919-1920
        series.setdefault(seq["boundary"], {}).setdefault(seq["seq"], set()).add(ident)
    for boundary, nums in sorted(series.items()):
        # SQ5 (-03 L1920-1924): one seq with two identities fails the set.
        for n, ids in sorted(nums.items()):
            if len(ids) > 1:
                fail(f"series {boundary!r} holds two different records at seq {n} (-03 L1920-1924)")
        # SQ4 (-03 L1916-1918): a number below the highest held and not held.
        top = max(nums)
        for n in range(1, top):
            if n not in nums:
                out.missing.append({"boundary": boundary, "seq": n})
    return out


# ---------------------------------------------------------------------------
# Member dispatch
# ---------------------------------------------------------------------------

@dataclass
class MemberResult:
    shape: str
    verdict: str
    reason: str
    envelope: Optional[EnvelopeResult] = None
    dating: Optional[DatingResult] = None
    set: Optional[SetResult] = None
    records: List[EnvelopeResult] = field(default_factory=list)


def judge_member(data: bytes, cfg: VerifierConfig) -> MemberResult:
    """Judge one member file from its bytes and the verifier configuration."""
    try:
        doc = parse_profile(data)
    except Refusal as r:
        return MemberResult("unknown", MALFORMED, f"{r.stage}: {r.reason}")
    if isinstance(doc, dict) and "envelope" in doc and "retention" in doc:
        env = judge_envelope(doc["envelope"], cfg)
        dating = dating_check(doc, env, cfg)
        return MemberResult("retained", env.verdict, env.describe(), envelope=env, dating=dating)
    if isinstance(doc, dict) and "records" in doc and isinstance(doc["records"], list):
        envs = doc["records"]
        results = [judge_envelope(e, cfg) for e in envs]
        bad = [(i, r) for i, r in enumerate(results) if r.verdict != VALID]
        verdict = VALID if not bad else bad[0][1].verdict
        reason = "every record passed" if not bad else f"records[{bad[0][0]}] {bad[0][1].describe()}"
        sres = set_checks(results, envs, cfg)
        return MemberResult("set", verdict, reason, set=sres, records=results)
    env = judge_envelope(doc, cfg)
    return MemberResult("envelope", env.verdict, env.describe(), envelope=env)


def fmt_time(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
