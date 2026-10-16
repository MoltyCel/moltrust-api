"""The four cohort figures per registration week (ops G6).

No database: `registry_export._psql` is replaced by rows in the shape the
query returns. The query itself is compared against main on the live
database in the job report; here the arithmetic and the empty week are
pinned.
"""
import datetime as dt
from pathlib import Path

from app import kohorten, registry_export

AS_OF = "2026-10-19T08:00:00+00:00"
SLOT = dt.datetime(2026, 10, 19, 8, 0, tzinfo=dt.timezone.utc)   # a Monday


def row(week, bucket="bounty", activated=False, mature=True, returned=False,
        brought=0, before=False):
    return {"did": f"did:moltrust:{id(object()):x}", "bucket": bucket,
            "cohort_week": week, "activated": activated, "mature_7d": mature,
            "returned_7d": returned, "brought": brought,
            "before_telemetry_cutoff": before}


RAW = [
    row("2026-W41", activated=True, returned=True, brought=2),
    row("2026-W41", activated=True, returned=False),
    row("2026-W41", activated=False, returned=True),          # back, but never activated
    row("2026-W41", bucket="own_test", activated=True, returned=True, brought=1),
    row("2026-W42", activated=True, mature=False, returned=False),
    row("2026-W42", bucket="organic"),
]


def by_week(rows):
    return {w["woche"]: w for w in rows}


def test_the_four_figures_with_their_populations():
    w = by_week(kohorten.weeks(RAW, AS_OF))["2026-W41"]
    assert w["registriert"]["wert"] == 3
    assert (w["aktiviert"]["wert"], w["aktiviert"]["von"]) == (2, 3)
    assert (w["zurueck_nach_7_tagen"]["wert"], w["zurueck_nach_7_tagen"]["von"]) == (1, 2)
    assert (w["mitgebracht"]["wert"], w["mitgebracht"]["von"]) == (1, 3)
    assert w["stichtag"] == AS_OF


def test_own_agents_are_not_counted():
    """The same allow-list as the registry proof: own_test never counts."""
    total = sum(w["registriert"]["wert"] for w in kohorten.weeks(RAW, AS_OF))
    assert total == 5


def test_a_young_activation_is_not_in_the_return_population():
    w = by_week(kohorten.weeks(RAW, AS_OF))["2026-W42"]
    assert w["aktiviert"]["wert"] == 1
    assert w["zurueck_nach_7_tagen"]["von"] == 0


def test_a_week_without_registration_is_zeros_and_no_error():
    """Silence check: an empty week is a row of zeros, not a gap."""
    raw = [row("2026-W38", activated=True), row("2026-W41")]
    weeks = by_week(kohorten.weeks(raw, AS_OF))
    assert list(weeks) == ["2026-W38", "2026-W39", "2026-W40", "2026-W41"]
    for k in ("2026-W39", "2026-W40"):
        w = weeks[k]
        assert [w["registriert"]["wert"], w["aktiviert"]["wert"],
                w["zurueck_nach_7_tagen"]["wert"], w["mitgebracht"]["wert"]] == [0, 0, 0, 0]


def test_no_registration_at_all_is_zeros_and_no_error():
    weeks = kohorten.weeks([], AS_OF, dt.date(2026, 10, 5), dt.date(2026, 10, 18))
    assert [w["woche"] for w in weeks] == ["2026-W41", "2026-W42"]
    assert all(w["registriert"]["wert"] == 0 for w in weeks)
    line = kohorten.digest_line(weeks, SLOT)
    assert "W42 0 · 0/0 · 0/0 · 0/0 — W41 0 · 0/0 · 0/0 · 0/0" in line


def test_the_monday_line_names_the_two_completed_weeks():
    def fetch(as_of, since, until):
        assert (since, until) == (dt.date(2026, 10, 5), dt.date(2026, 10, 18))
        return {"wochen": kohorten.weeks(RAW, as_of, since, until)}
    lines = kohorten.digest_lines(SLOT, fetch=fetch)
    assert len(lines) == 1
    assert lines[0].startswith("Kohorten (registriert · aktiviert · zurück nach 7 T")
    assert "Stichtag 19.10. 08:00Z" in lines[0]
    assert "W42 2 · 1/2 · 0/0 · 0/2 — W41 3 · 2/3 · 1/2 · 1/3" in lines[0]


def test_the_line_comes_only_on_monday_at_eight():
    def fetch(*a):
        raise AssertionError("no database outside the Monday slot")
    assert kohorten.digest_lines(SLOT.replace(hour=20), fetch=fetch) == []
    assert kohorten.digest_lines(SLOT + dt.timedelta(days=1), fetch=fetch) == []


def test_the_block_reads_the_registry_proof_query(monkeypatch):
    seen = {}

    def fake(as_of, sql_path=None):
        seen["sql"] = sql_path or registry_export.SQL_PATH
        return RAW
    monkeypatch.setattr(registry_export, "_psql", fake)
    b = kohorten.block(AS_OF)
    assert Path(seen["sql"]).name == "registry_export.sql"
    assert b["quelle"] == "app/sql/registry_export.sql"
    assert set(b["definitionen"]) == {"registriert", "aktiviert",
                                      "zurueck_nach_7_tagen", "mitgebracht"}


def test_the_cohort_columns_stay_out_of_the_public_rows(monkeypatch):
    """registry-proof.json publishes a fixed field list; the new columns are
    read by the cohort module only."""
    anchor = {"credential_id": 1, "credential_type": "AgentTrustCredential",
              "issued_at": "2026-10-10T00:00:00.000000",
              "merkle_proof": {"root": "ab", "leaf": "cd"},
              "anchor_tx": "0x1", "anchor_block": 1}
    raw = [dict(r, registered="2026-10-10", anchors=[dict(anchor)],
                activated_endpoint_prefix=None, last_independent_call_day=None,
                did=f"did:moltrust:{i}")
           for i, r in enumerate(RAW) if r["bucket"] != "own_test"]
    monkeypatch.setattr(registry_export, "_psql", lambda *a, **k: raw)
    doc = registry_export.build(AS_OF)
    agents = [r for r in doc["rows"] if r.get("kind") == "agent"]
    assert len(agents) == 5
    for r in agents:
        assert set(r) == set(registry_export.AGENT_ROW_FIELDS)


def test_the_query_derives_every_cohort_column():
    sql = (Path(registry_export.SQL_PATH)).read_text()
    for col in ("cohort_week", "mature_7d", "returned_7d", "brought"):
        assert f"AS {col}" in sql
    assert "FROM agent_referrals" in sql
    # One list of prescribed endpoints for activation and return alike.
    assert sql.count("'/identity/verify/%'") == 1


def test_the_markdown_table_has_a_row_per_week():
    b = {"stichtag": AS_OF, "quelle": kohorten.SOURCE, "definitionen": {"a": "b"},
         "wochen": kohorten.weeks(RAW, AS_OF)}
    md = kohorten.markdown(b)
    assert "| 2026-W41 | 3 | 2 von 3 | 1 von 2 | 1 von 3 | 0 |" in md
    assert "| 2026-W42 | 2 | 1 von 2 | 0 von 0 | 0 von 2 | 0 |" in md
