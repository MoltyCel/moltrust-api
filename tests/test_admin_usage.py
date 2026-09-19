"""GET /admin/usage — counting rules, auth, and window validation.

Reads the source rather than importing app.main: the assertions are about which
table a number comes from and which SQL shape produces it, and importing drags
in the whole application to look at one endpoint.
"""
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
SRC = MAIN.read_text()

START = SRC.index('@app.get("/admin/usage")')
# /admin/funnel is the next endpoint in the file. The boundary is named so the
# block stays this endpoint only — a wider block would let a neighbour's code
# satisfy or break assertions about which table this one reads.
END = SRC.index('@app.get("/admin/funnel")')
BLOCK = SRC[START:END]


def _executable(block: str) -> str:
    """The block with its docstring and comments removed.

    Assertions about what the endpoint *does* must not be satisfiable — or
    broken — by what its prose says. The first version of this file failed
    because a comment contained the word "drop" and the docstring named
    request_log while explaining why it is not read.
    """
    lines, in_doc = [], False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""'):
            # A one-line docstring opens and closes on the same line.
            if not (len(stripped) > 3 and stripped.endswith('"""')):
                in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        lines.append(line.split("  # ")[0])
    return "\n".join(lines)


CODE = _executable(BLOCK)


class TestAuthAndWindow:
    def test_requires_an_admin_session(self):
        """Same gate as every other admin endpoint, not a new one."""
        assert "_get_admin_session(request)" in BLOCK

    def test_session_check_precedes_any_query(self):
        assert BLOCK.index("_get_admin_session") < BLOCK.index("db_pool.acquire")

    def test_only_the_three_offered_windows_are_accepted(self):
        """An open integer would let a caller ask for 3650 days and get a
        scan of the whole table back."""
        assert "if days not in (7, 30, 90):" in BLOCK
        assert 'HTTPException(400, "days must be one of 7, 30, 90")' in BLOCK

    def test_missing_database_is_503_not_a_traceback(self):
        assert 'HTTPException(503, "Database unavailable")' in BLOCK

    def test_is_read_only(self):
        for verb in ("INSERT", "UPDATE ", "DELETE", "DROP", "ALTER", "TRUNCATE"):
            assert verb not in CODE.upper(), f"{verb} in a read-only endpoint"


class TestCountingRules:
    """The two ways this panel could quietly lie."""

    def test_distinct_identities_do_not_come_from_usage_daily(self):
        """usage_daily.distinct_dids is stored per
        (day, endpoint, status, source, class). Summing it counts one agent
        once per group it appears in — with the current data that turns 10
        agents into 35."""
        assert "sum(distinct_dids)" not in CODE
        assert "SUM(distinct_dids)" not in CODE

    def test_distinct_identities_come_from_usage_daily_keys(self):
        """One row per (day, key), so a range can be counted honestly."""
        assert "count(DISTINCT key_fp)" in BLOCK
        assert "count(DISTINCT did)" in BLOCK
        assert "FROM usage_daily_keys" in BLOCK

    def test_settled_payments_come_from_the_payments_rollup(self):
        """A 200 proves the gate opened, not that money moved."""
        assert "usage_daily_payments" in BLOCK
        assert "sum(settled_payments)" in BLOCK

    def test_conversion_is_challenges_against_settlements(self):
        assert "conversion_pct" in BLOCK
        assert "settled / challenges" in BLOCK

    def test_conversion_does_not_divide_by_zero(self):
        assert "if challenges else 0.0" in BLOCK

    def test_two_hundreds_are_scoped_to_endpoints_that_gate(self):
        """Counting every 200 as a completed payment would fold in every free
        endpoint on the API."""
        assert "WITH gated AS (" in BLOCK
        assert "status_code = 402" in BLOCK


class TestHonestCoverage:
    def test_reports_how_much_data_the_window_actually_has(self):
        """usage_daily starts 2026-08-15, so a 90-day window is wider than the
        data. Saying so beats drawing a flat line back to June."""
        assert '"days_with_data"' in BLOCK
        assert '"partial"' in BLOCK
        assert "days_with_data < days" in BLOCK

    def test_identity_series_reports_its_own_start(self):
        """usage_daily_keys began when the instrumentation shipped; a short
        series is not a drop in usage."""
        assert BLOCK.count('"first_day"') >= 2

    def test_per_day_average_uses_days_with_data(self):
        assert "total_requests / days_with_data" in BLOCK
        assert "if days_with_data else 0" in BLOCK

    def test_source_of_each_derived_number_is_stated(self):
        assert BLOCK.count('"note"') >= 3


class TestNoRequestLog:
    def test_does_not_read_request_log(self):
        """request_log keeps 30 days. Answering a 90-day question from it
        returns 30 days of data under a 90-day label."""
        assert "request_log" not in CODE


class TestResponseShape:
    def test_carries_the_sections_the_panel_renders(self):
        for key in ('"by_class"', '"daily"', '"identities"', '"payments"',
                    '"priced_endpoints"', '"top_endpoints"', '"by_status"',
                    '"coverage"', '"totals"'):
            assert key in BLOCK, f"missing {key}"

    def test_shares_do_not_divide_by_zero(self):
        assert "if total_requests else 0.0" in BLOCK

    def test_usdc_is_a_decimal_string_not_a_float(self):
        """Money rendered through a float rounds the values the panel exists
        to show."""
        assert "f\"{pay['usdc'] or 0:.6f}\"" in BLOCK


class TestWindowBoundary:
    def test_window_is_exclusive_at_the_far_end(self):
        """`>= CURRENT_DATE - 7` returns eight days: the seven before today and
        today. A seven-day window has to be seven days, or every per-day
        average is quietly divided by the wrong number."""
        assert ">= (CURRENT_DATE - $1::int)" not in CODE
        assert "> (CURRENT_DATE - $1::int)" in CODE

    def test_every_query_uses_the_same_boundary(self):
        """Mixed boundaries would put the payment series and the request
        series on different windows."""
        assert CODE.count("> (CURRENT_DATE - $1::int)") >= 8
