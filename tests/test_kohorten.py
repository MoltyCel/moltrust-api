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


def referred(n=0, **flags):
    """`n` referral facts in the shape the query hands them over (ops G2)."""
    base = {"self": False, "mutual": False, "unknown": False,
            "revoked": False, "activated": False}
    return [{"did": f"did:moltrust:ref{i}", **base, **flags} for i in range(n)]


def row(week, bucket="bounty", activated=False, mature=True, returned=False,
        brought=0, referred_facts=None, before=False):
    return {"did": f"did:moltrust:{id(object()):x}", "bucket": bucket,
            "cohort_week": week, "activated": activated, "mature_7d": mature,
            "returned_7d": returned,
            "referred": referred(brought) if referred_facts is None else referred_facts,
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


def test_a_ring_is_not_somebody_brought():
    """A refers B and B refers A: the week has a referral row and no referral.

    The figure comes from app/referral.py, which is where the rule that drops a
    mutual pair lives; the cohort line does not count rows of its own.
    """
    ring = [row("2026-W41", referred_facts=referred(1, mutual=True))]
    assert by_week(kohorten.weeks(ring, AS_OF))["2026-W41"]["mitgebracht"]["wert"] == 0
    straight = [row("2026-W41", referred_facts=referred(1))]
    assert by_week(kohorten.weeks(straight, AS_OF))["2026-W41"]["mitgebracht"]["wert"] == 1


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
    assert "W42 0 · 0/0 · 0/0 · 0/0 (erfasst ab 16.10. 18:49Z) — W41 0 · 0/0 · 0/0 · 0/0 (erfasst ab 16.10. 18:49Z)" in line


def test_the_monday_line_names_the_two_completed_weeks():
    def fetch(as_of, since, until):
        assert (since, until) == (dt.date(2026, 10, 5), dt.date(2026, 10, 18))
        return {"wochen": kohorten.weeks(RAW, as_of, since, until)}
    lines = kohorten.digest_lines(SLOT, fetch=fetch)
    assert len(lines) == 1
    assert lines[0].startswith("Kohorten (registriert · aktiviert · zurück nach 7 T")
    assert "Stichtag 19.10. 08:00Z" in lines[0]
    assert "W42 2 · 1/2 · 0/0 · 0/2 (erfasst ab 16.10. 18:49Z) — W41 3 · 2/3 · 1/2 · 1/3 (erfasst ab 16.10. 18:49Z)" in lines[0]


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
    """registry-proof.json publishes a fixed field list. The week, the maturity
    flag, the return flag and the referral facts are read by the cohort module
    and stay out of the file; the two referral counts are on the list (ops G2)."""
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
    for col in ("cohort_week", "mature_7d", "returned_7d", "referred"):
        assert f"AS {col}" in sql
    assert "FROM agent_referrals" in sql
    # The query hands over the facts of each referral and counts nothing
    # itself, so the rule cannot be stated twice (ops G2).
    for fact in ("'self'", "'mutual'", "'unknown'", "'revoked'", "'activated'"):
        assert fact in sql
    # One list of prescribed endpoints for activation and return alike.
    assert sql.count("'/identity/verify/%'") == 1


def test_the_markdown_table_has_a_row_per_week():
    b = {"stichtag": AS_OF, "quelle": kohorten.SOURCE, "definitionen": {"a": "b"},
         "wochen": kohorten.weeks(RAW, AS_OF)}
    md = kohorten.markdown(b)
    assert "| 2026-W41 | 3 | 2 von 3 | 1 von 2 | 1 von 3 (erfasst ab 16.10. 18:49Z) | 0 |" in md
    assert "| 2026-W42 | 2 | 1 von 2 | 0 von 0 | 0 von 2 (erfasst ab 16.10. 18:49Z) | 0 |" in md


# --- Capture start of referrals (ops KM1) ----------------------------------

LATER = dt.datetime(2026, 11, 2, 8, 0, tzinfo=dt.timezone.utc)      # a Monday


def test_weeks_before_the_capture_start_carry_it():
    """Trigger: W41 and W42 began before 16.10. 18:49:03Z."""
    weeks = by_week(kohorten.weeks(RAW, AS_OF))
    for k in ("2026-W41", "2026-W42"):
        assert weeks[k]["mitgebracht"]["erfasst_seit"] == "2026-10-16T18:49:03Z"


def test_a_week_that_starts_after_the_capture_start_is_not_marked():
    """Silence: W43 begins on 19.10., after the capture start."""
    raw = [row("2026-W43", activated=True), row("2026-W44")]
    weeks = kohorten.weeks(raw, LATER.isoformat())
    assert [w["mitgebracht"]["erfasst_seit"] for w in weeks] == [None, None]
    line = kohorten.digest_line(weeks, LATER)
    assert "erfasst ab" not in line
    md = kohorten.markdown({"stichtag": LATER.isoformat(), "quelle": kohorten.SOURCE,
                            "definitionen": {}, "wochen": weeks})
    assert "erfasst ab" not in md


def test_the_week_across_the_capture_start_is_marked_and_the_next_is_not():
    """The boundary: W42 (12.10.) holds the start, W43 (19.10.) is clean."""
    weeks = by_week(kohorten.weeks([], LATER.isoformat(), dt.date(2026, 10, 12),
                                   dt.date(2026, 10, 19)))
    assert weeks["2026-W42"]["mitgebracht"]["erfasst_seit"] is not None
    assert weeks["2026-W43"]["mitgebracht"]["erfasst_seit"] is None


def test_the_mark_changes_no_other_figure():
    """Criterion 4: zurueck nach 7 T and the rest are what they were."""
    weeks = by_week(kohorten.weeks(RAW, AS_OF))
    w = weeks["2026-W41"]
    assert (w["registriert"]["wert"], w["aktiviert"], w["zurueck_nach_7_tagen"]) == \
        (3, {"wert": 2, "von": 3}, {"wert": 1, "von": 2})
    assert (w["mitgebracht"]["wert"], w["mitgebracht"]["von"]) == (1, 3)


def test_a_missing_week_in_the_line_is_marked_too():
    line = kohorten.digest_line([], SLOT)
    assert line.count(" (erfasst ab 16.10. 18:49Z)") == 2


def test_the_definition_names_the_capture_start(monkeypatch):
    monkeypatch.setattr(registry_export, "_psql", lambda as_of: [])
    b = kohorten.block(AS_OF, dt.date(2026, 10, 12))
    assert "Erfasst seit 16.10.2026 18:49:03Z" in b["definitionen"]["mitgebracht"]


# ── the Moltbook channel line (ops AMB-1) ──
#
# Same rows, filtered to referrer = the Ambassador AND source = moltbook, so the
# channel's figures use the cohort definitions above and no second rule.

AMB = kohorten.MOLTBOOK_REFERRER_DID


def mrow(week, referrer=AMB, source="moltbook", **kw):
    return dict(row(week, **kw), referrer_did=referrer, source=source)


CHANNEL_RAW = [
    mrow("2026-W41", activated=True, returned=True),          # counts, activated, back
    mrow("2026-W41", activated=False),                         # counts, not activated
    mrow("2026-W41", referrer=None, activated=True),           # source only: not the channel
    mrow("2026-W41", source="mcp", activated=True),            # referrer only: not the channel
    mrow("2026-W41", referrer="did:moltrust:aaaaaaaaaaaaaaaa", activated=True),
    mrow("2026-W41", bucket="own_test", activated=True),       # ours: never counted
    mrow("2026-W42"),                                          # counts, nothing activated
]


def channel_fetch(as_of, since, until):
    return {"wochen": kohorten.weeks(CHANNEL_RAW, as_of, since, until),
            "kanaele": {"Moltbook": kohorten.weeks(
                kohorten.channel_rows(CHANNEL_RAW, "Moltbook"), as_of, since, until)}}


def test_the_channel_takes_both_the_referrer_and_the_source():
    rows = kohorten.channel_rows(CHANNEL_RAW, "Moltbook")
    assert len(rows) == 4        # three counted plus own_test, which weeks() drops
    w = by_week(kohorten.weeks(rows, AS_OF))
    assert w["2026-W41"]["registriert"]["wert"] == 2
    assert (w["2026-W41"]["aktiviert"]["wert"], w["2026-W41"]["aktiviert"]["von"]) == (1, 2)
    assert (w["2026-W41"]["zurueck_nach_7_tagen"]["wert"],
            w["2026-W41"]["zurueck_nach_7_tagen"]["von"]) == (1, 1)


def test_the_monday_digest_carries_the_moltbook_line():
    lines = kohorten.digest_lines(SLOT, fetch=channel_fetch)
    assert len(lines) == 2
    assert lines[1] == ("Kanal Moltbook (registriert · aktiviert · zurück nach 7 T · "
                        "Kosten je Aktivierung), Kosten 0 USDC: "
                        "W42 1 · 0/1 · 0/0 · — — W41 2 · 1/2 · 1/1 · 0 USDC")


def test_no_activation_is_a_dash_not_a_division():
    assert kohorten.cost_per_activation(0, 0) == "—"
    assert kohorten.cost_per_activation(0, 3) == "0 USDC"
    assert kohorten.cost_per_activation(6, 3) == "2 USDC"


def test_a_channel_without_registrations_is_zeros_and_dashes():
    def fetch(as_of, since, until):
        return {"wochen": [], "kanaele": {"Moltbook": []}}
    line = kohorten.digest_lines(SLOT, fetch=fetch)[1]
    assert line.endswith("W42 0 · 0/0 · 0/0 · — — W41 0 · 0/0 · 0/0 · —")


def test_the_block_carries_the_channel(monkeypatch):
    monkeypatch.setattr(registry_export, "_psql", lambda *a, **k: CHANNEL_RAW)
    b = kohorten.block(AS_OF)
    assert set(b["kanaele"]) == {"Moltbook"}
    total = sum(w["registriert"]["wert"] for w in b["kanaele"]["Moltbook"])
    assert total == 3


def test_the_query_hands_over_source_and_referrer():
    sql = (Path(registry_export.SQL_PATH)).read_text()
    assert "AS source" in sql and "AS referrer_did" in sql
    assert "LEFT JOIN agent_source" in sql
    assert "byref.recorded_at < :'AS_OF'" in sql
