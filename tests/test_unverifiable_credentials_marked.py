"""Fourteen credentials that do not verify are marked, not deleted, and not vouched for.

Found 2026-10-12: ids 925-937 and 940, #key-1, issued 28.02.-23.03.2026, verify
in no tested variant against the published DID key. Until this change the
registry-signed gate_attestation listed their type for a DID that held nothing
else of that type, and GET /identity/verify/{did} listed them like any other.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app" / "main.py").read_text()
SQL = (ROOT / "migrations" / "2026-10-12_credential_verification_status.sql").read_text()

GATE = MAIN[MAIN.index("cred_rows = await conn.fetch("):]
GATE = GATE[:GATE.index("public_key = key_row")]
VERIFY = MAIN[MAIN.index('@app.get("/identity/verify/{did}")'):]
VERIFY = VERIFY[:VERIFY.index("# A lookup by anyone is not activity of the agent")]


def test_the_signed_attestation_leaves_marked_credentials_out():
    assert "NOT EXISTS (SELECT 1 FROM credential_verification_status v" in GATE
    assert "v.credential_id = c.id" in GATE


def test_the_attestation_keeps_its_other_conditions():
    for cond in ("c.subject_did = $1", "c.revoked IS FALSE", "c.expires_at > now()"):
        assert cond in GATE


def test_verify_lists_them_and_says_so():
    assert "LEFT JOIN credential_verification_status v ON v.credential_id = c.id" in VERIFY
    assert '"verifiable": c["verification_status"] is None' in VERIFY
    assert "not verifiable with the current issuer keys" in VERIFY


def test_verify_does_not_drop_them():
    """Hiding them would be a second false statement: they were issued."""
    q = VERIFY[VERIFY.index("creds = await conn.fetch("):VERIFY.index("result[\"credentials\"]")]
    assert "credential_verification_status v ON" in q
    assert "NOT EXISTS" not in q and "v.credential_id IS NULL" not in q


def test_the_table_is_created_at_startup():
    assert "await ensure_credential_verification_status_table(conn)" in MAIN
    assert "CREATE TABLE IF NOT EXISTS credential_verification_status" in MAIN


def test_the_migration_marks_exactly_the_fourteen():
    ids = re.search(r"WHERE id IN \(([^)]*)\)", SQL).group(1)
    assert sorted(int(x) for x in ids.split(",")) == [925, 926, 927, 928, 929, 930, 931,
                                                      932, 933, 934, 935, 936, 937, 940]


def test_the_migration_neither_deletes_nor_revokes():
    body = "\n".join(line for line in SQL.splitlines() if not line.lstrip().startswith("--"))
    assert "DELETE" not in body.upper()
    assert "UPDATE CREDENTIALS" not in body.upper()
    assert "revoked" not in body.lower()


def test_the_migration_is_idempotent():
    assert "CREATE TABLE IF NOT EXISTS" in SQL
    assert "ON CONFLICT (credential_id) DO NOTHING" in SQL


def test_the_nine_before_the_first_commit_say_so_without_a_guess():
    assert "WHEN id <= 935" in SQL
    assert "not on record" in SQL
