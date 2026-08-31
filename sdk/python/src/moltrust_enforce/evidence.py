"""AER — Attested-Evidence Replay: Evidenz-Items und Evidenz-Buendel.

`_core` entscheidet ueber statische Eingaben: `verdict = f(mandate, transaction)`. Reale
Compliance-Entscheidungen haengen zusaetzlich an lebenden Vorbedingungen — Widerrufsstand,
Sanktions-/Jurisdiktionsstatus, Umrechnungskurs. AER bindet diese Vorbedingungen als
signierte, zeitgebundene Evidenz ein, damit ein Dritter dasselbe Urteil ohne Neuabruf der
Quellen nachrechnet.

Dieses Modul baut und pruefet die Datenstruktur; es entscheidet nichts (`_ext_core`) und
prueft keine Signaturen (`verify`).

Format
------
Ein Evidenz-Item ist ein **DSSE-Envelope** (Dead Simple Signing Envelope), kein Eigenformat:

    {"payload": <base64(JCS(statement))>,
     "payloadType": "application/vnd.moltrust.aer-evidence+json",
     "signatures": [{"keyid": "...", "sig": "<base64>"}]}

Die Quelle signiert die DSSE-PAE ueber (payloadType, payload) — nicht den blossen Hash des
Statements. Das ist die Abweichung von der Feature-Skizze und der Grund dafuer: PAE bindet
den Typ mit ein, ein Statement kann also nicht als anderer Nachrichtentyp weiterverwendet
werden. Ein `statement` traegt genau diese Felder, weitere sind verboten:

    {"aer_version", "source_id", "query", "value", "valid_from", "valid_until", "nonce"}

Zeiten sind RFC-3339-UTC auf ganze Sekunden (`YYYY-MM-DDTHH:MM:SSZ`). Keine Bruchteile,
keine Offsets ausser `Z`, keine Schaltsekunde — was nicht exakt so dasteht, ist ungueltig.
Der Vergleich laeuft danach auf Ganzzahlen, damit zwei Maschinen nicht ueber eine
Zeitzonen-Bibliothek auseinanderlaufen.

Reihenfolge und Bindung
-----------------------
Die Items eines Buendels stehen aufsteigend nach ihrem `item_digest`. Diese Ordnung haengt
nur am Inhalt, nicht daran, in welcher Reihenfolge der Entscheider die Quellen gefragt hat;
zwei Sammlungen derselben Items ergeben denselben `bundle_commit`. Doppelte Items und zwei
Items zu derselben Abfrage sind verboten — ein Buendel, das zu einer Frage zwei Antworten
traegt, ist nicht eindeutig auswertbar und faellt fail-closed durch.

`mandate_ref` und `transaction_ref` benutzen dieselben Domain-Tags wie `_core`. Der Wert im
Buendel ist damit derselbe String wie `core["mandate_digest"]` im Verdikt-Record, und die
Bindung Buendel-zu-Entscheidung ist ohne Zusatzwissen nachrechenbar.
"""
from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from jcs import canonicalize  # RFC 8785 JCS -> bytes

from ._core import _TAG_MANDATE, _TAG_TRANSACTION, ENFORCE_VERSION

AER_VERSION = "1.0"

#: `payloadType` jedes Evidenz-Items. Teil der signierten PAE.
PAYLOAD_TYPE = "application/vnd.moltrust.aer-evidence+json"

# Domain-Separation je Digest-Rolle, wie in `_core`. Kein Tag wird zweimal vergeben.
_TAG_ITEM = b"moltrust:aer-item:v1\x00"
_TAG_QUERY = b"moltrust:aer-query:v1\x00"
_TAG_BUNDLE = b"moltrust:aer-bundle:v1\x00"

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$")

_STATEMENT_KEYS = ("aer_version", "source_id", "query", "value",
                   "valid_from", "valid_until", "nonce")
_ENVELOPE_KEYS = ("payload", "payloadType", "signatures")
_SIGNATURE_KEYS = ("keyid", "sig")
_BUNDLE_KEYS = ("aer_version", "kernel_version", "items", "mandate_ref",
                "transaction_ref", "decision_timestamp", "bundle_commit")

# Bounds. Ein Buendel darf den Verifizierer nicht per Groesse ausbremsen.
MAX_EVIDENCE_ITEMS = 64
MAX_SIGNATURES_PER_ITEM = 8
MAX_PAYLOAD_BYTES = 8192
MAX_QUERY_KEYS = 16
MAX_SOURCE_ID_LEN = 512
MAX_NONCE_LEN = 256

# Zeitfenster ausserhalb dieser Schranken sind kein plausibles Gueltigkeitsfenster.
MIN_EPOCH = 0                 # 1970-01-01T00:00:00Z
MAX_EPOCH = 4102444800        # 2100-01-01T00:00:00Z


# --------------------------------------------------------------------------- Digests

def _digest(tag: bytes, obj: Any) -> Optional[str]:
    """`sha256:<hex>` ueber JCS(obj) mit vorangestelltem Domain-Tag.

    None, wenn `obj` nicht kanonisierbar ist — der Aufrufer behandelt das fail-closed.
    """
    try:
        payload = canonicalize(obj)
    except Exception:
        return None
    return "sha256:" + hashlib.sha256(tag + payload).hexdigest()


def query_key(query: Any) -> Optional[str]:
    """Nachschlage-Schluessel einer Abfrage: Digest ueber ihre kanonische Form.

    Ein Constraint nennt die Abfrage, das Buendel liefert die Antwort; beide treffen sich
    ueber diesen Schluessel. Weil er ueber JCS laeuft, spielt die Schluesselreihenfolge im
    JSON keine Rolle.
    """
    if not _query_shape_ok(query):
        return None
    return _digest(_TAG_QUERY, query)


def item_digest(envelope: Any) -> Optional[str]:
    """Digest ueber den ganzen Envelope. Traegt Ordnung und Dedup im Buendel."""
    return _digest(_TAG_ITEM, envelope)


# ------------------------------------------------------------------------ Zeitstempel

def parse_timestamp(value: Any) -> Optional[int]:
    """`YYYY-MM-DDTHH:MM:SSZ` -> Sekunden seit Epoche. None bei allem anderen.

    Streng absichtlich: `2026-02-30T00:00:00Z` faellt durch (Kalender), `...T23:59:60Z`
    faellt durch (Schaltsekunde), `+01:00` faellt durch (Offset). Ein Fenster, das nicht
    eindeutig zu lesen ist, darf keine Entscheidung tragen.
    """
    if not isinstance(value, str):
        return None
    m = _TS_RE.match(value)
    if not m:
        return None
    year, month, day, hour, minute, second = (int(g) for g in m.groups())
    try:
        dt = datetime.datetime(year, month, day, hour, minute, second,
                               tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    epoch = int(dt.timestamp())
    if not MIN_EPOCH <= epoch <= MAX_EPOCH:
        return None
    return epoch


# ------------------------------------------------------------------------------ DSSE

def pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding — genau das wird signiert.

    `DSSEv1 <len(type)> <type> <len(payload)> <payload>`, Laengen dezimal, Trenner ein
    Leerzeichen. Die Laengenpraefixe verhindern, dass sich zwei verschiedene Paare zur
    gleichen Bytefolge zusammensetzen.
    """
    t = payload_type.encode("utf-8")
    return b"DSSEv1 " + str(len(t)).encode("ascii") + b" " + t + b" " \
        + str(len(payload)).encode("ascii") + b" " + payload


def evidence_payload_bytes(statement: Any) -> Optional[bytes]:
    """Die kanonischen Payload-Bytes eines Statements (JCS). None wenn nicht kanonisierbar."""
    try:
        return canonicalize(statement)
    except Exception:
        return None


def _b64decode(value: Any, cap: int) -> Optional[bytes]:
    """Strenges Base64 mit Groessendeckel. None bei Padding-/Alphabet-Fehlern.

    `validate=True`, damit ein Envelope nicht ueber eingestreute Fremdzeichen zwei Lesarten
    bekommt.
    """
    if not isinstance(value, str) or len(value) > cap * 2:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) > cap:
        return None
    return raw


def make_statement(source_id: str, query: Any, value: Any,
                   valid_from: str, valid_until: str, nonce: str) -> Dict[str, Any]:
    """Ein Statement in der Form, die signiert wird. Prueft nichts — dafuer ist
    `statement_problem` da."""
    return {"aer_version": AER_VERSION, "source_id": source_id, "query": query,
            "value": value, "valid_from": valid_from, "valid_until": valid_until,
            "nonce": nonce}


def make_envelope(statement: Any, signatures: List[Dict[str, str]]) -> Dict[str, Any]:
    """DSSE-Envelope um ein Statement. `signatures` kommt von der Quelle; dieses Modul
    signiert nicht — das SDK prueft Evidenz, es stellt keine aus."""
    payload = evidence_payload_bytes(statement)
    if payload is None:
        raise ValueError("statement is not canonicalizable")
    return {"payload": base64.b64encode(payload).decode("ascii"),
            "payloadType": PAYLOAD_TYPE,
            "signatures": list(signatures)}


# ----------------------------------------------------------------- Struktur-Pruefungen

def _query_shape_ok(query: Any) -> bool:
    """Eine Abfrage ist ein flaches Objekt mit `kind` und mindestens einem weiteren Feld.

    Flach absichtlich: verschachtelte Abfragen laden dazu ein, Semantik in die Struktur zu
    legen, die dann zwei Implementierungen verschieden lesen.
    """
    if not isinstance(query, dict) or not query:
        return False
    if len(query) > MAX_QUERY_KEYS:
        return False
    if not isinstance(query.get("kind"), str) or not query["kind"]:
        return False
    for k, v in query.items():
        if not isinstance(k, str) or not k:
            return False
        if not isinstance(v, (str, int)) or isinstance(v, bool):
            return False
        if isinstance(v, str) and len(v) > MAX_SOURCE_ID_LEN:
            return False
    return True


def statement_problem(statement: Any) -> Optional[str]:
    """None wenn strukturell brauchbar, sonst der Grund."""
    if not isinstance(statement, dict):
        return "statement is not an object"
    extra = sorted(set(statement) - set(_STATEMENT_KEYS))
    if extra:
        return f"statement carries unknown fields {extra}"
    missing = sorted(set(_STATEMENT_KEYS) - set(statement))
    if missing:
        return f"statement is missing fields {missing}"
    if statement["aer_version"] != AER_VERSION:
        return f"statement aer_version is not {AER_VERSION!r}"
    source_id = statement["source_id"]
    if not isinstance(source_id, str) or not source_id or len(source_id) > MAX_SOURCE_ID_LEN:
        return "statement source_id is not a bounded non-empty string"
    if not _query_shape_ok(statement["query"]):
        return "statement query is not a flat descriptor with a non-empty kind"
    value = statement["value"]
    if not isinstance(value, (bool, int, str)) or (isinstance(value, int)
                                                   and not isinstance(value, bool)
                                                   and abs(value) > 10 ** 15):
        return "statement value is not a bool, bounded integer or string"
    nonce = statement["nonce"]
    if not isinstance(nonce, str) or not nonce or len(nonce) > MAX_NONCE_LEN:
        return "statement nonce is not a bounded non-empty string"
    start = parse_timestamp(statement["valid_from"])
    end = parse_timestamp(statement["valid_until"])
    if start is None:
        return "statement valid_from is not an RFC 3339 UTC second"
    if end is None:
        return "statement valid_until is not an RFC 3339 UTC second"
    if start > end:
        return "statement window inverted (valid_from > valid_until)"
    return None


def envelope_statement(envelope: Any) -> Tuple[Optional[dict], Optional[str]]:
    """Statement aus einem Envelope lesen. Rueckgabe `(statement, problem)`.

    Kanonisierungs-Fixpunkt: die dekodierten Bytes muessen exakt JCS(statement) sein. Sonst
    liesse sich derselben Signatur ein anders serialisiertes Statement unterschieben, und
    zwei Verifizierer kaemen zu verschiedenen Werten.
    """
    if not isinstance(envelope, dict):
        return None, "evidence item is not an object"
    extra = sorted(set(envelope) - set(_ENVELOPE_KEYS))
    if extra:
        return None, f"evidence item carries unknown fields {extra}"
    if sorted(set(_ENVELOPE_KEYS) - set(envelope)):
        return None, "evidence item is not a DSSE envelope (payload/payloadType/signatures)"
    if envelope["payloadType"] != PAYLOAD_TYPE:
        return None, f"evidence item payloadType is not {PAYLOAD_TYPE!r}"
    sigs = envelope["signatures"]
    if not isinstance(sigs, list) or not sigs or len(sigs) > MAX_SIGNATURES_PER_ITEM:
        return None, "evidence item has no signatures or exceeds the signature cap"
    for s in sigs:
        if not isinstance(s, dict) or sorted(set(s) - set(_SIGNATURE_KEYS)):
            return None, "evidence item signature is not {keyid, sig}"
        if "sig" not in s or _b64decode(s["sig"], 512) is None:
            return None, "evidence item signature sig is not bounded base64"
        if "keyid" in s and (not isinstance(s["keyid"], str) or len(s["keyid"]) > MAX_SOURCE_ID_LEN):
            return None, "evidence item signature keyid is not a bounded string"
    raw = _b64decode(envelope["payload"], MAX_PAYLOAD_BYTES)
    if raw is None:
        return None, "evidence item payload is not bounded base64"
    try:
        statement = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, "evidence item payload is not UTF-8 JSON"
    problem = statement_problem(statement)
    if problem is not None:
        return None, problem
    if evidence_payload_bytes(statement) != raw:
        return None, "evidence item payload is not the canonical (JCS) form of its statement"
    return statement, None


# ---------------------------------------------------------------------------- Buendel

def build_bundle(items: List[dict], mandate: Any, transaction: Any,
                 decision_timestamp: str,
                 kernel_version: str = ENFORCE_VERSION) -> Dict[str, Any]:
    """Buendel aus fertigen Envelopes bauen: sortieren, binden, committen.

    Die Reihenfolge der uebergebenen Items ist egal — sortiert wird nach `item_digest`.
    Ein Item, dessen Digest nicht berechenbar ist, kommt ans Ende und faellt spaeter in
    `bundle_problem` durch; hier wird nichts stillschweigend verworfen.
    """
    ordered = sorted(items, key=lambda it: item_digest(it) or "￿")
    bundle: Dict[str, Any] = {
        "aer_version": AER_VERSION,
        "kernel_version": kernel_version,
        "items": ordered,
        "mandate_ref": _digest(_TAG_MANDATE, mandate),
        "transaction_ref": _digest(_TAG_TRANSACTION, transaction),
        "decision_timestamp": decision_timestamp,
    }
    bundle["bundle_commit"] = compute_bundle_commit(bundle)
    return bundle


def compute_bundle_commit(bundle: Any) -> Optional[str]:
    """`C` ueber alles ausser `bundle_commit` selbst. None wenn nicht kanonisierbar."""
    if not isinstance(bundle, dict):
        return None
    body = {k: v for k, v in bundle.items() if k != "bundle_commit"}
    return _digest(_TAG_BUNDLE, body)


def bundle_problem(bundle: Any) -> Optional[str]:
    """None wenn das Buendel strukturell traegt, sonst der Grund. Fail-closed."""
    if not isinstance(bundle, dict):
        return "bundle missing or not an object"
    extra = sorted(set(bundle) - set(_BUNDLE_KEYS))
    if extra:
        return f"bundle carries unknown fields {extra}"
    missing = sorted(set(_BUNDLE_KEYS) - set(bundle))
    if missing:
        return f"bundle is missing fields {missing}"
    if bundle["aer_version"] != AER_VERSION:
        return f"bundle aer_version is not {AER_VERSION!r}"
    if not isinstance(bundle["kernel_version"], str) or not bundle["kernel_version"]:
        return "bundle kernel_version is not a non-empty string"
    for ref in ("mandate_ref", "transaction_ref"):
        value = bundle[ref]
        if not isinstance(value, str) or not _DIGEST_RE.match(value):
            return f"bundle {ref} is not a sha256 digest string"
    if parse_timestamp(bundle["decision_timestamp"]) is None:
        return "bundle decision_timestamp is not an RFC 3339 UTC second"
    items = bundle["items"]
    if not isinstance(items, list):
        return "bundle items is not an array"
    if len(items) > MAX_EVIDENCE_ITEMS:
        return "bundle items exceeds the item cap"

    previous: Optional[str] = None
    seen_queries = set()
    for index, envelope in enumerate(items):
        statement, problem = envelope_statement(envelope)
        if problem is not None:
            return f"bundle items[{index}]: {problem}"
        digest = item_digest(envelope)
        if digest is None:
            return f"bundle items[{index}]: envelope is not canonicalizable"
        if previous is not None and digest <= previous:
            return f"bundle items[{index}] is out of canonical order or duplicated"
        previous = digest
        key = query_key(statement["query"])
        if key in seen_queries:
            return f"bundle items[{index}] answers a query that is already answered"
        seen_queries.add(key)

    claimed = bundle["bundle_commit"]
    if not isinstance(claimed, str) or not _DIGEST_RE.match(claimed):
        return "bundle bundle_commit is not a sha256 digest string"
    if compute_bundle_commit(bundle) != claimed:
        return "bundle bundle_commit does not match the bundle content"
    return None


def evidence_values(bundle: Any) -> Dict[str, dict]:
    """Abfrage-Schluessel -> Statement, fuer den Kern.

    Setzt ein per `bundle_problem` geprueftes Buendel voraus; ein unbrauchbares liefert ein
    leeres Verzeichnis, und jeder Constraint, der Evidenz braucht, faellt dann durch.
    """
    if bundle_problem(bundle) is not None:
        return {}
    values: Dict[str, dict] = {}
    for envelope in bundle["items"]:
        statement, problem = envelope_statement(envelope)
        if problem is not None:
            return {}
        key = query_key(statement["query"])
        if key is None:
            return {}
        values[key] = statement
    return values
