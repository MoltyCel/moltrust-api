"""The funnel: bucket mapping, goal arithmetic, and GET /admin/funnel.

The endpoint assertions read the source rather than importing app.main — same
reason as test_admin_usage.py: the claims are about which table a number comes
from, and importing drags in the whole application to look at one handler.
"""
import datetime as _dt
from pathlib import Path

from app.funnel import (
    BUCKET_ORDER,
    FUNNEL_EPOCH,
    FUNNEL_GOAL,
    FUNNEL_GOAL_DAYS,
    PLATFORM_BUCKETS,
    bucket_of,
    build_bucket_function_sql,
    goal_progress,
    telegram_line,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "app" / "main.py").read_text()

START = SRC.index('@app.get("/admin/funnel")')
END = SRC.index('@app.get("/admin/dashboard/x402")')
BLOCK = SRC[START:END]


def _executable(block: str) -> str:
    """The block with its docstring and comments removed.

    Assertions about what the endpoint does must not be satisfiable by what its
    prose says: this docstring names payment_events while explaining a limit.
    """
    lines, in_doc = [], False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""'):
            if not (len(stripped) > 3 and stripped.endswith('"""')):
                in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        lines.append(line.split("  # ")[0])
    return "\n".join(lines)


CODE = _executable(BLOCK)


class TestBucketMapping:
    def test_every_mapped_bucket_is_displayable(self):
        """A bucket the panel never renders would silently swallow rows."""
        for bucket in PLATFORM_BUCKETS.values():
            assert bucket in BUCKET_ORDER

    def test_unknown_platform_falls_through_to_other(self):
        assert bucket_of("ownify") == "other"
        assert bucket_of("moltbook") == "other"

    def test_other_is_last_so_the_residue_reads_as_residue(self):
        assert BUCKET_ORDER[-1] == "other"

    def test_missing_platform_is_other_not_a_crash(self):
        assert bucket_of(None) == "other"
        assert bucket_of("") == "other"

    def test_matching_is_case_and_whitespace_insensitive(self):
        assert bucket_of("  ClawHub ") == "clawhub"

    def test_openclaw_is_not_quietly_folded_into_clawhub(self):
        """They are different sources. Folding them would invent a number.

        openclaw is the org, clawhub the registry. One agent registered as
        `openclaw` in April, before either listing existed; counting it as a
        clawhub acquisition would overstate what the listing delivered.
        """
        assert bucket_of("openclaw") == "other"

    def test_no_sdk_aliases_are_guessed(self):
        """No agent has ever registered as crewai or langchain.

        Mapping them now would move future rows out of `other` on no evidence.
        When an SDK registration actually arrives, the raw value is visible in
        the panel and the mapping can be extended against it.
        """
        assert bucket_of("crewai") == "other"
        assert bucket_of("langchain") == "other"


class TestGeneratedMigration:
    MIGRATION = ROOT / "migrations" / "2026-09-19_funnel_platform_bucket.sql"

    def test_migration_matches_the_generator(self):
        """One mapping, two consumers. Drift here is drift in the numbers.

        The endpoint and the nightly Telegram digest both call the SQL
        function; only this assertion keeps it equal to the Python one.
        """
        assert self.MIGRATION.read_text().endswith(build_bucket_function_sql())

    def test_generated_sql_covers_every_mapping(self):
        sql = build_bucket_function_sql()
        for raw, bucket in PLATFORM_BUCKETS.items():
            assert f"WHEN '{raw}' THEN '{bucket}'" in sql

    def test_generated_sql_handles_null(self):
        """agents.platform is nullable; a NULL must bucket, not vanish."""
        assert "coalesce(p, '')" in build_bucket_function_sql()


class TestGoalProgress:
    def test_epoch_day_counts_as_one_day_elapsed(self):
        """Day one of the window is not zero days spent."""
        assert goal_progress(0, FUNNEL_EPOCH)["days_elapsed"] == 1

    def test_percent_is_against_the_target_not_against_elapsed_time(self):
        assert goal_progress(25, FUNNEL_EPOCH)["percent"] == 25.0

    def test_on_pace_marker_tracks_elapsed_time(self):
        """Halfway through the window, a straight line stands at half."""
        mid = FUNNEL_EPOCH + _dt.timedelta(days=FUNNEL_GOAL_DAYS // 2 - 1)
        assert goal_progress(0, mid)["on_pace"] == 50.0

    def test_on_pace_does_not_exceed_the_target_after_the_deadline(self):
        past = FUNNEL_EPOCH + _dt.timedelta(days=FUNNEL_GOAL_DAYS + 30)
        assert goal_progress(0, past)["on_pace"] == float(FUNNEL_GOAL)

    def test_required_per_day_is_none_once_the_window_closed(self):
        """A rate for a window with no days left is not a number."""
        past = FUNNEL_EPOCH + _dt.timedelta(days=FUNNEL_GOAL_DAYS)
        assert goal_progress(10, past)["required_per_day"] is None

    def test_required_per_day_is_zero_when_the_target_is_met(self):
        assert goal_progress(FUNNEL_GOAL, FUNNEL_EPOCH)["required_per_day"] == 0.0

    def test_overshooting_the_target_does_not_go_negative(self):
        p = goal_progress(FUNNEL_GOAL + 20, FUNNEL_EPOCH)
        assert p["required_per_day"] == 0.0
        assert p["percent"] > 100

    def test_deadline_is_inclusive_of_the_epoch_day(self):
        """90 days starting on the 19th ends on day 90, not day 91."""
        deadline = _dt.date.fromisoformat(goal_progress(0, FUNNEL_EPOCH)["deadline"])
        assert (deadline - FUNNEL_EPOCH).days == FUNNEL_GOAL_DAYS - 1


class TestTelegramLine:
    def test_shape_matches_the_agreed_wording(self):
        line = telegram_line(3, [("clawhub", 2), ("sdk", 1)])
        assert line == "Funnel (7d): +3 registrations by clawhub 2, sdk 1"

    def test_quiet_week_still_reports_a_number(self):
        """A missing line reads as a broken job; +0 reads as a quiet week."""
        assert telegram_line(0, []) == "Funnel (7d): +0 registrations"

    def test_empty_buckets_are_not_listed(self):
        assert telegram_line(1, [("clawhub", 1), ("smithery", 0)]).endswith("by clawhub 1")


class TestEndpoint:
    def test_requires_an_admin_session(self):
        assert "_get_admin_session(request)" in BLOCK

    def test_guards_a_missing_pool_before_querying(self):
        pool_guard = CODE.index("if not db_pool:")
        assert pool_guard < CODE.index("db_pool.acquire()")

    def test_window_starts_at_the_epoch_not_at_a_rolling_offset(self):
        """A rolling window would drop the early cohort as the weeks pass."""
        assert "created_at >= $1::date" in CODE
        assert "epoch" in CODE

    def test_cohort_uses_left_joins(self):
        """An agent that did nothing after registering is the important row.

        An inner join would silently delete exactly the population the funnel
        exists to measure.
        """
        assert CODE.count("LEFT JOIN first_call") == 1
        assert CODE.count("LEFT JOIN first_vc") == 1
        assert CODE.count("LEFT JOIN first_pay") == 1

    def test_first_credential_excludes_the_automatic_registration_vc(self):
        """Registration issues an AgentTrustCredential within the second.

        86 of 90 land inside a minute of the agent's own created_at. Counted as
        the first credential, the milestone would be a constant zero for nearly
        every agent and would measure nothing at all.
        """
        assert "c.issued_at >= a.created_at + ($2::int * INTERVAL '1 second')" in CODE
        assert "REGISTRATION_VC_GRACE_SECONDS" in CODE

    def test_the_vc_exclusion_is_disclosed_in_the_response(self):
        """A number whose definition is not shipped with it invites misreading."""
        assert "vc_grace_seconds" in CODE

    def test_payments_are_attributed_by_did_first_then_wallet(self):
        assert "coalesce(p.did, a.did)" in CODE

    def test_wallet_match_is_case_insensitive(self):
        """Checksummed and lowercase hex are the same address.

        payment_events stores what the chain returned; agents.wallet_address
        stores what the agent typed. A plain = would miss every real match.
        """
        assert "lower(a.wallet_address) = lower(p.from_address)" in CODE

    def test_payment_linkage_coverage_is_reported(self):
        """An empty payment column must be readable as missing linkage."""
        assert '"linkage"' in CODE
        assert "attributable" in CODE

    def test_bucketing_goes_through_the_shared_sql_function(self):
        """Not a CASE inlined here, which would drift from the digest."""
        assert "funnel_platform_bucket(" in CODE
        assert "WHEN 'clawhub'" not in CODE

    def test_raw_platform_values_are_reported_beside_the_bucket(self):
        """A bucket that hides what it swallowed cannot be audited."""
        assert '"raw"' in CODE

    def test_first_call_censoring_is_checked_not_assumed(self):
        """usage_daily_keys starts 2026-09-14.

        If that start ever moved past the epoch, agents would be reported as
        never having called when the rollup simply did not exist yet.
        """
        assert "first_call_censored" in CODE
        assert "usage_daily_keys" in CODE

    def test_revoked_agents_are_excluded_like_everywhere_else(self):
        """/stats and the nightly digest already count `revoked_at IS NULL`.

        A funnel on a different population would put two numbers for the same
        day in front of the same reader.
        """
        assert CODE.count("revoked_at IS NULL") == 4

    def test_the_revoked_exclusion_is_counted_not_silent(self):
        assert "revoked_excluded" in CODE

    def test_cohort_query_is_bounded(self):
        assert "LIMIT 500" in CODE

    def test_does_not_read_request_log(self):
        """30-day retention would answer a since-the-epoch question wrongly."""
        assert "request_log" not in CODE


class TestDigestLine:
    DIGEST = (ROOT / "scripts" / "daily_stats.sh").read_text()

    def test_the_line_is_actually_in_the_message(self):
        """A variable that is computed and never interpolated sends nothing."""
        assert "$FUNNEL_LINE" in self.DIGEST

    def test_digest_and_panel_share_the_bucket_function(self):
        assert "funnel_platform_bucket(platform)" in self.DIGEST

    def test_digest_uses_the_same_population_as_the_panel(self):
        """Both exclude revoked agents, or the two disagree by a headcount."""
        funnel_block = self.DIGEST[self.DIGEST.index("--- Funnel (7d) ---"):]
        funnel_block = funnel_block[:funnel_block.index("FUNNEL_LINE=")]
        assert "revoked_at IS NULL" in funnel_block

    def test_a_failed_query_is_not_reported_as_zero(self):
        """An empty result and a failed psql are different facts.

        COALESCE makes a genuinely empty week return "0|"; a failed query
        returns nothing at all, and saying +0 for it would hide a broken job.
        """
        assert "Funnel (7d): unavailable" in self.DIGEST


class TestConversionChain:
    """The chain is the headline, so its arithmetic has to be the honest one."""

    def test_chain_is_in_the_response(self):
        assert '"conversion": _chain(agents_out)' in CODE
        assert '"conversion_by_platform"' in CODE

    def test_steps_are_measured_against_the_previous_step(self):
        """Everything over registrations would flatten two different businesses.

        7 of 69 calling and 1 of those 7 asking for a credential is not the
        same as 7 calling and 1 of 69 asking, and dividing both by the
        registration count makes them look identical.
        """
        assert 'step("called", "credentialed", did_cred, did_call' in CODE
        assert 'step("credentialed", "paid", did_paid, did_cred' in CODE

    def test_steps_are_nested_so_a_rate_cannot_exceed_100(self):
        """The steps are not nested in the data.

        A credential can be issued to an agent that never appears in
        usage_daily_keys, because issuance is not always a metered call — seen
        live on 2026-09-20, one agent credentialed with zero calls. Measuring
        that against the calling population would print a rate above 100 %.
        """
        assert 'did_cred = [a for a in did_call' in CODE
        assert 'did_paid = [a for a in did_cred' in CODE

    def test_out_of_order_arrivals_are_shown_not_folded_in(self):
        assert '"reached_out_of_order"' in CODE

    def test_end_to_end_is_computed_not_multiplied(self):
        """Multiplying rounded step rates gives a different number."""
        assert '"end_to_end_pct": rate(len(did_paid), reg)' in CODE

    def test_rates_are_none_not_zero_when_the_step_is_empty(self):
        """0 % of nothing is a claim; None is the absence of one."""
        assert "if d else None" in CODE

    def test_taskmarket_is_reported_separately(self):
        """Per-bucket chains, so a paid channel cannot hide inside the total."""
        assert "by_bucket_chain[b] = _chain(" in CODE
