"""The CI copy of trust_score_cache stays identical to the migration.

.github/scripts/ci_init_db.sh exists so that two jobs cannot drift apart over
the same schema. For trust_score_cache it holds a copy of the DDL instead of
running migrations/001_swarm_phase1.sql, because that migration also carries
`endorsements` — a table init_db.sql has already created in another shape, so
the migration's CREATE TABLE IF NOT EXISTS is skipped and the index after it
fails on a column (expires_at) the existing table has not got.

A copy is the thing that file was written to avoid, so it gets the guard the
include would not have needed: change the column list in the migration without
changing the copy, and this goes red. Herkunft: ops CI-1, 2026-10-17, after
132 errors in the first whole-suite run, three of them "relation
trust_score_cache does not exist".
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "001_swarm_phase1.sql"
CI_SCHEMA = ROOT / ".github" / "scripts" / "ci_init_db.sh"


def spalten(sql: str, tabelle: str) -> list[tuple[str, str]]:
    """(name, type) per column of `tabelle`, from a CREATE TABLE in `sql`."""
    m = re.search(
        r"CREATE TABLE (?:IF NOT EXISTS )?" + tabelle + r"\s*\((.*?)\n\s*\)",
        sql, re.DOTALL | re.IGNORECASE)
    assert m, f"kein CREATE TABLE fuer {tabelle} gefunden"
    paare = []
    for zeile in m.group(1).splitlines():
        zeile = zeile.strip().rstrip(",")
        if not zeile or zeile.startswith("--"):
            continue
        teile = zeile.split()
        paare.append((teile[0], teile[1].upper()))
    return paare


def test_die_ci_kopie_nennt_dieselben_spalten_wie_die_migration():
    aus_migration = spalten(MIGRATION.read_text(), "trust_score_cache")
    aus_ci = spalten(CI_SCHEMA.read_text(), "trust_score_cache")
    assert aus_ci == aus_migration, (
        "trust_score_cache steht in .github/scripts/ci_init_db.sh und in "
        "migrations/001_swarm_phase1.sql. Die beiden sind auseinander "
        f"gelaufen: Migration {aus_migration}, CI {aus_ci}."
    )


def test_die_migration_traegt_die_tabelle_noch():
    # Wird trust_score_cache in der Migration umbenannt oder verschoben,
    # soll das hier auffallen und nicht in einem Sammlungsfehler der Suite.
    assert "trust_score_cache" in MIGRATION.read_text()


def test_der_vergleich_wuerde_eine_abweichung_sehen():
    # Negativfall: ohne ihn prueft der Test oben nur, dass zwei gleiche
    # Zeichenketten gleich sind, und nicht, dass spalten() etwas sieht.
    verbogen = CI_SCHEMA.read_text().replace("endorser_count    INTEGER",
                                             "endorser_count    BIGINT")
    assert spalten(verbogen, "trust_score_cache") != spalten(
        MIGRATION.read_text(), "trust_score_cache")
