"""The digest asked for paths that already existed.

/a2a/health and /.well-known/security.txt were built on 2026-10-02 and kept
appearing as repair candidates for days afterwards, because a path fixed
yesterday still carries its whole 404 history inside the 30-day window. And
two classes of 404 are the correct answer and will never stop appearing.
"""
from datetime import datetime, timedelta, timezone
from unittest import mock

import scripts.auto_repair as ar

JETZT = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)


def zeile(endpoint, ua, hits, *, alter_tage=10, last_status=404):
    """One row as fetch_hits_per_ua returns it: endpoint, UA, count."""
    return {
        "endpoint": endpoint,
        "user_agent": ua,
        "hits": hits,
        "first_seen": JETZT - timedelta(days=alter_tage),
        "last_seen": JETZT - timedelta(hours=1),
        "last_status": last_status,
    }


class TestExpectedReason:
    def test_oauth_metadata_is_expected(self):
        for p in ("/.well-known/oauth-protected-resource",
                  "/.well-known/oauth-protected-resource/mcp",
                  "/.well-known/oauth-authorization-server",
                  "/.well-known/oauth-authorization-server/mcp"):
            assert ar.expected_reason(p), p

    def test_a_did_miss_is_expected(self):
        assert ar.expected_reason("/identity/resolve/did:moltrust:0000000000000000")

    def test_a_real_gap_is_not_expected(self):
        for p in ("/a2a/health", "/.well-known/glama.json", "/vc/aae/evaluate"):
            assert ar.expected_reason(p) is None, p

    def test_every_entry_carries_a_reason(self):
        assert ar.EXPECTED_404
        for pattern, reason in ar.EXPECTED_404:
            assert pattern and len(reason) > 40, pattern


class TestProbe:
    def test_head_is_tried_first(self):
        resp = mock.MagicMock(status=404)
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        with mock.patch.object(ar.urllib.request, "urlopen", return_value=resp) as op:
            assert ar.probe("/nope") == 404
        assert op.call_args.args[0].method == "HEAD"

    def test_405_on_head_falls_back_to_get(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(req.method)
            if req.method == "HEAD":
                raise ar.urllib.error.HTTPError("u", 405, "no", {}, None)
            raise ar.urllib.error.HTTPError("u", 404, "no", {}, None)

        with mock.patch.object(ar.urllib.request, "urlopen", side_effect=fake):
            assert ar.probe("/post-only") == 404
        assert calls == ["HEAD", "GET"]

    def test_a_dead_network_is_not_a_404(self):
        """None, not 404 — an unreachable host must not read as a missing path."""
        with mock.patch.object(ar.urllib.request, "urlopen", side_effect=OSError("down")):
            assert ar.probe("/anything") is None


class TestEigeneAnfrage:
    """Our own programs name themselves; the report counted them as callers.

    The report of 2026-10-16 listed `moltrust-auto-repair/1.0` and
    `moltrust-artefact-check/1.0` inside its hit counts, so the digest asked
    for paths it had been asking for itself (ops AR-404-2).
    """

    def test_our_own_user_agents_are_ours(self):
        for ua in ("moltrust-auto-repair/1.0 (internal 404 recheck)",
                   "moltrust-artefact-check/1.0",
                   "MolTrust-Auto-Repair/1.0"):
            assert ar.ist_eigene_anfrage(ua), ua

    def test_a_stranger_stays_a_stranger(self):
        """The negative case: a foreign agent that merely mentions us.

        `python-requests/2.31` is the plain case. The other two matter more:
        a client naming MolTrust in the middle of its UA, and one calling
        itself `moltrustler` — neither is one of our programs, and a
        substring match would have dropped both.
        """
        for ua in ("python-requests/2.31",
                   "Mozilla/5.0 (compatible; moltrust-fan)",
                   "moltrustler/2.0",
                   "curl/8.4.0"):
            assert not ar.ist_eigene_anfrage(ua), ua

    def test_no_user_agent_is_a_stranger(self):
        """An unnamed caller is someone we cannot account for, so it counts."""
        for ua in (None, "", "unknown"):
            assert not ar.ist_eigene_anfrage(ua), repr(ua)


class TestZuPfaden:
    def test_our_own_hits_leave_the_count(self):
        rows = [zeile("/vc/aae/evaluate", "curl/8.4.0", 30),
                zeile("/vc/aae/evaluate", "moltrust-auto-repair/1.0", 120)]

        pfade = ar.zu_pfaden(rows, JETZT)

        assert len(pfade) == 1
        assert pfade[0]["fremd_hits"] == 30
        assert pfade[0]["total_hits"] == 150
        assert pfade[0]["user_agents"] == ["curl/8.4.0"]

    def test_a_path_only_we_ask_for_is_no_candidate(self):
        """The case that put /identity/resolve/ in the report every day."""
        rows = [zeile("/identity/resolve/did:moltrust:0", "moltrust-artefact-check/1.0", 700),
                zeile("/identity/resolve/did:moltrust:0", "moltrust-auto-repair/1.0", 30)]

        assert ar.zu_pfaden(rows, JETZT) == []

    def test_a_stranger_over_the_threshold_still_arrives(self):
        """The other direction: the filter must not swallow a real complaint."""
        rows = [zeile("/vc/aae/evaluate", "python-requests/2.31", 21)]

        assert [p["endpoint"] for p in ar.zu_pfaden(rows, JETZT)] == ["/vc/aae/evaluate"]

    def test_twenty_foreign_hits_are_not_enough(self):
        """Same threshold as before, now measured on strangers only."""
        rows = [zeile("/vc/aae/evaluate", "python-requests/2.31", 20),
                zeile("/vc/aae/evaluate", "moltrust-auto-repair/1.0", 500)]

        assert ar.zu_pfaden(rows, JETZT) == []

    def test_our_own_recheck_does_not_age_a_fresh_problem(self):
        """Age is dated by a stranger's first 404, not by our daily recheck.

        Our recheck runs every day, so its first hit is always older than
        three days. Dating the problem by it would carry every endpoint past
        the age condition, including one a stranger first hit this morning.
        """
        rows = [zeile("/vc/aae/evaluate", "curl/8.4.0", 40, alter_tage=1),
                zeile("/vc/aae/evaluate", "moltrust-auto-repair/1.0", 10, alter_tage=25)]

        assert ar.zu_pfaden(rows, JETZT) == []

    def test_the_order_follows_the_foreign_count(self):
        rows = [zeile("/a", "curl/8.4.0", 25), zeile("/a", "moltrust-auto-repair/1.0", 900),
                zeile("/b", "curl/8.4.0", 60)]

        assert [p["endpoint"] for p in ar.zu_pfaden(rows, JETZT)] == ["/b", "/a"]


class TestAnteilFremd:
    def test_the_share_is_printed_per_path(self):
        pfad = {"fremd_hits": 142, "total_hits": 180}

        assert ar.anteil_fremd(pfad) == "142/180 foreign (79%)"

    def test_a_path_nobody_outside_asked_for_shows_zero(self):
        assert ar.anteil_fremd({"fremd_hits": 0, "total_hits": 90}) == "0/90 foreign (0%)"

    def test_no_hits_does_not_divide_by_zero(self):
        assert ar.anteil_fremd({"fremd_hits": 0, "total_hits": 0}) == "no hits"


class TestUaZeile:
    def test_only_foreign_agents_are_named(self):
        pfad = {"user_agents": ["curl/8.4.0", "python-requests/2.31"]}

        assert ar.ua_zeile(pfad) == "curl/8.4.0; python-requests/2.31"

    def test_a_path_without_a_foreign_agent_says_so(self):
        assert ar.ua_zeile({"user_agents": []}) == "none"


class TestNurEigenePfade:
    def test_a_path_only_we_ask_for_is_named(self):
        """It leaves the candidate list and lands in its own line."""
        rows = [zeile("/agent/score", "moltrust-artefact-check/1.0", 480)]

        assert ar.nur_eigene_pfade(rows) == [("/agent/score", 480)]

    def test_a_path_with_real_callers_is_not_in_that_line(self):
        rows = [zeile("/vc/aae/evaluate", "curl/8.4.0", 30),
                zeile("/vc/aae/evaluate", "moltrust-auto-repair/1.0", 120)]

        assert ar.nur_eigene_pfade(rows) == []
