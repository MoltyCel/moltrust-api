"""Tests — `f_ext`: dieselbe Maschine wie `_core`, plus Evidenz.

Zwei Gruppen. Die erste haelt fest, dass ein Mandat ohne Evidenz-Constraints von `f_ext`
dasselbe Urteil bekommt wie von `enforce_check` — sonst waere AER eine zweite, leise
abweichende Enforcement-Semantik. Die zweite prueft die vier Evidenz-Praedikate, jeweils
den haltenden und den fallenden Fall.
"""
import pytest
from conftest import (
    ADDR, FX_Q, JURIS_Q, REVOKED_Q, SANCTION_Q, T0, aer_bundle, evidence_item, grant,
    mandate, tx,
)

from moltrust_enforce import (
    DENY, PENDING, PERMIT, action_digest, enforce_check, ext_core_digest, f_ext,
    is_evidence_constraint, recompute_ext,
)

PAY = {"verb": "transfer", "asset": "USDC", "chain": "base"}
EXACT_TO = {"type": "exact", "field": "to", "value": ADDR}
RANGE_AMOUNT = {"type": "range", "field": "amount", "lo": 0, "hi": 1000}

NOT_REVOKED = {"type": "evidence_bool", "query": REVOKED_Q, "expect": False}
NOT_SANCTIONED = {"type": "evidence_bool", "query": SANCTION_Q, "expect": False}
JURIS_ALLOWED = {"type": "evidence_enum", "query": JURIS_Q, "values": ["CH", "DE", "AT"]}
FX_PLAUSIBLE = {"type": "evidence_range", "query": FX_Q, "lo": 500000, "hi": 1500000}
# 500 USDC-Minor-Units zum Kurs 0.92 EUR/USDC (6 Nachkommastellen) = 460 EUR-Minor-Units.
FIAT_LIMIT = {"type": "evidence_scaled_range", "field": "amount", "query": FX_Q,
              "rate_scale": 6, "lo": 0, "hi": 500}


def decide(mandate_obj, transaction, items=(), decision_timestamp=T0, bundle=None):
    if bundle is None:
        bundle = aer_bundle(list(items), mandate_obj, transaction, decision_timestamp)
    return f_ext(mandate_obj, transaction, bundle), bundle


# ------------------------------------------------------- Parität zum statischen Kern

CORPUS = [
    ({}, {"action": PAY}),
    ({"grants": []}, {"action": PAY}),
    (mandate(grant("allow")), {"action": PAY}),
    (mandate(grant("forbid")), {"action": PAY}),
    (mandate(grant("hold", [EXACT_TO])), tx()),
    (mandate(grant("allow", [EXACT_TO, RANGE_AMOUNT])), tx()),
    (mandate(grant("allow", [RANGE_AMOUNT])), tx(amount=5000)),
    (mandate(grant("allow", [{"type": "enum", "field": "region",
                              "values": ["CH", "DE"]}])), tx(region="US")),
    (mandate(grant("allow", action={"verb": "swap"})), tx()),
    (mandate(grant("allow", [{"type": "nonsense", "field": "to"}])), tx()),
    # Typform vor Bindung (#319): ein Instanzargument in der Aktion, ein fehlendes
    # Typ-Feld, eine Aktion, die kein Objekt ist, und ein Grant ohne `type_fields`.
    (mandate(grant("allow")), tx(action=dict(PAY, amount=500))),
    (mandate(grant("allow")), tx(action={"verb": "transfer", "asset": "USDC"})),
    (mandate(grant("allow")), tx(action="transfer")),
    ({"grants": [{"action_binding": action_digest(PAY), "disposition": "allow",
                  "constraints": []}]}, tx()),
]


@pytest.mark.parametrize("mandate_obj,transaction", CORPUS)
def test_evidence_free_mandates_decide_exactly_as_the_static_core(mandate_obj, transaction):
    static = enforce_check(mandate_obj, transaction)
    extended, _bundle = decide(mandate_obj, transaction)
    assert extended["verdict"] == static["verdict"]
    assert extended["reason"] == static["reason"]
    assert extended["grant_index"] == static["grant_index"]
    # Die Constraint-Praedikate selbst sind identisch; `f_ext` haengt nur die
    # Buendel-Praedikate davor.
    static_predicates = [p for p in static["trace"] if p["predicate"] != "mandate_present"]
    extended_predicates = [p for p in extended["trace"]
                           if p["predicate"] not in ("mandate_present", "bundle_present",
                                                     "bundle_binding")]
    assert extended_predicates == static_predicates


def test_the_two_cores_do_not_share_a_digest():
    """Gleiche Eingaben, verschiedene Maschinen: ein AER-Core darf nicht als statischer
    Core durchgehen und umgekehrt."""
    m, t = mandate(grant("allow")), {"action": PAY}
    extended, _bundle = decide(m, t)
    assert extended["core_digest"] != enforce_check(m, t)["core_digest"]


# ------------------------------------------------------------- Bindung an das Buendel

def test_a_bundle_bound_to_another_mandate_is_denied():
    m = mandate(grant("allow"))
    other = mandate(grant("allow", [EXACT_TO]))
    foreign = aer_bundle([], other, tx())
    result = f_ext(m, tx(), foreign)
    assert result["verdict"] == DENY
    assert "mandate_ref" in result["reason"]


def test_a_bundle_bound_to_another_transaction_is_denied():
    m = mandate(grant("allow"))
    foreign = aer_bundle([], m, tx(amount=1))
    result = f_ext(m, tx(), foreign)
    assert result["verdict"] == DENY
    assert "transaction_ref" in result["reason"]


def test_a_broken_bundle_denies_even_without_evidence_constraints():
    m = mandate(grant("allow"))
    _result, bundle = decide(m, tx())
    bundle["bundle_commit"] = "sha256:" + "0" * 64
    assert f_ext(m, tx(), bundle)["verdict"] == DENY


def test_a_missing_bundle_denies():
    assert f_ext(mandate(grant("allow")), tx(), None)["verdict"] == DENY


# ----------------------------------------------------------------- evidence_bool

def test_revocation_evidence_permits_when_not_revoked():
    m = mandate(grant("allow", [EXACT_TO, NOT_REVOKED]))
    result, _b = decide(m, tx(), [evidence_item("revocation", REVOKED_Q, False)])
    assert result["verdict"] == PERMIT


def test_revocation_evidence_denies_when_revoked():
    m = mandate(grant("allow", [NOT_REVOKED]))
    result, _b = decide(m, tx(), [evidence_item("revocation", REVOKED_Q, True)])
    assert result["verdict"] == DENY
    assert any(p["predicate"] == "evidence_bool" and p["result"] == "FAIL"
               for p in result["trace"])


def test_missing_evidence_denies():
    """Kein Item zur Frage heisst DENY, nicht „ungeprueft durchlassen"."""
    m = mandate(grant("allow", [NOT_REVOKED]))
    result, _b = decide(m, tx(), [])
    assert result["verdict"] == DENY
    assert any("no evidence item answers this query" == p["reason"] for p in result["trace"])


def test_a_non_bool_evidence_value_denies():
    m = mandate(grant("allow", [NOT_REVOKED]))
    result, _b = decide(m, tx(), [evidence_item("revocation", REVOKED_Q, "false")])
    assert result["verdict"] == DENY


def test_hold_disposition_with_evidence_yields_pending():
    m = mandate(grant("hold", [NOT_REVOKED]))
    result, _b = decide(m, tx(), [evidence_item("revocation", REVOKED_Q, False)])
    assert result["verdict"] == PENDING


# ---------------------------------------------------------------------- Frische

def test_evidence_outside_its_window_denies():
    """Das Item ist echt und signiert, gilt aber zum Entscheidungszeitpunkt nicht mehr."""
    m = mandate(grant("allow", [NOT_REVOKED]))
    stale = evidence_item("revocation", REVOKED_Q, False,
                          window=("2026-08-30T10:00:00Z", "2026-08-30T11:00:00Z"))
    result, _b = decide(m, tx(), [stale])
    assert result["verdict"] == DENY
    assert any("window does not cover" in p["reason"] for p in result["trace"])


def test_evidence_exactly_at_the_window_edge_holds():
    """Beide Grenzen zaehlen — ein halboffenes Fenster waere eine stille Abweichung von
    der OCSP-Semantik, an der das Format haengt."""
    m = mandate(grant("allow", [NOT_REVOKED]))
    edge = evidence_item("revocation", REVOKED_Q, False, window=(T0, T0))
    result, _b = decide(m, tx(), [edge])
    assert result["verdict"] == PERMIT


# ---------------------------------------------------------------- evidence_enum

def test_jurisdiction_in_the_allowed_set_permits():
    m = mandate(grant("allow", [JURIS_ALLOWED]))
    result, _b = decide(m, tx(), [evidence_item("jurisdiction", JURIS_Q, "CH")])
    assert result["verdict"] == PERMIT


def test_jurisdiction_outside_the_allowed_set_denies():
    m = mandate(grant("allow", [JURIS_ALLOWED]))
    result, _b = decide(m, tx(), [evidence_item("jurisdiction", JURIS_Q, "US")])
    assert result["verdict"] == DENY


# --------------------------------------------------------------- evidence_range

def test_evidence_range_holds_and_fails_on_the_same_query():
    m = mandate(grant("allow", [FX_PLAUSIBLE]))
    inside, _a = decide(m, tx(), [evidence_item("fx", FX_Q, 920000)])
    outside, _b = decide(m, tx(), [evidence_item("fx", FX_Q, 4000000)])
    assert inside["verdict"] == PERMIT
    assert outside["verdict"] == DENY


def test_a_float_rate_denies():
    """Fliesskomma bricht die Nachrechnung; der Kern nimmt nur Ganzzahlen."""
    m = mandate(grant("allow", [FX_PLAUSIBLE]))
    result, _b = decide(m, tx(), [evidence_item("fx", FX_Q, 920000)])
    assert result["verdict"] == PERMIT
    broken = {"type": "evidence_range", "query": FX_Q, "lo": 0, "hi": 2}
    m2 = mandate(grant("allow", [broken]))
    result2, _b2 = decide(m2, tx(), [evidence_item("fx", FX_Q, "0.92")])
    assert result2["verdict"] == DENY


# -------------------------------------------------------- evidence_scaled_range

def test_fiat_limit_holds_below_the_ceiling():
    """500 * 920000 = 460000000 <= 500 * 10**6. 460 EUR unter 500 EUR Limit."""
    m = mandate(grant("allow", [FIAT_LIMIT]))
    result, _b = decide(m, tx(amount=500), [evidence_item("fx", FX_Q, 920000)])
    assert result["verdict"] == PERMIT


def test_fiat_limit_fails_above_the_ceiling():
    m = mandate(grant("allow", [FIAT_LIMIT]))
    result, _b = decide(m, tx(amount=600), [evidence_item("fx", FX_Q, 920000)])
    assert result["verdict"] == DENY
    predicate = [p for p in result["trace"] if p["predicate"] == "evidence_scaled_range"][0]
    assert predicate["value"]["converted"] == 600 * 920000


def test_fiat_limit_is_computed_without_rounding():
    """Der Grenzfall genau auf dem Limit haelt; ein Minor-Unit darueber faellt. Mit einer
    Division und Rundung waere beides gleich ausgegangen."""
    m = mandate(grant("allow", [dict(FIAT_LIMIT, hi=460)]))
    exact, _a = decide(m, tx(amount=500), [evidence_item("fx", FX_Q, 920000)])
    over, _b = decide(m, tx(amount=501), [evidence_item("fx", FX_Q, 920000)])
    assert exact["verdict"] == PERMIT
    assert over["verdict"] == DENY


def test_fiat_limit_rejects_an_out_of_range_scale():
    m = mandate(grant("allow", [dict(FIAT_LIMIT, rate_scale=99)]))
    result, _b = decide(m, tx(amount=1), [evidence_item("fx", FX_Q, 920000)])
    assert result["verdict"] == DENY


def test_fiat_limit_rejects_a_missing_transaction_field():
    m = mandate(grant("allow", [dict(FIAT_LIMIT, field="cumulative")]))
    result, _b = decide(m, tx(amount=500), [evidence_item("fx", FX_Q, 920000)])
    assert result["verdict"] == DENY


# --------------------------------------------------------------- Determinismus

def test_two_runs_of_the_same_inputs_agree_bit_for_bit():
    m = mandate(grant("allow", [EXACT_TO, NOT_REVOKED, JURIS_ALLOWED]))
    items = [evidence_item("revocation", REVOKED_Q, False),
             evidence_item("jurisdiction", JURIS_Q, "CH")]
    first, bundle = decide(m, tx(), items)
    second = f_ext(m, tx(), bundle)
    assert first["core_digest"] == second["core_digest"]
    assert first["core"] == second["core"]


def test_recompute_ext_accepts_the_record_it_produced():
    m = mandate(grant("allow", [NOT_REVOKED]))
    record, bundle = decide(m, tx(), [evidence_item("revocation", REVOKED_Q, False)])
    assert recompute_ext(m, tx(), bundle, record) is True


def test_recompute_ext_rejects_a_forged_verdict():
    """Der Betreiber schreibt PERMIT in den Core und rechnet den Digest passend nach. Die
    Nachrechnung aus denselben Eingaben ergibt trotzdem DENY."""
    m = mandate(grant("allow", [NOT_REVOKED]))
    record, bundle = decide(m, tx(), [evidence_item("revocation", REVOKED_Q, True)])
    assert record["verdict"] == DENY
    forged_core = dict(record["core"], verdict=PERMIT)
    forged = {"verdict": PERMIT, "core": forged_core,
              "core_digest": ext_core_digest(forged_core)}
    assert forged["core_digest"] != record["core_digest"]
    assert recompute_ext(m, tx(), bundle, forged) is False


def test_the_chain_field_is_carried_into_the_digest():
    m = mandate(grant("allow"))
    _first, bundle = decide(m, tx())
    a = f_ext(m, tx(), bundle, prev_core_digest=None)
    b = f_ext(m, tx(), bundle, prev_core_digest="sha256:" + "1" * 64)
    assert a["core_digest"] != b["core_digest"]


# ------------------------------------------------------------------- Hilfsfunktion

@pytest.mark.parametrize("constraint,expected", [
    (NOT_REVOKED, True),
    (JURIS_ALLOWED, True),
    (FIAT_LIMIT, True),
    (EXACT_TO, False),
    (RANGE_AMOUNT, False),
    ("evidence_bool", False),
])
def test_is_evidence_constraint(constraint, expected):
    assert is_evidence_constraint(constraint) is expected


def test_action_binding_still_comes_from_the_transaction_action():
    """Evidenz aendert nichts an der Bindung: ein Grant fuer eine andere Aktion greift
    auch mit passender Evidenz nicht."""
    m = mandate(grant("allow", [NOT_REVOKED], action={"verb": "swap"}))
    result, _b = decide(m, tx(), [evidence_item("revocation", REVOKED_Q, False)])
    assert result["verdict"] == DENY
    assert action_digest(PAY) != action_digest({"verb": "swap"})
