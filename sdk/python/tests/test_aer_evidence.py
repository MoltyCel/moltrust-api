"""Tests — Evidenz-Items und Buendel: Form, Kanonisierung, Commit.

Der Schwerpunkt liegt auf dem, was zwei Maschinen auseinanderlaufen liesse: eine andere
Reihenfolge, eine andere Serialisierung desselben Statements, ein anders gelesener
Zeitstempel. Was mehrdeutig ist, muss durchfallen, nicht geraten werden.
"""
import base64
import json

import pytest
from conftest import (
    ADDR, FX_Q, JURIS_Q, REVOKED_Q, T0, WINDOW, aer_bundle, evidence_item, grant,
    mandate, source_id, tx,
)

from moltrust_enforce import (
    AER_VERSION, PAYLOAD_TYPE, bundle_problem, compute_bundle_commit, envelope_statement,
    evidence_values, item_digest, make_envelope, make_statement, pae, parse_timestamp,
    query_key, evidence_payload_bytes, statement_problem,
)

MANDATE = mandate(grant("allow"))
TX = tx()


def bundle_of(*items, decision_timestamp=T0):
    return aer_bundle(list(items), MANDATE, TX, decision_timestamp)


# --------------------------------------------------------------------- Zeitstempel

@pytest.mark.parametrize("value", [
    "2026-08-31T12:00:00Z",
    "1970-01-01T00:00:00Z",
    "2099-12-31T23:59:59Z",
])
def test_timestamps_that_parse(value):
    assert parse_timestamp(value) is not None


@pytest.mark.parametrize("value", [
    "2026-08-31T12:00:00+01:00",   # Offset statt Z
    "2026-08-31T12:00:00.500Z",    # Bruchteile
    "2026-02-30T00:00:00Z",        # Kalender
    "2026-08-31T23:59:60Z",        # Schaltsekunde
    "2026-08-31 12:00:00Z",        # Leerzeichen statt T
    "2101-01-01T00:00:00Z",        # ausserhalb der Schranke
    20260831120000,
    None,
])
def test_timestamps_that_do_not_parse(value):
    assert parse_timestamp(value) is None


def test_timestamp_is_utc_seconds_not_local_time():
    assert parse_timestamp("1970-01-02T00:00:00Z") == 86400


# ------------------------------------------------------------------------ Statement

def test_statement_round_trips_through_the_envelope():
    item = evidence_item("revocation", REVOKED_Q, False)
    statement, problem = envelope_statement(item)
    assert problem is None
    assert statement["source_id"] == source_id("revocation")
    assert statement["value"] is False
    assert statement["aer_version"] == AER_VERSION


def test_statement_rejects_unknown_fields():
    statement = make_statement(source_id("x"), REVOKED_Q, False, WINDOW[0], WINDOW[1], "n")
    statement["priority"] = "high"
    assert "unknown fields" in statement_problem(statement)


def test_statement_rejects_inverted_window():
    statement = make_statement(source_id("x"), REVOKED_Q, False, WINDOW[1], WINDOW[0], "n")
    assert "inverted" in statement_problem(statement)


@pytest.mark.parametrize("query", [
    {},                                   # leer
    {"subject": ADDR},                    # ohne kind
    {"kind": "", "subject": ADDR},        # leeres kind
    {"kind": "x", "sub": {"a": 1}},       # verschachtelt
    {"kind": "x", "flag": True},          # bool als Wert
    "revocation",
])
def test_statement_rejects_unusable_queries(query):
    statement = make_statement(source_id("x"), query, False, WINDOW[0], WINDOW[1], "n")
    assert statement_problem(statement) is not None
    assert query_key(query) is None


def test_query_key_ignores_key_order():
    assert query_key({"kind": "fx", "pair": "USDC/EUR"}) \
        == query_key({"pair": "USDC/EUR", "kind": "fx"})


# ------------------------------------------------------------------------- Envelope

def test_envelope_rejects_a_non_canonical_payload():
    """Dieselben Felder, andere Serialisierung: die Signatur passt, das Item nicht.

    Ohne diese Pruefung liesse sich einer gueltigen Signatur ein anders geschriebenes
    Statement unterschieben, und zwei Verifizierer kaemen zu verschiedenen Werten.
    """
    item = evidence_item("revocation", REVOKED_Q, False)
    statement, _ = envelope_statement(item)
    loose = json.dumps(statement, indent=2).encode("utf-8")
    assert loose != evidence_payload_bytes(statement)
    item["payload"] = base64.b64encode(loose).decode("ascii")
    _statement, problem = envelope_statement(item)
    assert "canonical" in problem


def test_envelope_rejects_a_foreign_payload_type():
    item = evidence_item("revocation", REVOKED_Q, False)
    item["payloadType"] = "application/json"
    assert "payloadType" in envelope_statement(item)[1]


def test_envelope_rejects_missing_signatures():
    statement = make_statement(source_id("x"), REVOKED_Q, False, WINDOW[0], WINDOW[1], "n")
    assert "no signatures" in envelope_statement(make_envelope(statement, []))[1]


def test_pae_binds_the_payload_type():
    """Gleiche Bytes, anderer Typ ergibt eine andere Nachricht — sonst waere ein Statement
    als anderer Nachrichtentyp weiterverwendbar."""
    payload = b"{}"
    assert pae(PAYLOAD_TYPE, payload) != pae("application/json", payload)
    assert pae(PAYLOAD_TYPE, payload).startswith(b"DSSEv1 ")


def test_pae_length_prefixes_prevent_a_collision():
    """`a` + `bc` und `ab` + `c` duerfen nicht dieselbe PAE ergeben."""
    assert pae("a", b"bc") != pae("ab", b"c")


# --------------------------------------------------------------------------- Buendel

def test_bundle_orders_items_canonically():
    a = evidence_item("revocation", REVOKED_Q, False)
    b = evidence_item("sanction", JURIS_Q, "CH")
    forward = bundle_of(a, b)
    backward = bundle_of(b, a)
    assert forward == backward
    digests = [item_digest(i) for i in forward["items"]]
    assert digests == sorted(digests)


def test_bundle_commit_covers_every_field():
    base = bundle_of(evidence_item("revocation", REVOKED_Q, False))
    for key, changed in (("decision_timestamp", "2026-08-31T12:00:01Z"),
                         ("kernel_version", "9.9"),
                         ("mandate_ref", "sha256:" + "0" * 64)):
        altered = dict(base)
        altered[key] = changed
        assert compute_bundle_commit(altered) != base["bundle_commit"]


def test_bundle_commit_survives_key_reordering():
    base = bundle_of(evidence_item("revocation", REVOKED_Q, False))
    shuffled = {k: base[k] for k in sorted(base, reverse=True)}
    assert compute_bundle_commit(shuffled) == base["bundle_commit"]


def test_empty_bundle_is_valid():
    """Ein Mandat ohne Evidenz-Constraints braucht kein Item — aber ein Buendel, damit die
    Entscheidung an Zeitpunkt und Eingaben gebunden bleibt."""
    assert bundle_problem(bundle_of()) is None


def test_bundle_rejects_a_duplicate_item():
    item = evidence_item("revocation", REVOKED_Q, False)
    broken = bundle_of(item)
    broken["items"] = [item, item]
    broken["bundle_commit"] = compute_bundle_commit(broken)
    assert "out of canonical order or duplicated" in bundle_problem(broken)


def test_bundle_rejects_two_answers_to_one_query():
    """Zwei Antworten auf dieselbe Frage machen das Urteil von der Auswahl abhaengig."""
    yes = evidence_item("revocation", REVOKED_Q, True, nonce="n-1")
    no = evidence_item("revocation", REVOKED_Q, False, nonce="n-2")
    broken = bundle_of(yes, no)
    assert "already answered" in bundle_problem(broken)


def test_bundle_rejects_manual_reordering():
    a = evidence_item("revocation", REVOKED_Q, False)
    b = evidence_item("sanction", JURIS_Q, "CH")
    broken = bundle_of(a, b)
    broken["items"] = list(reversed(broken["items"]))
    broken["bundle_commit"] = compute_bundle_commit(broken)
    assert "out of canonical order" in bundle_problem(broken)


def test_bundle_rejects_a_stale_commit():
    broken = bundle_of(evidence_item("revocation", REVOKED_Q, False))
    broken["decision_timestamp"] = "2026-08-31T12:00:01Z"
    assert "does not match the bundle content" in bundle_problem(broken)


def test_bundle_rejects_unknown_fields():
    broken = bundle_of()
    broken["operator_note"] = "trust me"
    assert "unknown fields" in bundle_problem(broken)


def test_bundle_rejects_too_many_items():
    items = [evidence_item("fx", dict(FX_Q, pair=f"P{i}/EUR"), i) for i in range(65)]
    broken = bundle_of(*items)
    assert "item cap" in bundle_problem(broken)


def test_evidence_values_are_keyed_by_query():
    bundle = bundle_of(evidence_item("revocation", REVOKED_Q, False),
                       evidence_item("fx", FX_Q, 920000))
    values = evidence_values(bundle)
    assert values[query_key(REVOKED_Q)]["value"] is False
    assert values[query_key(FX_Q)]["value"] == 920000


def test_evidence_values_of_a_broken_bundle_are_empty():
    broken = bundle_of(evidence_item("revocation", REVOKED_Q, False))
    broken["bundle_commit"] = "sha256:" + "0" * 64
    assert evidence_values(broken) == {}
