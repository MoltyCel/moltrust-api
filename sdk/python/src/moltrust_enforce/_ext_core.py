"""AER — erweiterter Kern `f_ext`: Urteil ueber Mandat, Transaktion und Evidenz-Buendel.

`_core.enforce_check` entscheidet ueber statische Eingaben. `f_ext` nimmt zusaetzlich ein
Evidenz-Buendel und wertet Constraints aus, die auf lebende Vorbedingungen zeigen — Widerruf,
Sanktions-/Jurisdiktionsstatus, Umrechnungskurs. Die Werte kommen ausschliesslich aus dem
Buendel; `f_ext` fragt keine Quelle, liest keine Uhr und oeffnet keine Verbindung. Dieselben
Eingaben liefern auf jeder Maschine denselben `core_digest`.

Verhaeltnis zu `_core`
----------------------
`_core.py` ist eine unveraenderte Kopie des Server-Kerns (`tests/test_core_parity.py` haelt
das nach) und bleibt es. `f_ext` importiert die Praedikate von dort, statt sie zu kopieren:
`exact`, `enum` und `range` verhalten sich hier byte-genau wie im statischen Fall, und ein
Mandat ohne Evidenz-Constraints bekommt von beiden Maschinen dasselbe Verdikt, dieselbe
`reason` und denselben `grant_index` (`tests/test_ext_core.py` prueft das ueber einen
Fallkorpus). Die `core_digest`-Werte unterscheiden sich, weil der AER-Core mehr traegt —
`bundle_commit` und `decision_timestamp` — und einen eigenen Domain-Tag benutzt.

Evidenz-Constraints
-------------------
Vier Typen, alle total: was nicht eindeutig auswertbar ist, ist FAIL, nie ein stiller Erfolg.

- `evidence_bool` — `{"type", "query", "expect"}`; Evidenzwert ist ein bool und gleich
  `expect`. Deckt „nicht widerrufen" und „nicht sanktioniert" ab.
- `evidence_enum` — `{"type", "query", "values"}`; Evidenzwert (String) liegt in der
  Aufzaehlung. Deckt „Jurisdiktion in erlaubter Menge" ab.
- `evidence_range` — `{"type", "query", "lo", "hi"}`; Evidenzwert (Ganzzahl) im
  geschlossenen Intervall.
- `evidence_scaled_range` — `{"type", "field", "query", "rate_scale", "lo", "hi"}`; der
  Betrag aus der Transaktion, umgerechnet mit dem Kurs aus dem Buendel, im Intervall. Der
  Kurs steht als Ganzzahl in Einheiten von `10**rate_scale`, gerechnet wird
  `lo * 10**s <= betrag * kurs <= hi * 10**s`. Ganzzahlig ohne Division und ohne Rundung —
  ein Float wuerde je nach Plattform ein anderes Urteil ergeben und die Nachrechnung brechen.

Jeder Evidenz-Constraint prueft ausserdem das Gueltigkeitsfenster seines Items gegen den
`decision_timestamp` des Buendels. Der Kern entscheidet damit nie auf Evidenz, die zum
Entscheidungszeitpunkt nicht galt, und die Ablehnung steht als Praedikat in der Spur. Die
Signaturen prueft `f_ext` nicht — das ist V2 im Verifizierer und braucht Schluessel, die im
Kern nichts zu suchen haben.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ._core import (
    DENY,
    ENFORCE_VERSION,
    FAIL,
    MAX_ABS_INT,
    MAX_ENUM_MEMBERS,
    PASS,
    PENDING,
    PERMIT,
    _TAG_MANDATE,
    _TAG_TRANSACTION,
    _ct_eq,
    _eval_constraint,
    _is_int,
    _mandate_problem,
    _pred,
    _resolve_field,
    _type_shape_problem,
    action_digest,
)
from .evidence import (
    AER_VERSION,
    _digest,
    bundle_problem,
    compute_bundle_commit,
    evidence_values,
    parse_timestamp,
    query_key,
)

_TAG_EXT_CORE = b"aae:aer-core:v1\x00"

_EVIDENCE_TYPES = ("evidence_bool", "evidence_enum", "evidence_range",
                   "evidence_scaled_range")

# Obergrenze fuer den Nachkomma-Massstab eines Kurses. 12 deckt jede uebliche Fiat- und
# Token-Notierung; darueber waechst nur das Produkt, nicht die Aussage.
MAX_RATE_SCALE = 12


def is_evidence_constraint(constraint: Any) -> bool:
    """Zeigt dieser Constraint auf das Buendel statt auf die Transaktion?"""
    return isinstance(constraint, dict) and constraint.get("type") in _EVIDENCE_TYPES


# ------------------------------------------------------------- Evidenz-Praedikate

def _lookup(c: dict, values: Dict[str, dict], decision_epoch: Optional[int]):
    """Statement zur Abfrage des Constraints holen und sein Fenster pruefen.

    Rueckgabe `(statement, failure)` — genau eins von beiden ist gesetzt.
    """
    ctype = str(c.get("type"))
    key = query_key(c.get("query"))
    if key is None:
        return None, _pred(ctype, None, FAIL, "constraint query is not a flat descriptor")
    field = "evidence:" + key
    statement = values.get(key)
    if statement is None:
        return None, _pred(ctype, field, FAIL, "no evidence item answers this query",
                           None, c.get("query"))
    if decision_epoch is None:
        return None, _pred(ctype, field, FAIL, "bundle has no usable decision_timestamp")
    start = parse_timestamp(statement["valid_from"])
    end = parse_timestamp(statement["valid_until"])
    if start is None or end is None or not start <= decision_epoch <= end:
        return None, _pred(ctype, field, FAIL,
                           "evidence window does not cover the decision timestamp",
                           statement["value"],
                           {"valid_from": statement["valid_from"],
                            "valid_until": statement["valid_until"]})
    return statement, None


def _eval_evidence_bool(c: dict, _transaction: dict, values, epoch) -> dict:
    statement, failure = _lookup(c, values, epoch)
    if failure is not None:
        return failure
    field = "evidence:" + query_key(c["query"])
    expect = c.get("expect")
    actual = statement["value"]
    if not isinstance(expect, bool):
        return _pred("evidence_bool", field, FAIL, "constraint expect is not a bool", None, expect)
    if not isinstance(actual, bool):
        return _pred("evidence_bool", field, FAIL, "evidence value is not a bool", actual, expect)
    if actual is expect:
        return _pred("evidence_bool", field, PASS, "evidence value matches", actual, expect)
    return _pred("evidence_bool", field, FAIL, "evidence value does not match", actual, expect)


def _eval_evidence_enum(c: dict, _transaction: dict, values, epoch) -> dict:
    statement, failure = _lookup(c, values, epoch)
    if failure is not None:
        return failure
    field = "evidence:" + query_key(c["query"])
    members = c.get("values")
    actual = statement["value"]
    if not isinstance(members, list) or not members:
        return _pred("evidence_enum", field, FAIL,
                     "constraint values is not a non-empty array", None, members)
    if len(members) > MAX_ENUM_MEMBERS:
        return _pred("evidence_enum", field, FAIL,
                     "constraint values exceeds member cap", None, len(members))
    if not isinstance(actual, str):
        return _pred("evidence_enum", field, FAIL,
                     "evidence value is not a string", actual, members)
    hits = 0
    for m in members:
        if _ct_eq(actual, m):
            hits += 1
    if hits:
        return _pred("evidence_enum", field, PASS, "evidence value in enumeration", actual, members)
    return _pred("evidence_enum", field, FAIL, "evidence value not in enumeration", actual, members)


def _eval_evidence_range(c: dict, _transaction: dict, values, epoch) -> dict:
    statement, failure = _lookup(c, values, epoch)
    if failure is not None:
        return failure
    field = "evidence:" + query_key(c["query"])
    lo, hi = c.get("lo"), c.get("hi")
    bound = {"lo": lo, "hi": hi}
    actual = statement["value"]
    if not _is_int(lo) or not _is_int(hi):
        return _pred("evidence_range", field, FAIL,
                     "constraint bounds are not bounded integers", None, bound)
    if lo > hi:
        return _pred("evidence_range", field, FAIL,
                     "constraint bounds inverted (lo > hi)", None, bound)
    if not _is_int(actual):
        return _pred("evidence_range", field, FAIL,
                     "evidence value is not a bounded integer", actual, bound)
    if lo <= actual <= hi:
        return _pred("evidence_range", field, PASS, "evidence value within range", actual, bound)
    return _pred("evidence_range", field, FAIL, "evidence value outside range", actual, bound)


def _eval_evidence_scaled_range(c: dict, transaction: dict, values, epoch) -> dict:
    """Betrag aus der Transaktion, Kurs aus dem Buendel, Grenze aus dem Mandat.

    Beispiel: `field` = `cumulative_amount` in USDC-Minor-Units, `query` liefert den Kurs
    USDC/EUR als Ganzzahl mit `rate_scale` Nachkommastellen, `lo`/`hi` sind EUR-Minor-Units.
    Verglichen wird `betrag * kurs` gegen `grenze * 10**rate_scale`, also ohne Division.
    """
    statement, failure = _lookup(c, values, epoch)
    if failure is not None:
        return failure
    field = "evidence:" + query_key(c["query"])
    lo, hi, scale = c.get("lo"), c.get("hi"), c.get("rate_scale")
    tx_field = c.get("field")
    bound = {"lo": lo, "hi": hi, "rate_scale": scale, "field": tx_field}
    rate = statement["value"]
    if not _is_int(lo) or not _is_int(hi) or lo > hi:
        return _pred("evidence_scaled_range", field, FAIL,
                     "constraint bounds are not bounded integers in order", None, bound)
    if not _is_int(scale) or not 0 <= scale <= MAX_RATE_SCALE:
        return _pred("evidence_scaled_range", field, FAIL,
                     "constraint rate_scale is not an integer in 0..%d" % MAX_RATE_SCALE,
                     None, bound)
    if not _is_int(rate) or rate < 0:
        return _pred("evidence_scaled_range", field, FAIL,
                     "evidence rate is not a bounded non-negative integer", rate, bound)
    found, amount = _resolve_field(transaction, tx_field)
    if not found:
        return _pred("evidence_scaled_range", field, FAIL,
                     "field not present in transaction", None, bound)
    if not _is_int(amount) or amount < 0:
        return _pred("evidence_scaled_range", field, FAIL,
                     "transaction value is not a bounded non-negative integer", amount, bound)
    if amount > MAX_ABS_INT or rate > MAX_ABS_INT:
        return _pred("evidence_scaled_range", field, FAIL,
                     "converted operand exceeds the integer bound", amount, bound)
    factor = 10 ** scale
    converted = amount * rate
    if lo * factor <= converted <= hi * factor:
        return _pred("evidence_scaled_range", field, PASS, "converted amount within range",
                     {"amount": amount, "rate": rate, "converted": converted}, bound)
    return _pred("evidence_scaled_range", field, FAIL, "converted amount outside range",
                 {"amount": amount, "rate": rate, "converted": converted}, bound)


_EVIDENCE_EVAL = {
    "evidence_bool": _eval_evidence_bool,
    "evidence_enum": _eval_evidence_enum,
    "evidence_range": _eval_evidence_range,
    "evidence_scaled_range": _eval_evidence_scaled_range,
}


def _eval_ext_constraint(c: Any, transaction: dict, values: Dict[str, dict],
                         epoch: Optional[int]) -> dict:
    """Ein Constraint auswerten. Evidenz-Typen hier, alle anderen unveraendert in `_core`."""
    if is_evidence_constraint(c):
        return _EVIDENCE_EVAL[c["type"]](c, transaction, values, epoch)
    return _eval_constraint(c, transaction)


# ------------------------------------------------------------------------ oeffentlich

def ext_core_digest(core: dict) -> Optional[str]:
    """Digest ueber den AER-Core. Eigener Domain-Tag, damit kein Core der einen Maschine als
    Core der anderen durchgeht."""
    return _digest(_TAG_EXT_CORE, core)


def f_ext(mandate: Any, transaction: Any, bundle: Any,
          prev_core_digest: Optional[str] = None) -> dict:
    """Wertet `transaction` gegen `mandate` unter der Evidenz aus `bundle` aus.

    Rein und ohne Seiteneffekt. Rueckgabe
    `{verdict, reason, grant_index, trace, core, core_digest}` wie bei `enforce_check`,
    der Core traegt zusaetzlich `bundle_commit` und `decision_timestamp`.

    Ein strukturell unbrauchbares Buendel ist DENY — auch dann, wenn das Mandat gar keine
    Evidenz braucht. Wer AER benutzt, bekommt kein Urteil auf einem Buendel, das der
    Verifizierer nachher verwirft.
    """
    trace: list = []
    grant_index: Optional[int] = None

    b_problem = bundle_problem(bundle)
    values = evidence_values(bundle) if b_problem is None else {}
    epoch = (parse_timestamp(bundle["decision_timestamp"]) if b_problem is None else None)
    commit = compute_bundle_commit(bundle)

    problem = _mandate_problem(mandate)
    tx_ok = isinstance(transaction, dict)
    act_digest = action_digest(transaction.get("action")) if tx_ok else None
    mandate_digest = _digest(_TAG_MANDATE, mandate)
    transaction_digest = _digest(_TAG_TRANSACTION, transaction)

    if b_problem is not None:
        verdict, reason = DENY, b_problem
        trace.append(_pred("bundle_present", None, FAIL, b_problem))
    elif problem is not None:
        verdict, reason = DENY, problem
        trace.append(_pred("mandate_present", None, FAIL, problem))
    elif not tx_ok:
        verdict, reason = DENY, "transaction missing or not an object"
        trace.append(_pred("transaction_present", None, FAIL, reason))
    elif act_digest is None:
        verdict, reason = DENY, "transaction.action missing or not canonicalizable"
        trace.append(_pred("action_binding", "action", FAIL, reason))
    elif not _ct_eq(bundle["mandate_ref"], mandate_digest or ""):
        # Das Buendel ist an ein anderes Mandat gebunden. Es zu benutzen hiesse, Evidenz aus
        # einer fremden Entscheidung zu recyceln.
        verdict, reason = DENY, "bundle mandate_ref does not bind this mandate"
        trace.append(_pred("bundle_binding", "mandate_ref", FAIL, reason,
                           bundle["mandate_ref"], mandate_digest))
    elif not _ct_eq(bundle["transaction_ref"], transaction_digest or ""):
        verdict, reason = DENY, "bundle transaction_ref does not bind this transaction"
        trace.append(_pred("bundle_binding", "transaction_ref", FAIL, reason,
                           bundle["transaction_ref"], transaction_digest))
    else:
        trace.append(_pred("bundle_present", None, PASS, "bundle structurally valid",
                           len(bundle["items"]), commit))
        trace.append(_pred("bundle_binding", None, PASS,
                           "bundle binds this mandate and transaction"))
        trace.append(_pred("mandate_present", None, PASS, "mandate structurally valid"))
        grants = mandate["grants"]
        action = transaction.get("action")

        # ★ Typform vor Bindung, wortgleich zu `_core`. Ein Grant kommt erst in die
        # Bindungspruefung, wenn die Aktion genau seine `type_fields` traegt. Der Schritt
        # steht hier noch einmal und nicht als Aufruf in `_core`, weil `_core.enforce_check`
        # den ganzen Ablauf samt eigenem Core zurueckgibt; geteilt sind die Praedikate.
        # `tests/test_aer_ext_core.py` haelt beide Maschinen ueber einen Fallkorpus zusammen
        # und faellt, sobald eine der beiden abweicht.
        typed, first_problem = [], None
        for i, g in enumerate(grants):
            problem = _type_shape_problem(action, g["type_fields"])
            if problem is None:
                typed.append(i)
            elif first_problem is None:
                first_problem = (i, problem)

        if not typed:
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
                verdict, reason = DENY, "unaddressed action: no grant binds this action digest"
                trace.append(_pred("action_binding", "action", FAIL, reason, act_digest, None))
            else:
                trace.append(_pred("action_binding", "action", PASS,
                                   f"bound by grant(s) {matched}", act_digest, act_digest))
                forbidden = [i for i in matched if grants[i]["disposition"] == "forbid"]
                if forbidden:
                    grant_index = forbidden[0]
                    verdict = DENY
                    reason = f"grant[{grant_index}] disposition=forbid"
                    trace.append(_pred("disposition", None, FAIL, reason, "forbid", None))
                else:
                    verdict, reason = DENY, "no matching grant satisfied its constraints"
                    for i in matched:
                        g = grants[i]
                        preds = [_eval_ext_constraint(c, transaction, values, epoch)
                                 for c in g["constraints"]]
                        trace.extend(preds)
                        if all(p["result"] == PASS for p in preds):
                            grant_index = i
                            disp = g["disposition"]
                            verdict = PERMIT if disp == "allow" else PENDING
                            reason = (f"grant[{i}] matched, all constraints hold, "
                                      f"disposition={disp}")
                            trace.append(_pred("disposition", None, PASS, reason, disp, None))
                            break

    core = {
        "aer_version": AER_VERSION,
        "enforce_version": ENFORCE_VERSION,
        "mandate_digest": mandate_digest,
        "transaction_digest": transaction_digest,
        "action_digest": act_digest,
        "bundle_commit": commit,
        "decision_timestamp": bundle["decision_timestamp"] if b_problem is None else None,
        "verdict": verdict,
        "grant_index": grant_index,
        "reason": reason,
        "trace": trace,
        "prev_core_digest": prev_core_digest if isinstance(prev_core_digest, str) else None,
    }
    return {"verdict": verdict, "reason": reason, "grant_index": grant_index,
            "trace": trace, "core": core, "core_digest": ext_core_digest(core)}


def recompute_ext(mandate: Any, transaction: Any, bundle: Any, record: Any) -> bool:
    """V4 in einer Zeile: liefern Mandat, Transaktion und Buendel denselben Core wie der
    Record? Vergleicht den Digest, nicht die Objektform."""
    if not isinstance(record, dict):
        return False
    claimed = record.get("core_digest")
    if not isinstance(claimed, str):
        return False
    core = record.get("core")
    prev = core.get("prev_core_digest") if isinstance(core, dict) else None
    fresh = f_ext(mandate, transaction, bundle, prev_core_digest=prev)
    if not isinstance(fresh["core_digest"], str):
        return False
    return _ct_eq(fresh["core_digest"], claimed)
