"""Enforce-Mode Kern — `constraint_mode = "enforce"` (ADR-D3-v3 Komponente 3, Schritt 1).

Rein und deterministisch: keine DB, kein Netz, keine Uhr, kein Prozesszustand.
`enforce_check(mandate, transaction)` bekommt beide Eingaben aus dem Request und gibt
Verdikt + nachrechenbaren Record zurueck. Wer mandate + transaction hat, rechnet denselben
`core_digest` nach — ohne Zugriff auf diesen Server.

Abgrenzung zum AAE-Evaluator (`app/enforcement/evaluator.py`): der ist DB-gebunden und traegt
Postgres-Zaehler (rate_limit, single_use) im signierten Record. Genau das ist hier verboten.
Die beiden Maschinen teilen keinen Code und keinen Pfad: `none`/`inherit`/`restrict` bleiben
beim Evaluator, `enforce` laeuft ausschliesslich hier.

Entscheidungsregeln
-------------------
- **deny-by-default.** PERMIT nur, wenn ein Grant per action_binding exakt trifft, alle seine
  Constraints halten und seine disposition `allow` ist. Alles andere ist DENY.
- **Typform vor Bindung.** Jeder Grant deklariert in `type_fields`, woraus die Aktion besteht.
  Die Aktion MUSS ein Objekt sein und genau diese Schluesselmenge tragen — kein fehlendes und
  kein zusaetzliches Feld. Ein Betrag oder ein Empfaenger gehoert damit nicht in die Aktion,
  sondern als Geschwister in die Transaktion, wo Constraints ihn pruefen. Ein String oder ein
  Array als Aktion erfuellt keine Typform und ist DENY.
- **PENDING nur bei explizitem `disposition="hold"`.** Eine unadressierte Aktion wird NIE
  PENDING — sonst waere „nicht geregelt" ein Weg an der Entscheidung vorbei.
- **forbid hat Vorrang.** Trifft irgendein passender Grant mit `disposition="forbid"`, ist das
  Ergebnis DENY, auch wenn ein anderer Grant erlauben wuerde.
- **Fail-closed.** Fehlendes/strukturell ungueltiges Mandat, fehlendes Feld, unbekannter
  Constraint-Typ, unparsebarer Wert: DENY. Kein Mandat, kein PERMIT.

verdictCore-Determinismus
-------------------------
Im `core` steht nichts, das nicht aus `mandate` + `transaction` (+ dem vom Aufrufer
uebergebenen `prev_core_digest`) rekonstruierbar waere. Insbesondere keine Serverzeit, keine
Zufallswerte, keine DB-Zaehler, kein kumuliertes Budget. Zweifache Auswertung derselben
Eingaben liefert byte-identische Digests.

Im Core steht ausserdem kein Freitext: weder das `reason` des Verdikts noch das je Praedikat.
Beide bleiben in der Antwort, damit ein Mensch den Grund liest — aber sie sind nicht Teil des
Werts, den zwei Implementierungen byte-identisch treffen muessen.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Optional, Tuple

from jcs import canonicalize  # RFC 8785 JCS -> bytes

PERMIT = "PERMIT"
DENY = "DENY"
PENDING = "PENDING"

ENFORCE_VERSION = "3.0"

# Domain-Separation auf Byte-Ebene, je Digest-Rolle eigener Tag (kein Cross-Protocol-Reuse).
_TAG_ACTION = b"aae:enforce-action:v1\x00"
_TAG_MANDATE = b"aae:enforce-mandate:v1\x00"
_TAG_TRANSACTION = b"aae:enforce-transaction:v1\x00"
_TAG_CORE = b"aae:enforce-core:v1\x00"

_DISPOSITIONS = ("allow", "hold", "forbid")
_CONSTRAINT_TYPES = ("exact", "enum", "range")

# Das eine Feld, das jede Typform tragen muss: ohne Verb gibt es keine Aktion, nur Argumente.
TYPE_FIELD_VERB = "verb"

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

# Bounds. Grenzen sind hart, damit ein Mandat den Check nicht per Groesse aushebelt.
MAX_GRANTS = 256
MAX_CONSTRAINTS_PER_GRANT = 64
MAX_ENUM_MEMBERS = 512
MAX_TYPE_FIELDS = 32
MAX_FIELD_DEPTH = 8
# Ganzzahl-Schranke wie im AAE-Evaluator (integer-minor-units), gegen Overflow/Float-Drift.
MAX_ABS_INT = 10 ** 15

PASS = "PASS"
FAIL = "FAIL"


# --------------------------------------------------------------------------- helpers

def _digest(tag: bytes, obj: Any) -> Optional[str]:
    """`sha256:<hex>` ueber JCS(obj) mit vorangestelltem Domain-Tag. None wenn nicht
    kanonisierbar (nicht-JSON-Wert) — der Aufrufer behandelt das fail-closed."""
    try:
        payload = canonicalize(obj)
    except Exception:
        return None
    return "sha256:" + hashlib.sha256(tag + payload).hexdigest()


def _ct_eq(a: Any, b: Any) -> bool:
    """Konstant-zeitiger Stringvergleich. Nur str==str; alles andere ist False.

    `compare_digest` verdeckt den Inhalt, nicht die Laenge — das ist fuer Adressen und
    Digests die uebliche und hier ausreichende Eigenschaft.
    """
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _is_int(x: Any) -> bool:
    # bool ist in Python ein int-Subtyp — hier ausdruecklich kein gueltiger Zahlwert.
    return isinstance(x, int) and not isinstance(x, bool) and abs(x) <= MAX_ABS_INT


def _resolve_field(transaction: dict, path: Any) -> Tuple[bool, Any]:
    """Punktpfad in die transaction aufloesen. Nur dict-Traversierung, begrenzte Tiefe.

    Rueckgabe `(found, value)`. Nicht gefunden ist kein Fehler nach oben, sondern ein
    FAIL-Praedikat im Trace.
    """
    if not isinstance(path, str) or not path:
        return False, None
    segments = path.split(".")
    if len(segments) > MAX_FIELD_DEPTH or any(s == "" for s in segments):
        return False, None
    cur: Any = transaction
    for seg in segments:
        if not isinstance(cur, dict) or seg not in cur:
            return False, None
        cur = cur[seg]
    return True, cur


def _pred(predicate: str, field: Any, result: str, reason: str,
          value: Any = None, bound: Any = None) -> dict:
    """Ein Eintrag der Praedikat-Spur π: welches Praedikat, welcher Wert, welche Grenze.

    Der Eintrag traegt `reason` fuer den Leser. In den Core geht er ohne — siehe
    `_trace_for_core()`.
    """
    return {"predicate": predicate, "field": field, "value": value,
            "bound": bound, "result": result, "reason": reason}


# Die Felder, die in den Digest gehen. `reason` ist nicht dabei: ein Freitext, ueber dessen
# Wortlaut zwei Implementierungen sich nicht einigen muessen, hat nichts in einem Wert zu
# suchen, den sie byte-identisch treffen muessen (AAE -02 §2.5.2/§2.5.3).
_DIGESTED_PRED_FIELDS = ("predicate", "field", "value", "bound", "result")


def _trace_for_core(trace: list) -> list:
    """Die Spur, wie sie in den Core geht: nur die reproduzierbaren Felder je Eintrag."""
    return [{k: e[k] for k in _DIGESTED_PRED_FIELDS} for e in trace]


# --------------------------------------------------------------- Constraint-Praedikate

def _eval_exact(c: dict, transaction: dict) -> dict:
    """Exakte Gleichheit. Kein Praefix, kein Suffix, keine Normalisierung, kein
    Case-Folding — eine Vanity-Adresse mit gleichem Anfang faellt durch."""
    field = c.get("field")
    expected = c.get("value")
    found, actual = _resolve_field(transaction, field)
    if not isinstance(expected, str):
        return _pred("exact", field, FAIL, "constraint value is not a string", None, expected)
    if not found:
        return _pred("exact", field, FAIL, "field not present in transaction", None, expected)
    if not isinstance(actual, str):
        return _pred("exact", field, FAIL, "transaction value is not a string", actual, expected)
    if _ct_eq(actual, expected):
        return _pred("exact", field, PASS, "exact match", actual, expected)
    return _pred("exact", field, FAIL, "value does not match exactly", actual, expected)


def _eval_enum(c: dict, transaction: dict) -> dict:
    """Mitgliedschaft in einer Aufzaehlung; jedes Element exakt verglichen."""
    field = c.get("field")
    members = c.get("values")
    found, actual = _resolve_field(transaction, field)
    if not isinstance(members, list) or not members:
        return _pred("enum", field, FAIL, "constraint values is not a non-empty array", None, members)
    if len(members) > MAX_ENUM_MEMBERS:
        return _pred("enum", field, FAIL, "constraint values exceeds member cap", None, len(members))
    if not found:
        return _pred("enum", field, FAIL, "field not present in transaction", None, members)
    if not isinstance(actual, str):
        return _pred("enum", field, FAIL, "transaction value is not a string", actual, members)
    # Ohne Kurzschluss ueber alle Elemente, damit die Trefferposition nichts verraet.
    hits = 0
    for m in members:
        if _ct_eq(actual, m):
            hits += 1
    if hits:
        return _pred("enum", field, PASS, "value in enumeration", actual, members)
    return _pred("enum", field, FAIL, "value not in enumeration", actual, members)


def _eval_range(c: dict, transaction: dict) -> dict:
    """Geschlossenes Ganzzahl-Intervall lo <= arg <= hi. Beide Grenzen sind erlaubt.

    Nur Ganzzahlen: Floats brechen die Nachrechenbarkeit (Rundung/Repraesentation), und
    bool ist kein Zahlwert.
    """
    field = c.get("field")
    lo, hi = c.get("lo"), c.get("hi")
    bound = {"lo": lo, "hi": hi}
    found, actual = _resolve_field(transaction, field)
    if not _is_int(lo) or not _is_int(hi):
        return _pred("range", field, FAIL, "constraint bounds are not bounded integers", None, bound)
    if lo > hi:
        return _pred("range", field, FAIL, "constraint bounds inverted (lo > hi)", None, bound)
    if not found:
        return _pred("range", field, FAIL, "field not present in transaction", None, bound)
    if not _is_int(actual):
        return _pred("range", field, FAIL, "transaction value is not a bounded integer", actual, bound)
    if lo <= actual <= hi:
        return _pred("range", field, PASS, "within range", actual, bound)
    return _pred("range", field, FAIL, "outside range", actual, bound)


def _eval_constraint(c: Any, transaction: dict) -> dict:
    if not isinstance(c, dict):
        return _pred("unknown", None, FAIL, "constraint is not an object")
    ctype = c.get("type")
    if ctype not in _CONSTRAINT_TYPES:
        # Unbekannter Typ ist nie ignorierbar: was der Kern nicht auswerten kann, erlaubt er nicht.
        return _pred(str(ctype), c.get("field"), FAIL, "unknown constraint type -> deny by default")
    return {"exact": _eval_exact, "enum": _eval_enum, "range": _eval_range}[ctype](c, transaction)


# ----------------------------------------------------------------- Struktur-Validierung

def _type_fields_ok(tf: Any) -> bool:
    """Die deklarierte Typform eines Grants: nicht-leere Liste eindeutiger, nicht-leerer
    Feldnamen, die `verb` enthaelt.

    Doppelte Namen sind ungueltig, nicht bloss redundant: der Abgleich unten vergleicht
    Mengen, und eine Liste mit Dublette behauptete eine Feldzahl, die sie nicht hat.
    """
    if not isinstance(tf, list) or not tf or len(tf) > MAX_TYPE_FIELDS:
        return False
    for name in tf:
        if not isinstance(name, str) or not name:
            return False
    if len(set(tf)) != len(tf):
        return False
    return TYPE_FIELD_VERB in tf


def _grant_shape_ok(g: Any) -> bool:
    if not isinstance(g, dict):
        return False
    if not isinstance(g.get("action_binding"), str) or not _DIGEST_RE.match(g["action_binding"]):
        return False
    if g.get("disposition") not in _DISPOSITIONS:
        return False
    if not _type_fields_ok(g.get("type_fields")):
        return False
    cs = g.get("constraints")
    if not isinstance(cs, list) or len(cs) > MAX_CONSTRAINTS_PER_GRANT:
        return False
    return True


def _type_shape_problem(action: Any, type_fields: list) -> Optional[str]:
    """None wenn die Aktion genau die deklarierten Typ-Felder traegt, sonst der Grund.

    Die Meldung nennt die Felder beim Namen, damit ein Aufrufer im Trace sieht, ob er ein
    Instanzargument in die Aktion gelegt oder ein Typ-Feld vergessen hat.
    """
    if not isinstance(action, dict):
        return "action is not an object"
    have, want = set(action.keys()), set(type_fields)
    missing, extra = sorted(want - have), sorted(have - want)
    if missing and extra:
        return (f"action fields do not match type_fields "
                f"(missing {missing}, outside the type {extra})")
    if missing:
        return f"action is missing type_fields {missing}"
    if extra:
        return f"action carries fields outside type_fields {extra}"
    return None


def _mandate_problem(mandate: Any) -> Optional[str]:
    """None wenn strukturell brauchbar, sonst der Grund. Fail-closed: im Zweifel ein Grund."""
    if not isinstance(mandate, dict):
        return "mandate missing or not an object"
    grants = mandate.get("grants")
    if not isinstance(grants, list) or not grants:
        return "mandate.grants missing or empty"
    if len(grants) > MAX_GRANTS:
        return "mandate.grants exceeds cap"
    for i, g in enumerate(grants):
        if not _grant_shape_ok(g):
            return (f"mandate.grants[{i}] malformed "
                    f"(action_binding/disposition/type_fields/constraints)")
    return None


# ------------------------------------------------- Grant attenuation (AAE -02 §5 step 9)
#
# A delegated mandate MUST be no broader than its parent (-02 §5 step 9, "Grant
# attenuation"): every child grant is covered by a parent grant with an equal
# action_binding whose constraints the child's imply, and a disposition never moves down
# allow <= hold <= forbid. Two rules here go past the literal coverage test, both from the
# same step's general clause "the child MUST be no broader than its parent":
#
# - A parent forbid outranks every grant for its action (§2.2.3 step 5). A child that keeps
#   an allow or hold for that action and drops the forbid would permit what the parent
#   denies, although each of its grants is "covered". Rejected.
# - The parent evaluates its grants in document order and stops at the first one whose
#   constraints hold. A child allow covered by a later parent allow is broader if an
#   earlier parent hold for the same action can hold on the same transaction. Rejected
#   unless the two grants are provably disjoint.
#
# The comparison is set-based over what a constraint admits, so `exact` inside an `enum`
# counts as narrower. A constraint that can never hold (unknown type, malformed bound, bad
# path) admits nothing; a grant carrying one never applies and so never widens.

_DISPOSITION_RANK = {"allow": 0, "hold": 1, "forbid": 2}

# Same ceiling as the recursion limit of §5 step 9.
MAX_ANCESTORS = 8


def _path_ok(path: Any) -> bool:
    if not isinstance(path, str) or not path:
        return False
    segments = path.split(".")
    return len(segments) <= MAX_FIELD_DEPTH and all(s != "" for s in segments)


def _admitted(c: Any) -> Optional[tuple]:
    """What one constraint admits: ("str", frozenset) or ("int", lo, hi).

    None when the constraint can never hold — the cases the predicates above always FAIL.
    """
    if not isinstance(c, dict) or not _path_ok(c.get("field")):
        return None
    ctype = c.get("type")
    if ctype == "exact":
        v = c.get("value")
        return ("str", frozenset([v])) if isinstance(v, str) else None
    if ctype == "enum":
        vs = c.get("values")
        if not isinstance(vs, list) or not vs or len(vs) > MAX_ENUM_MEMBERS:
            return None
        members = frozenset(v for v in vs if isinstance(v, str))
        return ("str", members) if members else None
    if ctype == "range":
        lo, hi = c.get("lo"), c.get("hi")
        if not _is_int(lo) or not _is_int(hi) or lo > hi:
            return None
        return ("int", lo, hi)
    return None


def _admits_subset(a: Optional[tuple], b: Optional[tuple]) -> bool:
    """Everything `a` admits, `b` admits too."""
    if a is None:
        return True
    if b is None or a[0] != b[0]:
        return False
    if a[0] == "str":
        return a[1] <= b[1]
    return b[1] <= a[1] and a[2] <= b[2]


def _admits_disjoint(a: Optional[tuple], b: Optional[tuple]) -> bool:
    """No value satisfies both."""
    if a is None or b is None or a[0] != b[0]:
        return True
    if a[0] == "str":
        return not (a[1] & b[1])
    return a[2] < b[1] or b[2] < a[1]


def _grant_dead(g: dict) -> bool:
    """A grant with a constraint that can never hold never applies."""
    return any(_admitted(c) is None for c in g["constraints"])


def _unimplied(child: dict, parent: dict) -> Optional[str]:
    """None when the child's constraints imply every constraint of the parent grant."""
    for c in parent["constraints"]:
        want = _admitted(c)
        field = c.get("field") if isinstance(c, dict) else None
        if not any(isinstance(cc, dict) and cc.get("field") == field
                   and _admits_subset(_admitted(cc), want)
                   for cc in child["constraints"]):
            ctype = c.get("type") if isinstance(c, dict) else None
            return f"parent constraint {ctype} on {field!r} is absent or not narrowed"
    return None


def _grants_disjoint(a: dict, b: dict) -> bool:
    """Provably no transaction satisfies all constraints of both grants."""
    if _grant_dead(a) or _grant_dead(b):
        return True
    return any(isinstance(ca, dict) and isinstance(cb, dict)
               and ca.get("field") == cb.get("field")
               and _admits_disjoint(_admitted(ca), _admitted(cb))
               for ca in a["constraints"] for cb in b["constraints"])


def grant_attenuation_problem(child: Any, parent: Any) -> Optional[str]:
    """None when every grant of `child` stays inside `parent`, else the reason.

    Both arguments are mandates carrying grants. Pure: no clock, no state.
    """
    for who, m in (("child", child), ("parent", parent)):
        problem = _mandate_problem(m)
        if problem is not None:
            return f"{who} {problem}"
    pgrants, cgrants = parent["grants"], child["grants"]
    forbidden = [q["action_binding"] for q in pgrants if q["disposition"] == "forbid"]

    for i, g in enumerate(cgrants):
        same = [j for j, q in enumerate(pgrants)
                if _ct_eq(q["action_binding"], g["action_binding"])]
        if not same:
            return f"grant[{i}] binds an action no parent grant binds"
        if g["disposition"] == "forbid" or _grant_dead(g):
            continue
        if any(_ct_eq(f, g["action_binding"]) for f in forbidden):
            return f"grant[{i}] disposition={g['disposition']} for an action the parent forbids"

        # The reason names the closest candidate: one whose disposition would have fitted
        # beats one that failed on disposition alone.
        on_disposition, on_constraints = None, None
        for j in same:
            q = pgrants[j]
            if _DISPOSITION_RANK[g["disposition"]] < _DISPOSITION_RANK[q["disposition"]]:
                on_disposition = on_disposition or (
                    f"grant[{i}] disposition {g['disposition']} is broader than parent "
                    f"grant[{j}] {q['disposition']}")
                continue
            why = _unimplied(g, q)
            if why is None and g["disposition"] == "allow":
                blocker = next((k for k in same if k < j
                                and pgrants[k]["disposition"] == "hold"
                                and not _grants_disjoint(g, pgrants[k])), None)
                if blocker is not None:
                    why = f"parent grant[{blocker}] holds first on the same transactions"
            if why is None:
                break
            on_constraints = on_constraints or f"grant[{i}] vs parent grant[{j}]: {why}"
        else:
            return on_constraints or on_disposition
    return None


def _attenuation_trace(mandate: dict, ancestors: Any, trace: list) -> Optional[str]:
    """One `grant_attenuation` predicate per hop, root first. Returns the DENY reason or None.

    `ancestors` lists the parent mandates from the root down to the direct parent of
    `mandate`. value and bound are the mandate digests of child and parent, so the core
    binds the chain it was decided over.
    """
    if not isinstance(ancestors, list) or len(ancestors) > MAX_ANCESTORS:
        reason = f"ancestors must be an array of at most {MAX_ANCESTORS} mandates"
        trace.append(_pred("grant_attenuation", None, FAIL, reason))
        return reason
    chain = list(ancestors) + [mandate]
    for k in range(len(ancestors)):
        parent, child = chain[k], chain[k + 1]
        problem = grant_attenuation_problem(child, parent)
        field = f"ancestors[{k}]"
        value, bound = _digest(_TAG_MANDATE, child), _digest(_TAG_MANDATE, parent)
        if problem is not None:
            reason = f"grant attenuation, hop {k}: {problem}"
            trace.append(_pred("grant_attenuation", field, FAIL, reason, value, bound))
            return reason
        trace.append(_pred("grant_attenuation", field, PASS,
                           f"hop {k}: every child grant stays inside the parent", value, bound))
    return None


# ------------------------------------------------------------------------ oeffentlich

def action_digest(action: Any) -> Optional[str]:
    """Der exact-action-digest, an den ein Grant bindet.

    Quelle ist ausschliesslich `transaction["action"]` — oeffentlich rekonstruierbar, damit
    ein Dritter die Bindung nachrechnen kann, ohne den Rest der Transaktion zu kennen.
    """
    return _digest(_TAG_ACTION, action)


def core_digest(core: dict) -> Optional[str]:
    """Digest ueber den verdictCore. Ein Dritter ruft das mit dem Core aus dem Record auf."""
    return _digest(_TAG_CORE, core)


def _evaluate_grants(mandate: dict, transaction: dict, act_digest: str,
                     trace: list) -> Tuple[str, str, Optional[int]]:
    """§2.2.3 steps 2-7 over a structurally valid mandate. Appends to `trace`."""
    grant_index: Optional[int] = None
    grants = mandate["grants"]
    action = transaction.get("action")

    # ★ Typform vor Bindung. Ein Grant kommt erst in die Bindungspruefung, wenn die Aktion
    # genau seine `type_fields` traegt. Ohne diesen Schritt entschiede allein der Digest,
    # und dann waere jedes Feld typbestimmend — auch ein versehentlich hineingerutschter
    # Betrag. Der Digest faellt in dem Fall zwar ebenfalls auseinander, sagt aber nur
    # „unadressiert" statt zu benennen, was nicht stimmt.
    typed, first_problem = [], None
    for i, g in enumerate(grants):
        problem = _type_shape_problem(action, g["type_fields"])
        if problem is None:
            typed.append(i)
        elif first_problem is None:
            first_problem = (i, problem)

    if not typed:
        # Keine deklarierte Typform passt auf diese Aktion. Der Grund kommt vom ersten
        # Grant in Dokumentreihenfolge — deterministisch, und er benennt das Feld.
        i, problem = first_problem
        verdict, reason = DENY, f"grant[{i}]: {problem}"
        trace.append(_pred("type_fields", "action", FAIL, reason,
                           sorted(action.keys()) if isinstance(action, dict) else None,
                           list(grants[i]["type_fields"])))
    else:
        trace.append(_pred("type_fields", "action", PASS,
                           f"action carries exactly the type_fields of grant(s) {typed}",
                           sorted(action.keys()), list(grants[typed[0]]["type_fields"])))
        matched = [i for i in typed if _ct_eq(grants[i]["action_binding"], act_digest)]

        if not matched:
            # deny-by-default. Ausdruecklich NICHT PENDING: eine ungeregelte Aktion ist
            # keine Vorlage zur Freigabe, sonst waere „nicht geregelt" der Umgehungsweg.
            verdict, reason = DENY, "unaddressed action: no grant binds this action digest"
            trace.append(_pred("action_binding", "action", FAIL, reason, act_digest, None))
        else:
            trace.append(_pred("action_binding", "action", PASS,
                               f"bound by grant(s) {matched}", act_digest, act_digest))
            forbidden = [i for i in matched if grants[i]["disposition"] == "forbid"]
            if forbidden:
                # forbid schlaegt jede Erlaubnis, und es steht sichtbar im Record.
                grant_index = forbidden[0]
                verdict = DENY
                reason = f"grant[{grant_index}] disposition=forbid"
                trace.append(_pred("disposition", None, FAIL, reason, "forbid", None))
            else:
                verdict, reason = DENY, "no matching grant satisfied its constraints"
                for i in matched:
                    g = grants[i]
                    preds = [_eval_constraint(c, transaction) for c in g["constraints"]]
                    trace.extend(preds)
                    if all(p["result"] == PASS for p in preds):
                        grant_index = i
                        disp = g["disposition"]
                        verdict = PERMIT if disp == "allow" else PENDING
                        reason = (f"grant[{i}] matched, all constraints hold, "
                                  f"disposition={disp}")
                        trace.append(_pred("disposition", None, PASS, reason, disp, None))
                        break
    return verdict, reason, grant_index


def enforce_check(mandate: Any, transaction: Any,
                  prev_core_digest: Optional[str] = None,
                  ancestors: Any = None) -> dict:
    """Wertet `transaction` gegen `mandate` aus. Rein, ohne Seiteneffekt.

    `prev_core_digest` verkettet diesen Verdikt-Record mit dem vorherigen; der Aufrufer
    haelt die Kette. None ist der Kettenanfang.

    `ancestors` (optional) lists the parent mandates of a delegated `mandate`, root first.
    Each hop is checked for grant attenuation (AAE -02 §5 step 9) before any grant is
    evaluated, and recorded as a `grant_attenuation` predicate. None or [] adds nothing to
    the trace, so the core of an undelegated mandate is unchanged. Signatures and the
    depth rules of the chain stay with the acceptance gate.

    Rueckgabe: `{verdict, reason, grant_index, trace, core, core_digest}`.
    `verdict` ist PERMIT, DENY oder PENDING.
    """
    trace: list = []
    grant_index: Optional[int] = None

    problem = _mandate_problem(mandate)
    tx_ok = isinstance(transaction, dict)
    act_digest = action_digest(transaction.get("action")) if tx_ok else None

    if problem is not None:
        # ★ Kein gueltiges Mandat im Request -> DENY. Nie ein stiller Durchlauf.
        verdict, reason = DENY, problem
        trace.append(_pred("mandate_present", None, FAIL, problem))
    elif not tx_ok:
        verdict, reason = DENY, "transaction missing or not an object"
        trace.append(_pred("transaction_present", None, FAIL, reason))
    elif act_digest is None:
        verdict, reason = DENY, "transaction.action missing or not canonicalizable"
        trace.append(_pred("action_binding", "action", FAIL, reason))
    else:
        trace.append(_pred("mandate_present", None, PASS, "mandate structurally valid"))
        chain_problem = (_attenuation_trace(mandate, ancestors, trace)
                         if ancestors not in (None, []) else None)
        if chain_problem is not None:
            verdict, reason = DENY, chain_problem
        else:
            verdict, reason, grant_index = _evaluate_grants(mandate, transaction, act_digest,
                                                            trace)

    core = {
        "enforce_version": ENFORCE_VERSION,
        "mandate_digest": _digest(_TAG_MANDATE, mandate),
        "transaction_digest": _digest(_TAG_TRANSACTION, transaction),
        "action_digest": act_digest,
        "verdict": verdict,
        "grant_index": grant_index,
        "trace": _trace_for_core(trace),
        "prev_core_digest": prev_core_digest if isinstance(prev_core_digest, str) else None,
    }
    return {"verdict": verdict, "reason": reason, "grant_index": grant_index,
            "trace": trace, "core": core, "core_digest": core_digest(core)}


def recompute(mandate: Any, transaction: Any, record: dict, ancestors: Any = None) -> bool:
    """Dritt-Nachrechnung: liefert `mandate` + `transaction` denselben Core wie im Record?

    Vergleicht den Digest, nicht die Objektform — dieselbe Pruefung, die ein externer
    Verifizierer ohne Serverzugriff anstellt.
    """
    if not isinstance(record, dict):
        return False
    claimed = record.get("core_digest")
    if not isinstance(claimed, str):
        return False
    prev = (record.get("core") or {}).get("prev_core_digest") if isinstance(record.get("core"), dict) else None
    fresh = enforce_check(mandate, transaction, prev_core_digest=prev, ancestors=ancestors)
    if not isinstance(fresh["core_digest"], str):
        return False
    return _ct_eq(fresh["core_digest"], claimed)
