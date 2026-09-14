"""Usage instrumentation: key fingerprints, bounded meter keys, rollup shape."""
import re

import pytest

from app.usage import (
    ROLLUP_RETENTION_MONTHS,
    TRAFFIC_CLASS_SQL,
    VALID_TRAFFIC_CLASSES,
    bounded_endpoint_key,
    key_fingerprint,
)


class TestKeyFingerprint:
    def test_is_stable(self):
        assert key_fingerprint("mt_live_abc") == key_fingerprint("mt_live_abc")

    def test_differs_per_key(self):
        assert key_fingerprint("mt_live_abc") != key_fingerprint("mt_live_abd")

    def test_does_not_leak_the_key(self):
        secret = "mt_live_super_secret_value"
        fp = key_fingerprint(secret)
        assert secret not in fp
        assert len(fp) == 16
        assert re.fullmatch(r"[0-9a-f]{16}", fp)

    def test_empty_key_has_no_fingerprint(self):
        assert key_fingerprint("") is None


class TestBoundedEndpointKey:
    """Unbounded meter keys are the failure mode here: one row per subject."""

    @pytest.mark.parametrize(
        "template,values",
        [
            ("/identity/verify/did:moltrust:{}", ["6d5c9d50d2c34ad0", "a1b2c3d4e5f60718"]),
            (
                "/api/agent/score/{}",
                [
                    "0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38",
                    "0x380238347e58435f40B4da1F1A045A271D5838F5",
                ],
            ),
            ("/compliance/report/{}", ["1", "4711"]),
            ("/sports/fantasy/history/{}", ["1", "99"]),
        ],
    )
    def test_value_segments_collapse(self, template, values):
        """Which placeholder name is used does not matter; collapsing does.

        app.credits.resolve_endpoint_key labels several id-bearing routes
        {did} regardless of what the segment holds. Asserting on the name
        would pin that quirk instead of the property worth having.
        """
        keys = {bounded_endpoint_key("GET", template.format(v)) for v in values}
        assert len(keys) == 1
        assert "{" in keys.pop()

    def test_many_subjects_share_one_key(self):
        keys = {
            bounded_endpoint_key("GET", f"/identity/verify/did:moltrust:{i:016x}")
            for i in range(50)
        }
        assert len(keys) == 1

    def test_unmapped_routes_are_bounded_by_the_fallback(self):
        """resolve_endpoint_key leaves unmapped paths verbatim; this is the net."""
        keys = {
            bounded_endpoint_key("GET", f"/some/unmapped/route/did:moltrust:{i:016x}")
            for i in range(20)
        }
        assert len(keys) == 1
        assert "{did}" in keys.pop()

    def test_route_shape_survives(self):
        key = bounded_endpoint_key("GET", "/identity/verify/did:moltrust:abcd")
        assert key.startswith("GET /identity/verify/")

    def test_method_is_part_of_the_key(self):
        assert bounded_endpoint_key("GET", "/x") != bounded_endpoint_key("POST", "/x")

    def test_key_is_length_capped(self):
        long_path = "/a" * 500
        assert len(bounded_endpoint_key("GET", long_path)) <= 120

    def test_static_paths_are_untouched(self):
        assert bounded_endpoint_key("GET", "/stats") == "GET /stats"


class TestTrafficClassSql:
    def test_every_arm_yields_a_known_class(self):
        emitted = set(re.findall(r"THEN '([a-z-]+)'", TRAFFIC_CLASS_SQL))
        emitted |= set(re.findall(r"ELSE '([a-z-]+)'", TRAFFIC_CLASS_SQL))
        assert emitted, "no classes parsed out of the CASE"
        assert emitted <= VALID_TRAFFIC_CLASSES

    def test_scanner_is_decided_before_browser(self):
        """A scanner sending a Chrome user-agent is a scanner.

        The 90-day sweep found 37,524 of 48,305 'browser' requests came from two
        vulnerability scanners spoofing Chrome. If the browser arm were to move
        above the scanner arm, that misclassification comes straight back.
        """
        assert TRAFFIC_CLASS_SQL.index("'scanner'") < TRAFFIC_CLASS_SQL.index("'browser'")

    def test_self_is_decided_first(self):
        first = min(
            TRAFFIC_CLASS_SQL.index(f"'{cls}'")
            for cls in VALID_TRAFFIC_CLASSES
            if f"'{cls}'" in TRAFFIC_CLASS_SQL
        )
        assert TRAFFIC_CLASS_SQL.index("'self'") == first

    def test_case_expression_is_balanced(self):
        assert TRAFFIC_CLASS_SQL.count("CASE") == TRAFFIC_CLASS_SQL.count("END")
        assert TRAFFIC_CLASS_SQL.count("WHEN") == TRAFFIC_CLASS_SQL.count("THEN")


def test_rollups_outlive_request_log_by_a_wide_margin():
    """request_log keeps 30 days; the rollup has to answer far past that."""
    assert ROLLUP_RETENTION_MONTHS == 24
