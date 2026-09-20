"""What the profile may and may not claim.

The table exists to compare what an agent says about itself against what it
does. Every test here guards one half of that: either a classifier that must not
guess, or the separation that keeps a declaration from being read as a
measurement.
"""
import datetime as dt

from app.agent_profile import (
    classify_cloud,
    classify_ua,
    summarise_rows,
    wallet_age_days,
    _CLOUD_PATTERNS,
    _UA_PATTERNS,
)


def _row(**kw):
    base = {"ip_org": None, "ip_country": None, "user_agent": None,
            "caller_framework": None, "endpoint": None, "ts": None}
    base.update(kw)
    return base


class TestCloud:
    def test_recognises_the_providers_that_occur(self):
        assert classify_cloud("Amazon Technologies Inc.") == "aws"
        assert classify_cloud("Hetzner Online GmbH") == "hetzner"
        assert classify_cloud("DigitalOcean, LLC") == "digitalocean"
        assert classify_cloud("Alibaba (US) Technology Co., Ltd.") == "alibaba"

    def test_unknown_org_stays_none_rather_than_guessing(self):
        """A wrong cloud attribution is worse than an empty one: this column
        feeds Sybil clustering, where a spurious shared provider invents a link
        between unrelated agents."""
        assert classify_cloud("Deutsche Telekom AG") is None
        assert classify_cloud("some regional ISP") is None
        assert classify_cloud(None) is None
        assert classify_cloud("") is None


class TestUserAgent:
    def test_frameworks_beat_bare_http_clients(self):
        """Ordering matters: a LangChain client that sends httpx in its UA must
        come back as langchain, not python-http."""
        assert classify_ua("langchain/0.2 python-requests/2.31") == "langchain"
        assert classify_ua("crewai-agent httpx/0.27") == "crewai"

    def test_a_plain_http_client_is_a_finding_not_a_blank(self):
        assert classify_ua("python-requests/2.31.0") == "python-http"
        assert classify_ua("curl/8.4.0") == "shell-http"
        assert classify_ua("Go-http-client/2.0") == "go-http"

    def test_no_user_agent_is_none(self):
        assert classify_ua(None) is None
        assert classify_ua("") is None

    def test_every_pattern_compiles_and_is_lowercase_safe(self):
        """The matcher lowercases its input, so a pattern with an uppercase
        letter can never fire."""
        for pattern, name in _UA_PATTERNS + _CLOUD_PATTERNS:
            assert pattern == pattern.lower(), f"{name}: pattern has uppercase"


class TestSummary:
    def test_no_rows_reports_zero_rather_than_nulls_that_look_like_failure(self):
        s = summarise_rows([], dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc))
        assert s["observed_rows"] == 0
        assert s["first_endpoints_24h"] == []
        assert s["asn"] is None

    def test_declared_caller_framework_beats_the_user_agent_guess(self):
        """The client said what it is on purpose; the UA is the fallback."""
        rows = [_row(user_agent="python-requests/2.31", caller_framework="langgraph")]
        assert summarise_rows(rows, None)["ua_framework"] == "langgraph"

    def test_user_agent_is_used_when_nothing_was_declared(self):
        rows = [_row(user_agent="crewai/0.5")]
        assert summarise_rows(rows, None)["ua_framework"] == "crewai"

    def test_the_mode_wins_not_the_first_row(self):
        rows = [_row(ip_country="DE"), _row(ip_country="US"), _row(ip_country="US")]
        assert summarise_rows(rows, None)["country"] == "US"

    def test_first_endpoints_are_the_first_24h_in_first_seen_order(self):
        reg = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)
        rows = [
            _row(endpoint="/identity/register-pop", ts=reg),
            _row(endpoint="/credits/balance", ts=reg + dt.timedelta(hours=1)),
            _row(endpoint="/identity/register-pop", ts=reg + dt.timedelta(hours=2)),
            # Outside the window: an agent's later habits are not its first move.
            _row(endpoint="/stats", ts=reg + dt.timedelta(hours=30)),
        ]
        eps = summarise_rows(rows, reg)["first_endpoints_24h"]
        assert eps == ["/identity/register-pop", "/credits/balance"]

    def test_rows_before_registration_are_excluded(self):
        """An IP-keyed observation sees whatever else used that /24 beforehand.
        Counting it would attribute a predecessor's traffic to a new agent."""
        reg = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)
        rows = [_row(endpoint="/someone-else", ts=reg - dt.timedelta(days=3))]
        assert summarise_rows(rows, reg)["first_endpoints_24h"] == []

    def test_naive_timestamps_do_not_crash_the_window(self):
        """request_log.ts is timestamptz but agents.created_at is not, so the
        two arrive with different awareness."""
        reg = dt.datetime(2026, 9, 1, 12, 0)  # naive, as agents.created_at is
        rows = [_row(endpoint="/x", ts=dt.datetime(2026, 9, 1, 13, 0, tzinfo=dt.timezone.utc))]
        assert summarise_rows(rows, reg)["first_endpoints_24h"] == ["/x"]

    def test_endpoint_list_is_capped(self):
        reg = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
        rows = [_row(endpoint=f"/e{i}", ts=reg + dt.timedelta(minutes=i)) for i in range(50)]
        assert len(summarise_rows(rows, reg)["first_endpoints_24h"]) == 20


class TestWalletAge:
    def test_counts_days_since_the_binding(self):
        now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
        assert wallet_age_days(dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc), now) == 10

    def test_unbound_wallet_is_none_not_zero(self):
        """Zero would read as "bound today", which is a different fact."""
        assert wallet_age_days(None) is None

    def test_a_binding_in_the_future_does_not_go_negative(self):
        now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
        assert wallet_age_days(dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc), now) == 0

    def test_naive_binding_timestamp_is_treated_as_utc(self):
        now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
        assert wallet_age_days(dt.datetime(2026, 9, 10), now) == 10
