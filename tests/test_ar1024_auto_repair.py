"""Auto-repair digest of 24.10.2026 (ops AR-1024): three expected 404s, and a
separate line for paths one sweep asked for on one day."""
from datetime import date

import scripts.auto_repair as ar

TAG = date(2026, 9, 26)


def zeile(endpoint, ip, hits, *, tag=TAG, ua="Mozilla/5.0"):
    """One row as fetch_hits_per_netz returns it."""
    return {"endpoint": endpoint, "tag": tag, "ip": ip, "user_agent": ua, "hits": hits}


class TestExpected:
    def test_the_three_paths_are_expected(self):
        for p in ("/api/registry", "/.well-known/ucp", "/.well-known/openid-configuration"):
            assert ar.expected_reason(p), p

    def test_their_neighbours_stay_candidates(self):
        for p in ("/api/registry/x", "/registry", "/.well-known/ucp/profile",
                  "/.well-known/openid-configuration/x", "/.well-known/oauth-client"):
            assert ar.expected_reason(p) is None, p


class TestNetz:
    def test_ipv4_is_cut_to_a_24(self):
        assert ar.netz("83.199.159.17") == "83.199.159.0/24"

    def test_ipv6_is_cut_to_a_48(self):
        assert ar.netz("2a01:4f8:1c1c:abcd::1") == "2a01:4f8:1c1c::/48"

    def test_garbage_is_passed_through(self):
        assert ar.netz("not-an-ip") == "not-an-ip"
        assert ar.netz(None) == "unknown"


class TestEinmalScans:
    def test_exactly_ninety_percent_is_a_one_off(self):
        rows = [zeile("/p", "83.199.159.1", 50), zeile("/p", "83.199.159.200", 40),
                zeile("/p", "9.9.9.9", 10)]
        s = ar.einmal_scans(rows)["/p"]
        assert (s["netz"], s["tag"], s["hits"], s["fremd_hits"]) == \
            ("83.199.159.0/24", TAG, 90, 100)

    def test_eighty_nine_percent_stays_a_candidate(self):
        rows = [zeile("/p", "83.199.159.1", 89), zeile("/p", "9.9.9.9", 11)]
        assert ar.einmal_scans(rows) == {}

    def test_two_days_from_one_net_stay_a_candidate(self):
        rows = [zeile("/p", "83.199.159.1", 50),
                zeile("/p", "83.199.159.1", 50, tag=date(2026, 9, 27))]
        assert ar.einmal_scans(rows) == {}

    def test_two_nets_on_one_day_stay_a_candidate(self):
        rows = [zeile("/p", "83.199.159.1", 50), zeile("/p", "83.199.160.1", 50)]
        assert ar.einmal_scans(rows) == {}

    def test_our_own_requests_count_on_neither_side(self):
        # 90 from the sweep, 10 from strangers elsewhere: a one-off. Our own
        # recheck adds 30 daily hits; counted as foreign it would push the
        # sweep below 90 %.
        rows = [zeile("/p", "83.199.159.1", 90), zeile("/p", "9.9.9.9", 10),
                zeile("/p", "46.225.175.218", 30, tag=date(2026, 10, 20),
                      ua="moltrust-auto-repair/1.0 (internal 404 recheck)")]
        assert ar.einmal_scans(rows)["/p"]["fremd_hits"] == 100

    def test_the_sweep_of_26_september_is_found(self):
        # The live shape of /api/registry on 24.10.2026: 127 hits from one /24
        # on 26.09., one later hit from elsewhere.
        rows = [zeile("/api/registry", "83.199.159.42", 127),
                zeile("/api/registry", "82.135.77.5", 1, tag=date(2026, 10, 24),
                      ua="curl/8.7.1")]
        s = ar.einmal_scans(rows)["/api/registry"]
        assert s["netz"] == "83.199.159.0/24" and s["hits"] == 127
