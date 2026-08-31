"""Tests — V1 bis V4 und das CLI.

Der Leitfall steht ganz unten: eine Entscheidung wird gefaellt, alles Zustandsbehaftete
weggeworfen, und der Record spaeter aus reinem JSON heraus geprueft. Davor je Pruefung der
Fall, den sie abwehren soll.
"""
import json
import subprocess
import sys

import pytest
from conftest import (
    ADDR, JURIS_Q, REVOKED_Q, T0, aer_bundle, evidence_item, grant, mandate, source_id,
    trust_list, tx,
)

from moltrust_enforce import (
    DENY, PERMIT, compute_bundle_commit, ext_core_digest, f_ext, trust_list_problem,
    verify_record,
)
from moltrust_enforce.cli import EXIT_FAIL, EXIT_INPUT, EXIT_PASS, main

EXACT_TO = {"type": "exact", "field": "to", "value": ADDR}
NOT_REVOKED = {"type": "evidence_bool", "query": REVOKED_Q, "expect": False}
JURIS_ALLOWED = {"type": "evidence_enum", "query": JURIS_Q, "values": ["CH", "DE"]}

MANDATE = mandate(grant("allow", [EXACT_TO, NOT_REVOKED, JURIS_ALLOWED]))
TRUST = trust_list("revocation", "jurisdiction")


def good_case(decision_timestamp=T0, items=None):
    """Ein Record, der alle vier Pruefungen bestehen soll."""
    transaction = tx()
    if items is None:
        items = [evidence_item("revocation", REVOKED_Q, False),
                 evidence_item("jurisdiction", JURIS_Q, "CH")]
    bundle = aer_bundle(items, MANDATE, transaction, decision_timestamp)
    record = f_ext(MANDATE, transaction, bundle)
    return record, bundle, transaction


def check(record, bundle, transaction, trust=TRUST, mandate_obj=MANDATE):
    return verify_record(record, bundle, mandate_obj, transaction, trust)


# ------------------------------------------------------------------ der gruene Fall

def test_a_sound_record_passes_all_four():
    record, bundle, transaction = good_case()
    result = check(record, bundle, transaction)
    assert result.ok is True
    assert bool(result) is True
    assert result.failures == ()
    assert result.recomputed_verdict == PERMIT
    assert [result.checks[n]["result"] for n in ("V1", "V2", "V3", "V4")] == ["PASS"] * 4


def test_a_denying_record_also_verifies():
    """PASS heisst „dieses Urteil ist nachgerechnet", nicht „die Aktion ist erlaubt".
    Ein sauberes DENY besteht die Pruefung genauso."""
    transaction = tx()
    items = [evidence_item("revocation", REVOKED_Q, True),
             evidence_item("jurisdiction", JURIS_Q, "CH")]
    bundle = aer_bundle(items, MANDATE, transaction, T0)
    record = f_ext(MANDATE, transaction, bundle)
    result = check(record, bundle, transaction)
    assert record["verdict"] == DENY
    assert result.ok is True
    assert result.recomputed_verdict == DENY


# ------------------------------------------------------------------------- V1

def test_v1_catches_an_edited_bundle():
    record, bundle, transaction = good_case()
    bundle["items"] = bundle["items"][:1]
    result = check(record, bundle, transaction)
    assert result.checks["V1"]["result"] == "FAIL"
    assert not result.ok


def test_v1_catches_a_bundle_from_another_decision():
    """Derselbe Record, ein Buendel mit anderem Zeitpunkt: das Item ist echt, der Commit
    passt in sich — aber der Record zeigt auf einen anderen."""
    record, _bundle, transaction = good_case()
    other = aer_bundle([evidence_item("revocation", REVOKED_Q, False),
                        evidence_item("jurisdiction", JURIS_Q, "CH")],
                       MANDATE, transaction, "2026-08-31T12:30:00Z")
    result = check(record, other, transaction)
    assert result.checks["V1"]["result"] == "FAIL"
    assert "does not reference this bundle commit" in result.checks["V1"]["reason"]


def test_v1_catches_a_substituted_mandate():
    record, bundle, transaction = good_case()
    other_mandate = mandate(grant("allow"))
    result = check(record, bundle, transaction, mandate_obj=other_mandate)
    assert result.checks["V1"]["result"] == "FAIL"
    assert "mandate_ref" in result.checks["V1"]["reason"]


def test_v1_catches_a_substituted_transaction():
    record, bundle, _transaction = good_case()
    result = check(record, bundle, tx(amount=1))
    assert result.checks["V1"]["result"] == "FAIL"
    assert "transaction_ref" in result.checks["V1"]["reason"]


# ------------------------------------------------------------------------- V2

def test_v2_catches_a_value_changed_after_signing():
    """Der Betreiber dreht `revoked` von true auf false. Die Signatur deckt den alten Wert."""
    record, bundle, transaction = good_case(
        items=[evidence_item("revocation", REVOKED_Q, True,
                             tamper=lambda s: dict(s, value=False)),
               evidence_item("jurisdiction", JURIS_Q, "CH")])
    result = check(record, bundle, transaction)
    assert record["verdict"] == PERMIT      # der Kern glaubt dem Buendel …
    assert result.checks["V2"]["result"] == "FAIL"   # … der Verifizierer nicht
    assert not result.ok


def test_v2_catches_a_signature_from_the_wrong_source():
    """Die Widerrufs-Aussage traegt die Signatur der Jurisdiktions-Quelle."""
    transaction = tx()
    bundle = aer_bundle([evidence_item("revocation", REVOKED_Q, False,
                                       sign_with="jurisdiction"),
                         evidence_item("jurisdiction", JURIS_Q, "CH")],
                        MANDATE, transaction, T0)
    record = f_ext(MANDATE, transaction, bundle)
    result = check(record, bundle, transaction)
    assert result.checks["V2"]["result"] == "FAIL"


def test_v2_catches_an_unknown_source():
    transaction = tx()
    bundle = aer_bundle([evidence_item("revocation", REVOKED_Q, False),
                         evidence_item("jurisdiction", JURIS_Q, "CH")],
                        MANDATE, transaction, T0)
    record = f_ext(MANDATE, transaction, bundle)
    result = check(record, bundle, transaction, trust=trust_list("jurisdiction"))
    assert result.checks["V2"]["result"] == "FAIL"
    assert "not in the trust list" in result.checks["V2"]["reason"]


def test_v2_rejects_a_keyid_that_points_elsewhere():
    """Die `keyid` grenzt ein; passt sie zu keinem gelisteten Schluessel, faellt das Item —
    auch wenn die Signatur zu einem anderen Schluessel derselben Quelle passen wuerde."""
    transaction = tx()
    bundle = aer_bundle([evidence_item("revocation", REVOKED_Q, False, keyid="elsewhere"),
                         evidence_item("jurisdiction", JURIS_Q, "CH")],
                        MANDATE, transaction, T0)
    record = f_ext(MANDATE, transaction, bundle)
    assert check(record, bundle, transaction).checks["V2"]["result"] == "FAIL"


@pytest.mark.parametrize("broken,fragment", [
    (None, "not an object"),
    ({}, "version is not 1"),
    ({"trust_list_version": 2, "sources": {}}, "version is not 1"),
    ({"trust_list_version": 1, "sources": {}}, "non-empty object"),
    ({"trust_list_version": 1, "sources": {"did:x": {}}}, "no keys"),
    ({"trust_list_version": 1,
      "sources": {"did:x": {"keys": [{"algorithm": "rsa", "public_key": "AA=="}]}}},
     "not ed25519"),
    ({"trust_list_version": 1,
      "sources": {"did:x": {"keys": [{"algorithm": "ed25519", "public_key": "AA=="}]}}},
     "32 base64 bytes"),
])
def test_trust_list_problems(broken, fragment):
    assert fragment in trust_list_problem(broken)


def test_a_valid_trust_list_has_no_problem():
    assert trust_list_problem(TRUST) is None


# ------------------------------------------------------------------------- V3

def test_v3_catches_stale_evidence_in_the_bundle():
    """Das Item ist echt signiert, sein Fenster deckt den Zeitpunkt aber nicht."""
    transaction = tx()
    bundle = aer_bundle([evidence_item("revocation", REVOKED_Q, False,
                                       window=("2026-08-30T10:00:00Z",
                                               "2026-08-30T11:00:00Z")),
                         evidence_item("jurisdiction", JURIS_Q, "CH")],
                        MANDATE, transaction, T0)
    record = f_ext(MANDATE, transaction, bundle)
    result = check(record, bundle, transaction)
    assert result.checks["V3"]["result"] == "FAIL"
    assert "does not cover the decision timestamp" in result.checks["V3"]["reason"]


def test_v3_covers_items_the_kernel_never_read():
    """Eine abgelaufene Beilage, die kein Constraint anspricht, faellt trotzdem auf: das
    Buendel als Ganzes ist nicht mehr das, was vorgelegt wurde."""
    transaction = tx()
    unused = evidence_item("revocation", {"kind": "reputation", "subject": ADDR}, 42,
                           window=("2026-08-30T10:00:00Z", "2026-08-30T11:00:00Z"))
    bundle = aer_bundle([evidence_item("revocation", REVOKED_Q, False),
                         evidence_item("jurisdiction", JURIS_Q, "CH"), unused],
                        MANDATE, transaction, T0)
    record = f_ext(MANDATE, transaction, bundle)
    assert record["verdict"] == PERMIT
    result = check(record, bundle, transaction)
    assert result.checks["V3"]["result"] == "FAIL"
    assert result.checks["V4"]["result"] == "PASS"


# ------------------------------------------------------------------------- V4

def test_v4_catches_a_forged_verdict():
    record, bundle, transaction = good_case()
    forged_core = dict(record["core"], verdict=DENY)
    forged = {"verdict": DENY, "core": forged_core,
              "core_digest": ext_core_digest(forged_core)}
    result = check(forged, bundle, transaction)
    assert result.checks["V4"]["result"] == "FAIL"
    assert "differs from the record" in result.checks["V4"]["reason"]


def test_v4_catches_a_core_that_does_not_match_its_own_digest():
    """Der Record zeigt einen Core mit PERMIT und einen Digest ueber einen anderen. Wer den
    Core liest statt ihn nachzurechnen, liest sonst eine Luege."""
    record, bundle, transaction = good_case()
    inconsistent = dict(record)
    inconsistent["core"] = dict(record["core"], reason="approved by operator")
    result = check(inconsistent, bundle, transaction)
    assert result.checks["V4"]["result"] == "FAIL"
    assert "does not digest the core" in result.checks["V4"]["reason"]


def test_all_four_checks_run_even_when_the_first_fails():
    record, bundle, transaction = good_case()
    bundle["bundle_commit"] = "sha256:" + "0" * 64
    result = check(record, bundle, transaction)
    assert set(result.checks) == {"V1", "V2", "V3", "V4"}
    assert len(result.failures) == 4


# --------------------------------------------------------------- Zwei-Maschinen-Fall

def test_the_record_verifies_from_plain_json_without_the_deciding_objects(tmp_path):
    """Maschine 1 entscheidet, schreibt JSON und verschwindet. Maschine 2 prueft spaeter
    aus den Dateien — ohne die Python-Objekte der Entscheidung, ohne Server, ohne Netz."""
    record, bundle, transaction = good_case()
    blob = json.dumps({"record": record, "bundle": bundle,
                       "mandate": MANDATE, "transaction": transaction},
                      sort_keys=True)
    del record, bundle, transaction

    reloaded = json.loads(blob)
    result = verify_record(reloaded["record"], reloaded["bundle"], reloaded["mandate"],
                           reloaded["transaction"], json.loads(json.dumps(TRUST)))
    assert result.ok is True
    assert result.recomputed_verdict == PERMIT


def test_bundle_commit_is_stable_across_a_json_round_trip():
    _record, bundle, _transaction = good_case()
    reloaded = json.loads(json.dumps(bundle))
    assert compute_bundle_commit(reloaded) == bundle["bundle_commit"]


# ------------------------------------------------------------------------- CLI

def write_case(tmp_path, record, bundle, transaction, trust=TRUST):
    (tmp_path / "decision.json").write_text(json.dumps(
        {"record": record, "bundle": bundle, "mandate": MANDATE,
         "transaction": transaction}), encoding="utf-8")
    (tmp_path / "trust.json").write_text(json.dumps(trust), encoding="utf-8")
    return [str(tmp_path / "decision.json")], str(tmp_path / "trust.json")


def test_cli_passes_on_a_sound_record(tmp_path, capsys):
    record, bundle, transaction = good_case()
    [decision], trust = write_case(tmp_path, record, bundle, transaction)
    code = main(["--input", decision, "--trust-list", trust])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert out.strip().endswith("PASS — recomputed verdict PERMIT")
    assert out.count("PASS") == 5


def test_cli_fails_on_a_tampered_bundle(tmp_path, capsys):
    record, bundle, transaction = good_case()
    bundle["decision_timestamp"] = "2026-08-31T12:00:01Z"
    [decision], trust = write_case(tmp_path, record, bundle, transaction)
    code = main(["--input", decision, "--trust-list", trust])
    assert code == EXIT_FAIL
    assert "FAIL" in capsys.readouterr().out


def test_cli_json_output_carries_every_check(tmp_path, capsys):
    record, bundle, transaction = good_case()
    [decision], trust = write_case(tmp_path, record, bundle, transaction)
    main(["--input", decision, "--trust-list", trust, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert sorted(payload["checks"]) == ["V1", "V2", "V3", "V4"]


def test_cli_single_files_override_the_input_blob(tmp_path, capsys):
    record, bundle, transaction = good_case()
    [decision], trust = write_case(tmp_path, record, bundle, transaction)
    other = tmp_path / "other-transaction.json"
    other.write_text(json.dumps(tx(amount=1)), encoding="utf-8")
    code = main(["--input", decision, "--transaction", str(other), "--trust-list", trust])
    assert code == EXIT_FAIL


def test_cli_reports_missing_inputs(tmp_path, capsys):
    record, bundle, transaction = good_case()
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"record": record, "bundle": bundle}), encoding="utf-8")
    (tmp_path / "trust.json").write_text(json.dumps(TRUST), encoding="utf-8")
    code = main(["--input", str(partial), "--trust-list", str(tmp_path / "trust.json")])
    assert code == EXIT_INPUT
    assert "missing input(s): mandate, transaction" in capsys.readouterr().err


def test_cli_reports_an_unreadable_file(tmp_path, capsys):
    (tmp_path / "trust.json").write_text(json.dumps(TRUST), encoding="utf-8")
    code = main(["--input", str(tmp_path / "nope.json"),
                 "--trust-list", str(tmp_path / "trust.json")])
    assert code == EXIT_INPUT


def test_the_verifier_source_names_no_network_client():
    source = (__import__("pathlib").Path(__file__).resolve().parents[1]
              / "src" / "moltrust_enforce")
    for name in ("verify.py", "cli.py", "_ext_core.py", "evidence.py"):
        text = (source / name).read_text(encoding="utf-8")
        assert "import httpx" not in text
        assert "urllib" not in text


def test_the_verifier_path_loads_no_http_stack():
    """Netzfreiheit am geladenen Code gemessen, nicht am Quelltext.

    `import moltrust_enforce.cli` fuehrt das Paket-`__init__` aus. Solange das den Client
    eager importierte, lagen httpx, socket und ssl im Prozess — in einem Programm, das
    keines davon benutzt. Der Client kommt deshalb erst beim Zugriff (PEP 562).
    """
    source = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src")
    probe = ("import sys; import moltrust_enforce.cli; "
             "print(sorted(m for m in sys.modules "
             "if m.split('.')[0] in {'httpx','httpcore','socket','ssl','urllib'}))")
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env={"PYTHONPATH": source, "PATH": "/usr/bin"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]", proc.stdout


def test_without_httpx_the_client_says_what_is_missing():
    """Ohne den Client-Extra darf kein nacktes `No module named 'httpx'` herausfallen —
    sonst sucht der Leser den Fehler bei sich statt in seiner Installation.

    httpx laesst sich im laufenden Testprozess nicht entfernen, also blockiert ein
    Meta-Path-Finder im Subprozess den Import.
    """
    source = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src")
    probe = (
        "import sys\n"
        "class Block:\n"
        "    def find_module(self, name, path=None): return None\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] == 'httpx':\n"
        "            raise ModuleNotFoundError('No module named httpx', name='httpx')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "import moltrust_enforce\n"
        "try:\n"
        "    moltrust_enforce.EnforceClient\n"
        "except ImportError as exc:\n"
        "    print(exc)\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env={"PYTHONPATH": source, "PATH": "/usr/bin"})
    assert proc.returncode == 0, proc.stderr
    assert "moltrust-enforce[client]" in proc.stdout
    assert "Recomputing and verifying work without it" in proc.stdout


def test_the_client_still_arrives_when_it_is_asked_for():
    """Der faule Import darf die oeffentliche Oberflaeche nicht verkleinern."""
    import moltrust_enforce

    from moltrust_enforce import EnforceClient, Ratification, Verdict, VerifyResult

    assert moltrust_enforce.EnforceClient is EnforceClient
    assert all(cls is not None for cls in (Verdict, VerifyResult, Ratification))
    assert "EnforceClient" in dir(moltrust_enforce)
    with pytest.raises(AttributeError):
        moltrust_enforce.NotAThing


def test_the_committed_example_still_verifies():
    """`examples/aer/` ist die Fassung, die im README steht und die ein Dritter zuerst
    ausprobiert. Faellt sie, ist das Beispiel veraltet und nicht der Code kaputt — beides
    muss auffallen, bevor es jemand anders findet."""
    here = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples" / "aer"
    decision = json.loads((here / "decision.json").read_text(encoding="utf-8"))
    trust = json.loads((here / "trust.json").read_text(encoding="utf-8"))
    result = verify_record(decision["record"], decision["bundle"], decision["mandate"],
                           decision["transaction"], trust)
    assert result.ok is True, result.failures
    assert result.recomputed_verdict == PERMIT


def test_cli_runs_as_a_subprocess(tmp_path):
    """Der Entrypoint muss auch ausserhalb des Testprozesses laufen — das ist die Form, in
    der ein Dritter ihn benutzt."""
    record, bundle, transaction = good_case()
    [decision], trust = write_case(tmp_path, record, bundle, transaction)
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "moltrust_enforce.cli", "--input", decision,
         "--trust-list", trust],
        capture_output=True, text=True, env={"PYTHONPATH": str(source), "PATH": "/usr/bin"})
    assert proc.returncode == EXIT_PASS, proc.stderr
    assert "PASS" in proc.stdout
