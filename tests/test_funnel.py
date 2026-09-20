"""The funnel: bucket mapping, goal arithmetic, and GET /admin/funnel.

The endpoint assertions read the source rather than importing app.main — same
reason as test_admin_usage.py: the claims are about which table a number comes
from, and importing drags in the whole application to look at one handler.
"""
import datetime as _dt
from pathlib import Path

from app.funnel import (
    BUCKET_ORDER,
    canonical_platform,
    FUNNEL_EPOCH,
    FUNNEL_GOAL,
    FUNNEL_GOAL_DAYS,
    PLATFORM_BUCKETS,
    bucket_of,
    build_bucket_function_sql,
    goal_progress,
    telegram_line,
    is_internal,
    build_internal_function_sql,
    split_paid_and_organic,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "app" / "main.py").read_text()

START = SRC.index('@app.get("/admin/funnel")')
# /admin/pools is the next endpoint in the file. Named so this block stays
# this endpoint only — a wider block lets a neighbour satisfy or break
# assertions about which table this one reads.
END = SRC.index('@app.get("/admin/pools")')
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

    def test_framework_pools_are_mapped_because_we_ship_the_package(self):
        """This reverses an earlier rule, and the reason it reverses matters.

        The mapping used to be forbidden: no agent had ever registered as
        crewai or langchain, so inventing the alias would have moved future
        rows out of `other` on no evidence. The evidence now exists in the
        other direction — moltrust-crewai and moltrust-langchain set the value
        themselves, so a row carrying it was put there by our own package.
        """
        assert bucket_of("crewai") == "crewai"
        assert bucket_of("moltrust-crewai") == "crewai"
        assert bucket_of("langchain") == "langchain"

    def test_a_framework_we_do_not_ship_stays_in_other(self):
        """The rule still holds where we have no package to point at."""
        assert bucket_of("autogen") == "other"
        assert bucket_of("llamaindex") == "other"
        assert bucket_of("semantic-kernel") == "other"


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

        This is a tripwire, not a fact: the number goes up when a population
        query is added and the point is to look at the new query rather than
        to bump the constant. Five as of 2026-09-20 — daily, by_platform,
        cohort, recent, internal_total. The sixth query in the endpoint counts
        revocations and uses IS NOT NULL by design.
        """
        assert CODE.count("revoked_at IS NULL") == 5

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


class TestInternalTraffic:
    """Ours, and why it must come out of organic.

    On 2026-09-20 the largest origin cluster in the database was one /24
    belonging to our own infrastructure: 36 agents across klaw, moltbook and
    ownify over four months. Read through the platform column alone it looked
    like three independent sources, and every organic figure carried it.
    """

    def test_the_operator_host_is_internal(self):
        assert is_internal(registration_ip="57.129.23.0") is True
        assert is_internal(registration_ip="57.129.23.44") is True

    def test_a_neighbouring_network_is_not(self):
        """The prefix ends at the third octet on purpose — 57.129.230.0 is a
        different network and a substring match would swallow it."""
        assert is_internal(registration_ip="57.129.230.0") is False
        assert is_internal(registration_ip="57.129.2.0") is False
        assert is_internal(registration_ip="157.129.23.0") is False

    def test_reserved_platform_strings(self):
        assert is_internal(platform="test") is True
        assert is_internal(platform="system") is True
        assert is_internal(platform="moltrust-internal") is True
        assert is_internal(platform="  TEST  ") is True

    def test_moltrust_is_not_internal(self):
        """`moltrust` is the platform string the public registry examples use,
        so anyone reading the docs can send it. Treating it as internal would
        quietly hide real registrations."""
        assert is_internal(platform="moltrust") is False

    def test_either_signal_is_enough(self):
        assert is_internal(platform="taskmarket", registration_ip="57.129.23.0") is True
        assert is_internal(platform="test", registration_ip="1.2.3.0") is True
        assert is_internal(platform="taskmarket", registration_ip="1.2.3.0") is False

    def test_nothing_known_is_not_internal(self):
        assert is_internal() is False
        assert is_internal(platform=None, registration_ip=None) is False
        assert is_internal(platform="", registration_ip="") is False

    def test_generated_migration_matches_the_generator(self):
        """Same contract as the bucket function: one definition, three
        consumers, and only this assertion keeps the SQL equal to the Python."""
        path = ROOT / "migrations" / "2026-09-20_funnel_is_internal.sql"
        assert build_internal_function_sql() in path.read_text()


class TestAcquisitionSplit:
    def test_internal_comes_out_of_organic(self):
        counts = {"taskmarket": 64, "other": 100}
        s = split_paid_and_organic(counts, internal=36)
        assert s == {"total": 164, "paid": 64, "internal": 36, "organic": 64}

    def test_without_internal_it_behaves_as_before(self):
        s = split_paid_and_organic({"taskmarket": 10, "a2a": 5})
        assert s["total"] == 15 and s["paid"] == 10 and s["organic"] == 5
        assert s["internal"] == 0

    def test_organic_never_goes_negative(self):
        """A paid bucket that is also internal would otherwise be subtracted
        twice and produce a negative headline number."""
        s = split_paid_and_organic({"taskmarket": 5}, internal=5)
        assert s["organic"] == 0

    def test_internal_is_not_a_bucket(self):
        """It is passed in, not read from counts: an internal registration
        still carries whatever platform string it was made with, and moving it
        into a bucket of its own would make the platform breakdown lie."""
        counts = {"taskmarket": 64, "other": 100}
        before = dict(counts)
        split_paid_and_organic(counts, internal=36)
        assert counts == before


class TestPoolTaxonomy:
    """A.1 — the pools the acquisition programme reports against."""

    EXPECTED_POOLS = {
        "clawhub", "hermes", "smithery", "glama", "a2a", "erc8004", "rnwy",
        "virtuals-acp", "olas", "taskmarket", "x402-bazaar", "langchain",
        "crewai", "openai-agents", "vercel-ai", "sdk", "other",
    }

    def test_every_agreed_pool_is_a_bucket(self):
        assert set(BUCKET_ORDER) == self.EXPECTED_POOLS

    def test_aliases_collapse_so_a_pool_is_not_split(self):
        """Three spellings of one pool read as three small pools otherwise."""
        for alias in ("virtuals", "virtuals_acp", "VIRTUALS-ACP"):
            assert bucket_of(alias) == "virtuals-acp"
        for alias in ("bazaar", "x402bazaar"):
            assert bucket_of(alias) == "x402-bazaar"
        for alias in ("moltrust-crewai", "crewai"):
            assert bucket_of(alias) == "crewai"

    def test_canonical_platform_keeps_an_unknown_value(self):
        """A registration is worth more than a tidy taxonomy.

        Rejecting an unmapped platform would cost the thing the taxonomy
        exists to count. It stays verbatim and buckets to `other`.
        """
        assert canonical_platform("mein-agent") == "mein-agent"
        assert bucket_of("mein-agent") == "other"

    def test_canonical_platform_is_what_registration_stores(self):
        src = (ROOT / "app" / "main.py").read_text()
        block = src[src.index("def validate_platform"):]
        block = block[:block.index("@field_validator", 10)]
        assert "return canonical_platform(v)" in block

    def test_ownify_is_partner_not_internal(self):
        """Excluded from the goal, but it is not ours.

        Filing a partner's population under `internal` would misreport whose
        agents they are.
        """
        from app.funnel import is_partner_platform, is_internal_platform
        assert is_partner_platform("ownify")
        assert not is_internal_platform("ownify")

    def test_openclaw_still_does_not_count_as_clawhub(self):
        """Unchanged on purpose — see the older test for the reasoning."""
        assert bucket_of("openclaw") == "other"


class TestPoolsEndpoint:
    """A.2 — per-pool acquisition with the spend beside the count."""

    SRC = (ROOT / "app" / "main.py").read_text()
    START = SRC.index('@app.get("/admin/pools")')
    END = SRC.index('@app.get("/admin/dashboard/x402")')
    CODE = _executable(SRC[START:END])

    def test_requires_an_admin_session(self):
        assert "_get_admin_session(request)" in self.CODE

    def test_internal_and_partner_are_held_out_of_the_goal(self):
        """Spend over a population that includes our own test agents flatters
        exactly the number the programme is meant to manage down."""
        assert 'excluded["internal"] += 1' in self.CODE
        assert 'excluded["partner"] += 1' in self.CODE
        assert "goal_total += e[\"registered\"]" in self.CODE

    def test_partner_is_counted_apart_from_internal(self):
        assert "is_partner_platform(r[\"platform\"])" in self.CODE

    def test_chain_is_nested(self):
        """Same reason as the funnel: a credential can arrive without a call,
        and an unnested rate can exceed 100 %."""
        block = self.CODE[self.CODE.index('if m["call_day"]:'):]
        assert block.index('if m["vc_at"]:') < block.index('if m["pay_at"]:')

    def test_cost_ratios_guard_against_division_by_zero(self):
        assert "return round(float(cost) / n, 3) if n and cost else None" in self.CODE

    def test_spend_comes_from_the_table_not_from_chain_history(self):
        assert "FROM pool_spend" in self.CODE
        assert "payment_events" not in self.CODE.split("first_pay")[0]

    def test_window_is_validated(self):
        assert "days not in (7, 30, 90)" in self.CODE

    def test_repeat_use_is_seven_days_apart(self):
        """The bounty bonus condition: a DID that came back a week later."""
        assert "max(day) - min(day) >= 7" in self.CODE


class TestPoolsTelegramLine:
    def test_shape(self):
        from app.funnel import pools_telegram_line
        line = pools_telegram_line(7, [("clawhub", 12), ("taskmarket", 40)])
        assert line == "Pools (7d): 52 — taskmarket 40 · clawhub 12"

    def test_largest_first_because_that_is_what_moved(self):
        from app.funnel import pools_telegram_line
        assert pools_telegram_line(7, [("a", 1), ("b", 9)]).index("b 9") < \
               pools_telegram_line(7, [("a", 1), ("b", 9)]).index("a 1")

    def test_quiet_week_still_says_something(self):
        from app.funnel import pools_telegram_line
        assert "keine Registrierungen" in pools_telegram_line(7, [])
