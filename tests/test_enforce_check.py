"""Tests — Enforce-Mode Kern (constraint_mode="enforce", ADR-D3-v3 Komponente 3).

Der Kern ist rein: die meisten Tests brauchen weder DB noch App. Die Endpunkt- und
Regressionstests am Ende brauchen beides und ueberspringen sich, wenn
agent_delegation_config im Sandbox-Schema fehlt (die Tabelle ist nicht in app/migrations/).
"""
import json
import os
import uuid

import asyncpg
import pytest
import pytest_asyncio

from app.enforcement.enforce_check import (
    DENY, MAX_TYPE_FIELDS, PENDING, PERMIT, action_digest, core_digest, enforce_check,
    recompute,
)

DB = dict(host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack")

# Zwei Adressen mit identischem Praefix — die Vanity-Adresse des Angreifers.
ADDR = "0xABCDEF0123456789ABCDEF0123456789ABCDEF01"
ADDR_VANITY = "0xABCDEF0123456789ABCDEF0123456789ABCDEFff"

PAY = {"verb": "transfer", "asset": "USDC", "chain": "base"}
PAY_FIELDS = ["verb", "asset", "chain"]


def _tx(**over):
    tx = {"action": dict(PAY), "to": ADDR, "amount": 500, "region": "CH"}
    tx.update(over)
    return tx


_UNSET = object()  # „nicht uebergeben" — unterscheidbar von einem uebergebenen None


def _grant(disposition="allow", constraints=None, action=None, type_fields=_UNSET):
    """Ein Grant. `type_fields` folgt per Default der Aktion, an die gebunden wird — so
    testen die Faelle unten weiter ihren eigenen Gegenstand und nicht die Typform."""
    act = action if action is not None else PAY
    if type_fields is _UNSET:
        type_fields = list(act) if isinstance(act, dict) else []
    return {"action_binding": action_digest(act),
            "disposition": disposition,
            "type_fields": type_fields,
            "constraints": constraints if constraints is not None else []}


def _mandate(*grants):
    return {"mandate_version": "1.0", "grants": list(grants)}


def _preds(res, predicate):
    return [p for p in res["trace"] if p["predicate"] == predicate]


# --------------------------------------------------------------- Grundentscheidungen

def test_permit_grant_matches_constraints_hold_allow():
    res = enforce_check(_mandate(_grant("allow", [
        {"type": "exact", "field": "to", "value": ADDR},
        {"type": "range", "field": "amount", "lo": 0, "hi": 1000},
    ])), _tx())
    assert res["verdict"] == PERMIT, res["trace"]
    assert res["grant_index"] == 0


def test_deny_by_default_unaddressed_action():
    # Mandat regelt "transfer", die Transaktion tut etwas anderes.
    other = {"verb": "withdraw", "asset": "USDC", "chain": "base"}
    res = enforce_check(_mandate(_grant("allow")), _tx(action=other))
    assert res["verdict"] == DENY
    assert "unaddressed action" in res["reason"]


def test_deny_action_binding_mismatch():
    # Grant bindet an eine andere Aktion; ein Feld der Aktion weicht ab.
    res = enforce_check(_mandate(_grant("allow", action={**PAY, "chain": "ethereum"})), _tx())
    assert res["verdict"] == DENY
    binding = _preds(res, "action_binding")[0]
    assert binding["result"] == "FAIL"
    assert binding["value"] == action_digest(PAY)


def test_action_binding_is_exact_not_subset():
    # Ein Zusatzfeld in der Aktion aendert den Digest — kein „passt im Wesentlichen".
    res = enforce_check(_mandate(_grant("allow")), _tx(action={**PAY, "memo": "x"}))
    assert res["verdict"] == DENY


# ------------------------------------------------------------------ ★ Typform (type_fields)

def test_type_fields_is_required_on_every_grant():
    """Ohne deklarierte Typform ist der Grant ungueltig — nicht „ohne Einschraenkung"."""
    g = {"action_binding": action_digest(PAY), "disposition": "allow", "constraints": []}
    res = enforce_check({"mandate_version": "1.0", "grants": [g]}, _tx())
    assert res["verdict"] == DENY
    assert _preds(res, "mandate_present")[0]["result"] == "FAIL"
    assert "type_fields" in res["reason"]


@pytest.mark.parametrize("tf", [
    "verb", 7, None, [], {},                                   # kein nicht-leeres Array
    ["asset", "chain"],                                        # ohne „verb"
    ["verb", ""], ["verb", 1],                                 # leerer / nicht-String-Name
    ["verb", "verb"],                                          # Dublette
    ["verb"] + [f"f{i}" for i in range(MAX_TYPE_FIELDS)],      # ueber der Obergrenze
])
def test_invalid_type_fields_make_the_grant_malformed(tf):
    res = enforce_check(_mandate(_grant("allow", type_fields=tf)), _tx())
    assert res["verdict"] == DENY
    assert res["verdict"] not in (PERMIT, PENDING)
    assert _preds(res, "mandate_present")[0]["result"] == "FAIL"
    assert "type_fields" in res["reason"]


def test_action_matching_type_fields_exactly_permits():
    res = enforce_check(_mandate(_grant("allow", [
        {"type": "exact", "field": "to", "value": ADDR},
        {"type": "range", "field": "amount", "lo": 0, "hi": 1000},
    ], type_fields=PAY_FIELDS)), _tx())
    assert res["verdict"] == PERMIT, res["trace"]
    tp = _preds(res, "type_fields")[0]
    assert tp["result"] == "PASS"
    assert tp["value"] == sorted(PAY_FIELDS)
    assert tp["bound"] == PAY_FIELDS


def test_action_field_outside_type_fields_denies():
    """Genau der alte :78-Fall, jetzt benannt: `memo` gehoert nicht zur Typform."""
    res = enforce_check(_mandate(_grant("allow", type_fields=PAY_FIELDS)),
                        _tx(action={**PAY, "memo": "x"}))
    assert res["verdict"] == DENY
    assert _preds(res, "type_fields")[0]["result"] == "FAIL"
    assert "outside type_fields ['memo']" in res["reason"]


def test_action_missing_a_type_field_denies():
    res = enforce_check(_mandate(_grant("allow", type_fields=PAY_FIELDS)),
                        _tx(action={"verb": "transfer", "asset": "USDC"}))
    assert res["verdict"] == DENY
    assert "missing type_fields ['chain']" in res["reason"]


def test_missing_and_outside_are_named_together():
    """Ein Instanzargument in der Aktion und ein fehlendes Typ-Feld: beides steht im Grund."""
    res = enforce_check(_mandate(_grant("allow", type_fields=PAY_FIELDS)),
                        _tx(action={"verb": "transfer", "asset": "USDC", "amount": 500}))
    assert res["verdict"] == DENY
    assert "missing ['chain']" in res["reason"]
    assert "outside the type ['amount']" in res["reason"]


@pytest.mark.parametrize("action", ["pay", ["pay"], 42, None, True])
def test_non_object_action_is_denied(action):
    """★ Die String-Luecke. `"action": "pay"` kanonisiert sauber und lieferte vorher einen
    gueltigen Digest; gescheitert waere es erst als „unadressiert". Jetzt scheitert es an der
    Typform, mit Namen."""
    res = enforce_check(_mandate(_grant("allow", type_fields=PAY_FIELDS)), _tx(action=action))
    assert res["verdict"] == DENY
    tp = _preds(res, "type_fields")[0]
    assert tp["result"] == "FAIL"
    assert tp["value"] is None
    assert "action is not an object" in res["reason"]


def test_absent_action_is_denied():
    res = enforce_check(_mandate(_grant("allow", type_fields=PAY_FIELDS)),
                        {"to": ADDR, "amount": 500})
    assert res["verdict"] == DENY
    assert "action is not an object" in res["reason"]


def test_type_mismatch_is_never_pending():
    """Ein `hold`-Grant darf eine typfremde Aktion nicht in die Warteschleife heben."""
    res = enforce_check(_mandate(_grant("hold", type_fields=PAY_FIELDS)),
                        _tx(action={**PAY, "memo": "x"}))
    assert res["verdict"] == DENY
    assert res["verdict"] != PENDING


def test_a_second_type_form_in_the_same_mandate_is_skipped_not_fatal():
    """Ein Mandat darf mehrere Aktionsarten fuehren. Der Grant mit fremder Typform wird
    uebergangen, nicht zum Fehler."""
    swap = {"verb": "swap", "asset_in": "USDC", "asset_out": "ETH"}
    m = _mandate(_grant("allow", action=swap),
                 _grant("allow", [{"type": "exact", "field": "to", "value": ADDR}]))
    res = enforce_check(m, _tx())
    assert res["verdict"] == PERMIT, res["trace"]
    assert res["grant_index"] == 1


def test_type_fields_order_changes_the_document_not_the_verdict():
    """Die Reihenfolge in `type_fields` ist fuer den Abgleich egal (Mengen), aendert aber das
    Mandat als Dokument — und damit den Digest. Beides gehoert nachweisbar zusammen."""
    a = _mandate(_grant("allow", type_fields=["verb", "asset", "chain"]))
    b = _mandate(_grant("allow", type_fields=["chain", "verb", "asset"]))
    ra, rb = enforce_check(a, _tx()), enforce_check(b, _tx())
    assert ra["verdict"] == rb["verdict"] == PERMIT
    assert ra["core_digest"] != rb["core_digest"]


# ---------------------------------------------------------------------- disposition

def test_pending_only_on_explicit_hold():
    res = enforce_check(_mandate(_grant("hold", [
        {"type": "exact", "field": "to", "value": ADDR}])), _tx())
    assert res["verdict"] == PENDING
    assert res["grant_index"] == 0


def test_unaddressed_action_is_never_pending():
    """Der Guard: „nicht geregelt" darf nicht zur Vorlage werden, sonst waere es der
    Umgehungsweg — kein Grant noetig, nur warten."""
    res = enforce_check(_mandate(_grant("hold")), _tx(action={"verb": "drain"}))
    assert res["verdict"] == DENY
    assert res["verdict"] != PENDING


def test_empty_mandate_is_not_pending():
    for bad in (None, {}, {"grants": []}):
        assert enforce_check(bad, _tx())["verdict"] == DENY


def test_forbid_denies_and_is_visible_in_record():
    res = enforce_check(_mandate(_grant("forbid")), _tx())
    assert res["verdict"] == DENY
    assert "forbid" in res["reason"]
    disp = _preds(res, "disposition")
    assert disp and disp[0]["value"] == "forbid" and disp[0]["result"] == "FAIL"
    assert res["core"]["grant_index"] == 0


def test_forbid_beats_a_permitting_grant():
    # Gleiche Bindung, beide Grants treffen: forbid hat Vorrang, unabhaengig von der Reihenfolge.
    allow_g = _grant("allow", [{"type": "exact", "field": "to", "value": ADDR}])
    res = enforce_check(_mandate(allow_g, _grant("forbid")), _tx())
    assert res["verdict"] == DENY
    res_rev = enforce_check(_mandate(_grant("forbid"), allow_g), _tx())
    assert res_rev["verdict"] == DENY


# ------------------------------------------------------------------ exact-match

def test_exact_address_rejects_prefix_vanity_attacker():
    """Der Angreifer erzeugt eine Adresse mit identischem Anfang. Exakt heisst exakt."""
    m = _mandate(_grant("allow", [{"type": "exact", "field": "to", "value": ADDR}]))
    assert enforce_check(m, _tx(to=ADDR))["verdict"] == PERMIT
    res = enforce_check(m, _tx(to=ADDR_VANITY))
    assert res["verdict"] == DENY
    p = _preds(res, "exact")[0]
    assert p["result"] == "FAIL" and p["value"] == ADDR_VANITY and p["bound"] == ADDR


def test_exact_rejects_shorter_prefix_and_longer_extension():
    m = _mandate(_grant("allow", [{"type": "exact", "field": "to", "value": ADDR}]))
    assert enforce_check(m, _tx(to=ADDR[:-2]))["verdict"] == DENY
    assert enforce_check(m, _tx(to=ADDR + "00"))["verdict"] == DENY


def test_exact_is_case_sensitive_and_unnormalized():
    m = _mandate(_grant("allow", [{"type": "exact", "field": "to", "value": ADDR}]))
    assert enforce_check(m, _tx(to=ADDR.lower()))["verdict"] == DENY


def test_exact_missing_field_denies():
    m = _mandate(_grant("allow", [{"type": "exact", "field": "nope", "value": ADDR}]))
    res = enforce_check(m, _tx())
    assert res["verdict"] == DENY
    assert _preds(res, "exact")[0]["reason"] == "field not present in transaction"


# ------------------------------------------------------------------- Enumeration

def test_enum_in_set_permits_out_of_set_denies():
    m = _mandate(_grant("allow", [
        {"type": "enum", "field": "region", "values": ["CH", "DE", "AT"]}]))
    assert enforce_check(m, _tx(region="CH"))["verdict"] == PERMIT
    assert enforce_check(m, _tx(region="AT"))["verdict"] == PERMIT
    res = enforce_check(m, _tx(region="US"))
    assert res["verdict"] == DENY
    assert _preds(res, "enum")[0]["value"] == "US"


def test_enum_members_are_exact_not_prefix():
    m = _mandate(_grant("allow", [{"type": "enum", "field": "region", "values": ["CH"]}]))
    assert enforce_check(m, _tx(region="CHE"))["verdict"] == DENY
    assert enforce_check(m, _tx(region="ch"))["verdict"] == DENY


def test_enum_empty_or_malformed_denies():
    for values in ([], "CH", None):
        m = _mandate(_grant("allow", [{"type": "enum", "field": "region", "values": values}]))
        assert enforce_check(m, _tx())["verdict"] == DENY


# ------------------------------------------------------------------------- Range

@pytest.mark.parametrize("amount,expected", [
    (100, PERMIT),   # = lo
    (1000, PERMIT),  # = hi
    (99, DENY),      # lo - 1
    (1001, DENY),    # hi + 1
    (550, PERMIT),   # innen
])
def test_range_boundaries(amount, expected):
    m = _mandate(_grant("allow", [
        {"type": "range", "field": "amount", "lo": 100, "hi": 1000}]))
    assert enforce_check(m, _tx(amount=amount))["verdict"] == expected


def test_range_rejects_non_integer_and_bool():
    m = _mandate(_grant("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 1000}]))
    for bad in (500.0, "500", True, None):
        assert enforce_check(m, _tx(amount=bad))["verdict"] == DENY


def test_range_inverted_bounds_deny():
    m = _mandate(_grant("allow", [{"type": "range", "field": "amount", "lo": 1000, "hi": 100}]))
    assert enforce_check(m, _tx(amount=500))["verdict"] == DENY


def test_unknown_constraint_type_denies():
    m = _mandate(_grant("allow", [{"type": "regex", "field": "to", "value": ".*"}]))
    res = enforce_check(m, _tx())
    assert res["verdict"] == DENY
    assert "unknown constraint type" in _preds(res, "regex")[0]["reason"]


def test_all_constraints_must_hold():
    m = _mandate(_grant("allow", [
        {"type": "exact", "field": "to", "value": ADDR},
        {"type": "range", "field": "amount", "lo": 0, "hi": 100},
    ]))
    assert enforce_check(m, _tx(amount=500))["verdict"] == DENY


# ------------------------------------------------------- ★ FAIL-CLOSED (Kern-Eigenschaft)

@pytest.mark.parametrize("mandate", [
    None, {}, [], "mandate", 0,
    {"grants": None}, {"grants": []}, {"grants": [{}]},
    {"grants": [{"action_binding": "not-a-digest", "disposition": "allow",
                 "type_fields": PAY_FIELDS, "constraints": []}]},
    {"grants": [{"action_binding": "sha256:" + "a" * 64, "disposition": "maybe",
                 "type_fields": PAY_FIELDS, "constraints": []}]},
    {"grants": [{"action_binding": "sha256:" + "a" * 64, "disposition": "allow",
                 "type_fields": PAY_FIELDS, "constraints": {}}]},
])
def test_fail_closed_no_valid_mandate_denies(mandate):
    """★ Kein gueltiges Mandat im Request -> DENY. Nie ein stiller Durchlauf, nie PERMIT."""
    res = enforce_check(mandate, _tx())
    assert res["verdict"] == DENY
    assert res["verdict"] not in (PERMIT, PENDING)
    assert _preds(res, "mandate_present")[0]["result"] == "FAIL"


def test_fail_closed_missing_transaction_denies():
    m = _mandate(_grant("allow"))
    for tx in (None, {}, "tx", {"action": None}):
        assert enforce_check(m, tx)["verdict"] == DENY


def test_fail_closed_grant_without_binding_cannot_permit():
    """Ein Grant ohne gueltige Bindung macht das ganze Mandat unbrauchbar — er wird nicht
    stillschweigend uebersprungen, waehrend die anderen weiterlaufen."""
    m = {"grants": [{"disposition": "allow", "constraints": []}, _grant("allow")]}
    assert enforce_check(m, _tx())["verdict"] == DENY


# ------------------------------------------------------------------------ π-Trace

def test_trace_records_predicate_value_and_bound():
    m = _mandate(_grant("allow", [
        {"type": "exact", "field": "to", "value": ADDR},
        {"type": "enum", "field": "region", "values": ["CH", "DE"]},
        {"type": "range", "field": "amount", "lo": 100, "hi": 400},
    ]))
    res = enforce_check(m, _tx(amount=500))
    assert res["verdict"] == DENY

    ex = _preds(res, "exact")[0]
    assert (ex["field"], ex["value"], ex["bound"], ex["result"]) == ("to", ADDR, ADDR, "PASS")

    en = _preds(res, "enum")[0]
    assert (en["field"], en["value"], en["bound"], en["result"]) == (
        "region", "CH", ["CH", "DE"], "PASS")

    rg = _preds(res, "range")[0]
    assert (rg["field"], rg["value"], rg["bound"], rg["result"]) == (
        "amount", 500, {"lo": 100, "hi": 400}, "FAIL")
    assert rg["reason"] == "outside range"


def test_trace_is_in_the_signed_core():
    res = enforce_check(_mandate(_grant("allow")), _tx())
    assert res["core"]["trace"] == res["trace"]


# -------------------------------------------------------------------- Determinismus

def test_double_evaluation_is_identical():
    m = _mandate(_grant("allow", [{"type": "exact", "field": "to", "value": ADDR}]))
    a = enforce_check(m, _tx())
    b = enforce_check(m, _tx())
    assert a["core"] == b["core"]
    assert a["core_digest"] == b["core_digest"]


def test_key_order_does_not_change_the_digest():
    """JCS kanonisiert — dieselbe Semantik, andere Schluesselreihenfolge, gleicher Digest."""
    m = _mandate(_grant("allow", [{"type": "exact", "field": "to", "value": ADDR}]))
    tx1 = {"action": {"verb": "transfer", "asset": "USDC", "chain": "base"},
           "to": ADDR, "amount": 500, "region": "CH"}
    tx2 = {"region": "CH", "amount": 500, "to": ADDR,
           "action": {"chain": "base", "asset": "USDC", "verb": "transfer"}}
    assert enforce_check(m, tx1)["core_digest"] == enforce_check(m, tx2)["core_digest"]


def test_third_party_recomputes_the_verdict():
    """Ein Dritter hat mandate + transaction + Record und rechnet nach — ohne diesen Server."""
    m = _mandate(_grant("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 1000}]))
    tx = _tx()
    res = enforce_check(m, tx)
    record = {"core": res["core"], "core_digest": res["core_digest"]}
    assert recompute(m, tx, record) is True
    assert core_digest(record["core"]) == record["core_digest"]


def test_recompute_detects_tampering():
    m = _mandate(_grant("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 1000}]))
    res = enforce_check(m, _tx(amount=500))
    record = {"core": res["core"], "core_digest": res["core_digest"]}
    # Der Betrag wird nachtraeglich hochgesetzt: der Record passt nicht mehr zur Transaktion.
    assert recompute(m, _tx(amount=999999), record) is False
    # Ebenso, wenn das Mandat gegen ein grosszuegigeres getauscht wird.
    wider = _mandate(_grant("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 10 ** 9}]))
    assert recompute(wider, _tx(amount=500), record) is False


def test_core_has_no_wallclock_or_random_field():
    """Determinismus-Regel: im Core steht nichts, das nicht aus dem Request kommt."""
    res = enforce_check(_mandate(_grant("allow")), _tx())
    assert set(res["core"]) == {
        "enforce_version", "mandate_digest", "transaction_digest", "action_digest",
        "verdict", "grant_index", "reason", "trace", "prev_core_digest",
    }
    blob = json.dumps(res["core"])
    for forbidden in ("timestamp", "created_at", "now", "nonce", "eval_id"):
        assert forbidden not in blob


def test_hash_chain_links_records():
    m = _mandate(_grant("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 1000}]))
    first = enforce_check(m, _tx(amount=100))
    second = enforce_check(m, _tx(amount=200), prev_core_digest=first["core_digest"])
    assert second["core"]["prev_core_digest"] == first["core_digest"]
    # Die Verkettung geht in den Digest ein: derselbe Verdikt an anderer Kettenstelle
    # ergibt einen anderen core_digest.
    detached = enforce_check(m, _tx(amount=200))
    assert detached["core_digest"] != second["core_digest"]


def test_core_carries_no_database_state():
    """Der Kern nimmt keine conn und fasst kein Postgres an — im Gegensatz zum AAE-Evaluator,
    dessen rate_limit/single_use-Werte aus aae_evaluations stammen."""
    import inspect
    from app.enforcement import enforce_check as mod
    src = inspect.getsource(mod)
    for forbidden in ("asyncpg", "conn", "SELECT ", "INSERT "):
        assert forbidden not in src, f"enforce_check core references {forbidden!r}"


# ============================================================================
# Endpunkt + Regression (brauchen DB; ueberspringen ohne agent_delegation_config)
# ============================================================================

async def _has_delegation_config(conn) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass('public.agent_delegation_config')"))


@pytest_asyncio.fixture
async def cfg_conn():
    conn = await asyncpg.connect(**DB)
    try:
        if not await _has_delegation_config(conn):
            pytest.skip("agent_delegation_config not present in this schema")
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def enforce_agent(credit_test_agent, cfg_conn):
    """Agent + API-Key, dazu eine agent_delegation_config-Zeile mit waehlbarem Modus."""
    made = []

    async def _make(mode: str):
        did, key = await credit_test_agent()
        await cfg_conn.execute(
            "INSERT INTO agent_delegation_config (did, delegation_permitted, max_depth, "
            "constraint_mode, updated_at) VALUES ($1, true, 2, $2, NOW()) "
            "ON CONFLICT (did) DO UPDATE SET constraint_mode = $2", did, mode)
        made.append(did)
        return did, key

    yield _make
    for did in made:
        await cfg_conn.execute("DELETE FROM agent_delegation_config WHERE did = $1", did)


async def test_endpoint_returns_verdict_and_record(async_client, credit_test_agent):
    _did, key = await credit_test_agent()
    m = _mandate(_grant("allow", [{"type": "exact", "field": "to", "value": ADDR}]))
    r = await async_client.post("/enforce/check", headers={"X-API-Key": key},
                                json={"mandate": m, "transaction": _tx()})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["verdict"] == PERMIT
    assert b["record"]["core_digest"] == core_digest(b["record"]["core"])
    assert b["trace"] and b["record"]["core"]["prev_core_digest"] is None


async def test_endpoint_fail_closed_without_mandate(async_client, credit_test_agent):
    """★ Am Endpunkt sichtbar: kein Mandat heisst DENY mit Record, nicht 4xx und nicht PERMIT."""
    _did, key = await credit_test_agent()
    r = await async_client.post("/enforce/check", headers={"X-API-Key": key},
                                json={"transaction": _tx()})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == DENY


async def test_endpoint_requires_auth(async_client):
    r = await async_client.post("/enforce/check", json={"mandate": {}, "transaction": {}})
    assert r.status_code in (401, 403)


async def test_enforce_agent_has_no_bypass_via_aae_evaluate(async_client, enforce_agent):
    """Kein Umgehungspfad: ein enforce-Agent wird vom AAE-Evaluator abgewiesen und auf
    /enforce/check verwiesen. Sonst gaebe es zwei Wege zu einem PERMIT."""
    did, key = await enforce_agent("enforce")
    r = await async_client.post(
        "/vc/aae/evaluate", headers={"X-API-Key": key},
        json={"aae_ref": "sha256:" + "b" * 64,
              "action_context": {"agent_did": did, "vc_id": "vc1",
                                 "nonce": uuid.uuid4().hex, "action": "pay"}})
    assert r.status_code == 409, r.text
    assert "enforce" in r.text and "/enforce/check" in r.text


@pytest.mark.parametrize("mode", ["none", "inherit", "restrict"])
async def test_non_enforce_modes_still_reach_the_evaluator(async_client, enforce_agent, mode):
    """Regression: none/inherit/restrict laufen unveraendert in evaluate_envelope.
    Der Envelope existiert nicht -> der Evaluator antwortet DENY (envelope_not_found),
    also 200 aus dem Evaluator und ausdruecklich KEIN 409 aus dem enforce-Guard."""
    did, key = await enforce_agent(mode)
    r = await async_client.post(
        "/vc/aae/evaluate", headers={"X-API-Key": key},
        json={"aae_ref": "sha256:" + "c" * 64,
              "action_context": {"agent_did": did, "vc_id": "vc1",
                                 "nonce": uuid.uuid4().hex, "action": "pay"}})
    assert r.status_code != 409, r.text
    if r.status_code == 200:
        assert r.json()["verdict"] == "DENY"


async def test_configure_accepts_enforce_and_keeps_the_old_modes(async_client, credit_test_agent,
                                                                 cfg_conn):
    did, key = await credit_test_agent()
    try:
        for mode in ("none", "inherit", "restrict", "enforce"):
            r = await async_client.post(
                "/delegation/configure", headers={"X-API-Key": key},
                json={"did": did, "delegation_permitted": True, "max_depth": 2,
                      "constraint_mode": mode})
            assert r.status_code == 200, (mode, r.text)
            assert r.json()["constraint_mode"] == mode
        r = await async_client.post(
            "/delegation/configure", headers={"X-API-Key": key},
            json={"did": did, "delegation_permitted": True, "max_depth": 2,
                  "constraint_mode": "bogus"})
        assert r.status_code == 400
    finally:
        await cfg_conn.execute("DELETE FROM agent_delegation_config WHERE did = $1", did)


async def test_restrict_topology_rule_is_untouched(async_client, enforce_agent):
    """Regression: restrict verbietet weiterhin das Praegen einer Wurzel-Delegation.
    Der enforce-Bau fasst diesen Zweig nicht an."""
    did, key = await enforce_agent("restrict")
    r = await async_client.post(
        "/delegation/create", headers={"X-API-Key": key},
        json={"delegator_did": did, "audience_did": did,
              "capabilities": {"example://x": {"crud/read": [{}]}}, "proofs": []})
    assert r.status_code == 400, r.text
    assert "forbids minting a root delegation" in r.text
