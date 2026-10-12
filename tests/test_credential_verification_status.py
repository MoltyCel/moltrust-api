"""The 14 unverifiable credentials are marked, not presented as valid (ops C1).

Structural checks over the source: the DB-backed paths need a database these
jobs do not have. What they hold: the seed marks exactly the 14 the live check
exempts, /identity/verify reports `verifiable`, and the registry-signed
gate_attestation counts no marked credential.
"""
import pathlib
import re

from scripts.credentials_verify import KNOWN_UNVERIFIABLE

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = (ROOT / "migrations/2026-10-12_credential_verification_status.sql").read_text()
MAIN = (ROOT / "app/main.py").read_text()


def test_seed_marks_exactly_the_fourteen():
    ids = re.search(r"WHERE id IN \(([\d,\s]+)\)", SEED).group(1)
    assert {int(x) for x in ids.split(",")} == set(KNOWN_UNVERIFIABLE)


def test_identity_verify_reports_verifiable():
    assert "LEFT JOIN credential_verification_status v ON v.credential_id = c.id" in MAIN
    assert '"verifiable": not c["unverifiable"]' in MAIN


def test_gate_attestation_counts_no_marked_credential():
    q = MAIN[MAIN.index('"SELECT DISTINCT credential_type FROM credentials c "'):]
    q = q[:q.index("did,")]
    assert "NOT EXISTS (SELECT 1 FROM credential_verification_status s" in q
