"""AER — unabhaengige Verifikation eines Verdikt-Records, offline.

Eingabe: Verdikt-Record, Evidenz-Buendel, Mandat, Transaktion und eine gepinnte Trust-List.
Ausgabe: PASS oder FAIL ueber vier Pruefungen. Kein Netz, keine Uhr, keine Datenbank — was
hier geprueft wird, ist Tage nach der Entscheidung auf einer fremden Maschine dasselbe.

V1 Integritaet
    `bundle_commit` passt zum Buendelinhalt, der Record nennt denselben Commit, und die
    Referenzen im Buendel binden genau das mitgelieferte Mandat und dieselbe Transaktion.

V2 Authentizitaet
    Je Item mindestens eine Ed25519-Signatur ueber die DSSE-PAE, gegen einen Schluessel, den
    die Trust-List der `source_id` des Items zuordnet. Traegt eine Signatur eine `keyid`,
    muss sie zu dem Schluessel passen, gegen den geprueft wird.

V3 Frische
    Je Item `valid_from <= T <= valid_until`, mit T aus dem Buendel. Ueber alle Items, nicht
    nur ueber die, die der Kern benutzt hat — ein Buendel mit abgelaufener Beilage ist als
    Ganzes nicht mehr das, was der Entscheider vorgelegt hat.

V4 Nachrechnung
    `f_ext(mandate, transaction, bundle)` ergibt denselben `core_digest` und dasselbe
    Verdikt wie der Record.

Alle vier gruen: das Urteil steht unabhaengig vom Betreiber. Eine faellt: der Record traegt
nicht. Das ist keine Aussage darueber, ob die Aktion erlaubt waere — nur darueber, dass diese
Antwort sie nicht belegt.

Was hier nicht geprueft wird
----------------------------
Ob eine benannte Quelle die Wahrheit gesagt hat. V2 bindet den Wert an einen Schluessel, den
die Trust-List nennt; wer diesen Schluessel besitzt, kann im Fenster einen falschen Wert
signieren. Das Vertrauen ist damit verschoben und benannt, nicht beseitigt: von „glaube dem
Betreiber" zu „glaube diesen Quellen und rechne die Arithmetik selbst nach". Ebenso offen
bleibt die Aenderung eines Faktums innerhalb eines gueltigen Fensters — dagegen hilft nur ein
kurzes Fenster oder ein erneuter Abruf unmittelbar vor der Ausfuehrung.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional, Tuple

from ._core import _ct_eq
from ._ext_core import ext_core_digest, f_ext
from .evidence import (
    MAX_SOURCE_ID_LEN,
    bundle_problem,
    compute_bundle_commit,
    envelope_statement,
    pae,
    parse_timestamp,
    evidence_payload_bytes,
)

PASS = "PASS"
FAIL = "FAIL"

_CHECKS = ("V1", "V2", "V3", "V4")


@dataclass(frozen=True)
class AerVerifyResult:
    """Ergebnis der unabhaengigen Pruefung.

    `ok` ist nur dann True, wenn alle vier Pruefungen bestanden sind. `checks` haelt je
    Pruefung Ergebnis und Begruendung, `failures` die Gruende in Reihenfolge — genug, um im
    Streitfall zu zeigen, woran es lag.
    """

    ok: bool
    checks: Dict[str, dict]
    failures: Tuple[str, ...] = ()
    recomputed_verdict: Optional[str] = None

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class _Report:
    checks: Dict[str, dict] = _dc_field(default_factory=dict)
    failures: List[str] = _dc_field(default_factory=list)

    def record(self, name: str, ok: bool, reason: str, detail: Any = None) -> bool:
        self.checks[name] = {"result": PASS if ok else FAIL, "reason": reason,
                             "detail": detail}
        if not ok:
            self.failures.append(f"{name}: {reason}")
        return ok


# ------------------------------------------------------------------------ Trust-List

def trust_list_problem(trust_list: Any) -> Optional[str]:
    """None wenn die Trust-List brauchbar ist, sonst der Grund.

    Form:

        {"trust_list_version": 1,
         "sources": {"<source_id>": {"keys": [{"algorithm": "ed25519",
                                               "public_key": "<base64 32 bytes>",
                                               "keyid": "<optional>"}]}}}

    Die Liste wird mitgebracht, nicht aufgeloest. Ein Verifizierer, der Schluessel erst
    online holen muesste, waere kein Offline-Verifizierer; welche Quelle er anerkennt, ist
    ausserdem seine Entscheidung und nicht die des Entscheiders.
    """
    if not isinstance(trust_list, dict):
        return "trust list missing or not an object"
    if trust_list.get("trust_list_version") != 1:
        return "trust list version is not 1"
    sources = trust_list.get("sources")
    if not isinstance(sources, dict) or not sources:
        return "trust list sources is not a non-empty object"
    for source_id, entry in sources.items():
        if not isinstance(source_id, str) or not source_id or len(source_id) > MAX_SOURCE_ID_LEN:
            return "trust list has a source id that is not a bounded non-empty string"
        if not isinstance(entry, dict):
            return f"trust list entry for {source_id!r} is not an object"
        keys = entry.get("keys")
        if not isinstance(keys, list) or not keys:
            return f"trust list entry for {source_id!r} has no keys"
        for key in keys:
            if not isinstance(key, dict):
                return f"trust list key for {source_id!r} is not an object"
            if key.get("algorithm") != "ed25519":
                return f"trust list key for {source_id!r} is not ed25519"
            raw = _key_bytes(key.get("public_key"))
            if raw is None:
                return f"trust list key for {source_id!r} is not 32 base64 bytes"
            if "keyid" in key and not isinstance(key["keyid"], str):
                return f"trust list key for {source_id!r} has a non-string keyid"
    return None


def _key_bytes(value: Any) -> Optional[bytes]:
    """Rohbytes eines Ed25519-Public-Keys aus Base64. None bei allem anderen."""
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == 32 else None


def _verify_ed25519(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """Ed25519-Pruefung ueber `cryptography`. Jede Exception ist ein FAIL, kein Durchlauf."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:  # pragma: no cover - Abhaengigkeit ist im pyproject deklariert
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# --------------------------------------------------------------------- Einzelpruefungen

def _check_v1(report: _Report, record: Any, bundle: Any,
              mandate: Any, transaction: Any) -> bool:
    problem = bundle_problem(bundle)
    if problem is not None:
        return report.record("V1", False, problem)
    commit = compute_bundle_commit(bundle)
    if not _ct_eq(bundle["bundle_commit"], commit or ""):
        return report.record("V1", False, "bundle_commit does not match the bundle content")
    if not isinstance(record, dict) or not isinstance(record.get("core"), dict):
        return report.record("V1", False, "verdict record has no core object")
    claimed = record["core"].get("bundle_commit")
    if not isinstance(claimed, str) or not _ct_eq(claimed, commit):
        return report.record("V1", False,
                             "verdict record does not reference this bundle commit",
                             {"record": claimed, "bundle": commit})
    # Die Refs im Buendel gegen die mitgelieferten Eingaben — sonst prueft V4 zwar sauber
    # nach, aber ueber ein Mandat, das mit diesem Buendel nie zusammengehoert hat.
    fresh = f_ext(mandate, transaction, bundle)
    for ref, digest_key in (("mandate_ref", "mandate_digest"),
                            ("transaction_ref", "transaction_digest")):
        expected = fresh["core"].get(digest_key)
        if not isinstance(expected, str) or not _ct_eq(bundle[ref], expected):
            return report.record("V1", False, f"bundle {ref} does not bind the supplied input",
                                 {"bundle": bundle[ref], "input": expected})
    return report.record("V1", True, "bundle commit and input binding hold", commit)


def _check_v2(report: _Report, bundle: Any, trust_list: Any) -> bool:
    problem = trust_list_problem(trust_list)
    if problem is not None:
        return report.record("V2", False, problem)
    if bundle_problem(bundle) is not None:
        return report.record("V2", False, "bundle is not structurally valid")
    sources = trust_list["sources"]
    signed: List[str] = []
    for index, envelope in enumerate(bundle["items"]):
        statement, item_problem = envelope_statement(envelope)
        if item_problem is not None:
            return report.record("V2", False, f"items[{index}]: {item_problem}")
        source_id = statement["source_id"]
        entry = sources.get(source_id)
        if entry is None:
            return report.record("V2", False,
                                 f"items[{index}]: source {source_id!r} is not in the trust list")
        message = pae(envelope["payloadType"], evidence_payload_bytes(statement))
        if not _item_signature_holds(envelope, entry["keys"], message):
            return report.record("V2", False,
                                 f"items[{index}]: no signature verifies against a trusted key "
                                 f"for {source_id!r}")
        signed.append(source_id)
    return report.record("V2", True, "every item carries a signature from a trusted source",
                         signed)


def _item_signature_holds(envelope: dict, keys: List[dict], message: bytes) -> bool:
    """Mindestens eine Signatur des Items gegen mindestens einen Schluessel der Quelle.

    Eine `keyid` grenzt ein, sie ersetzt die Pruefung nicht: passt sie nicht, wird der
    Schluessel uebersprungen; passt sie, muss die Signatur trotzdem halten.
    """
    for signature in envelope["signatures"]:
        raw_sig = _sig_bytes(signature.get("sig"))
        if raw_sig is None:
            continue
        keyid = signature.get("keyid")
        for key in keys:
            if isinstance(keyid, str) and "keyid" in key and key["keyid"] != keyid:
                continue
            raw_key = _key_bytes(key.get("public_key"))
            if raw_key is None:
                continue
            if _verify_ed25519(raw_key, raw_sig, message):
                return True
    return False


def _sig_bytes(value: Any) -> Optional[bytes]:
    if not isinstance(value, str) or len(value) > 512:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _check_v3(report: _Report, bundle: Any) -> bool:
    if bundle_problem(bundle) is not None:
        return report.record("V3", False, "bundle is not structurally valid")
    decision = parse_timestamp(bundle["decision_timestamp"])
    if decision is None:
        return report.record("V3", False, "bundle decision_timestamp is not readable")
    for index, envelope in enumerate(bundle["items"]):
        statement, item_problem = envelope_statement(envelope)
        if item_problem is not None:
            return report.record("V3", False, f"items[{index}]: {item_problem}")
        start = parse_timestamp(statement["valid_from"])
        end = parse_timestamp(statement["valid_until"])
        if start is None or end is None:
            return report.record("V3", False, f"items[{index}]: window is not readable")
        if not start <= decision <= end:
            return report.record("V3", False,
                                 f"items[{index}]: window does not cover the decision timestamp",
                                 {"valid_from": statement["valid_from"],
                                  "valid_until": statement["valid_until"],
                                  "decision_timestamp": bundle["decision_timestamp"]})
    return report.record("V3", True, "every item window covers the decision timestamp",
                         bundle["decision_timestamp"])


def _check_v4(report: _Report, record: Any, bundle: Any,
              mandate: Any, transaction: Any) -> Tuple[bool, Optional[str]]:
    if not isinstance(record, dict) or not isinstance(record.get("core"), dict):
        return report.record("V4", False, "verdict record has no core object"), None
    prev = record["core"].get("prev_core_digest")
    fresh = f_ext(mandate, transaction, bundle,
                  prev_core_digest=prev if isinstance(prev, str) else None)
    claimed = record.get("core_digest")
    if not isinstance(claimed, str):
        return report.record("V4", False, "verdict record has no core_digest"), None
    # Der Record muss zu sich selbst passen, bevor er mit der Nachrechnung verglichen wird.
    # Sonst zeigt ein Record einen Core mit PERMIT und einen Digest ueber einen anderen —
    # wer den Core liest statt ihn nachzurechnen, liest dann eine Luege, die alle vier
    # Pruefungen ueberlebt haette.
    if not _ct_eq(ext_core_digest(record["core"]) or "", claimed):
        return report.record("V4", False, "core_digest does not digest the core in the record",
                             {"record": claimed,
                              "core": ext_core_digest(record["core"])}), fresh["verdict"]
    if not isinstance(fresh["core_digest"], str):
        return report.record("V4", False, "the recomputed core is not digestible"), None
    if not _ct_eq(fresh["core_digest"], claimed):
        return report.record("V4", False, "recomputed core digest differs from the record",
                             {"record": claimed, "recomputed": fresh["core_digest"]}), \
            fresh["verdict"]
    if record.get("verdict") is not None and not _ct_eq(record["verdict"], fresh["verdict"]):
        return report.record("V4", False, "recomputed verdict differs from the record",
                             {"record": record.get("verdict"),
                              "recomputed": fresh["verdict"]}), fresh["verdict"]
    return report.record("V4", True, f"recomputed {fresh['verdict']} from the same inputs",
                         fresh["core_digest"]), fresh["verdict"]


# ------------------------------------------------------------------------ oeffentlich

def verify_record(record: Any, bundle: Any, mandate: Any, transaction: Any,
                  trust_list: Any) -> AerVerifyResult:
    """V1 bis V4 gegen einen Verdikt-Record. Alle vier laufen, auch wenn eine schon faellt —
    ein Bericht, der nach der ersten Abweichung abbricht, verschweigt die anderen."""
    report = _Report()
    _check_v1(report, record, bundle, mandate, transaction)
    _check_v2(report, bundle, trust_list)
    _check_v3(report, bundle)
    _ok4, verdict = _check_v4(report, record, bundle, mandate, transaction)
    ok = all(report.checks.get(name, {}).get("result") == PASS for name in _CHECKS)
    return AerVerifyResult(ok=ok, checks=report.checks, failures=tuple(report.failures),
                           recomputed_verdict=verdict)
