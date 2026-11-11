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

    def test_the_signature_directory_is_expected(self):
        # ops AR-404-4: Web Bot Auth wants this directory from a client that
        # signs its outgoing requests. We sign none.
        assert ar.expected_reason("/.well-known/http-message-signatures-directory")

    def test_the_signature_directory_entry_does_not_swallow_its_neighbours(self):
        # The silence test for the entry above: anchored, so it holds back
        # exactly one path and not the rest of /.well-known/ — api-catalog is a
        # gap we intend to close (AR-404-4 criterion 2), not an expected 404.
        for p in ("/.well-known/api-catalog",
                  "/.well-known/http-message-signatures-directory/keys",
                  "/.well-known/http-message-signatures"):
            assert ar.expected_reason(p) is None, p

    def test_the_two_api_paths_of_the_26_09_sweep_are_expected(self):
        # ops AR-1022: one sweep asked for both, nobody since.
        for p in ("/api/credentials", "/api/did/resolve"):
            assert ar.expected_reason(p), p

    def test_the_api_entries_do_not_swallow_their_neighbours(self):
        # Anchored at both ends: /api is served now, and a DID in the path
        # would be a different request that deserves to show up. /api/registry
        # itself is on the list since ops AR-1024; its sub-path is not.
        for p in ("/api", "/api/registry/x", "/api/credentials/x",
                  "/api/did/resolve/did:moltrust:0000000000000000",
                  "/credentials/verify"):
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

        assert ar.nur_eigene_pfade(rows, JETZT) == [("/agent/score", 480)]

    def test_a_path_with_real_callers_is_not_in_that_line(self):
        rows = [zeile("/vc/aae/evaluate", "curl/8.4.0", 30),
                zeile("/vc/aae/evaluate", "moltrust-auto-repair/1.0", 120)]

        assert ar.nur_eigene_pfade(rows, JETZT) == []

    def test_a_path_below_the_threshold_is_not_called_ours(self):
        """Fifteen strangers got a 404 there. "Only us" would be a false line.

        The first version of this filter took every path with 20 or fewer
        foreign hits into the "our own checks, not a missing route" section
        and printed our own count beside it — so a path with 15 foreign and
        10 internal 404s was reported as ours, and the 15 disappeared. It is
        a path under the threshold, and the digest never listed those.
        """
        rows = [zeile("/agent/intent", "curl/8.4.0", 15),
                zeile("/agent/intent", "moltrust-artefact-check/1.0", 10)]

        assert ar.nur_eigene_pfade(rows, JETZT) == []
        assert ar.zu_pfaden(rows, JETZT) == []

    def test_the_count_is_the_whole_traffic_of_that_path(self):
        """With no foreign hits, our own count and the total are the same."""
        rows = [zeile("/agent/score", "moltrust-artefact-check/1.0", 480),
                zeile("/agent/score", "moltrust-auto-repair/1.0", 20)]

        assert ar.nur_eigene_pfade(rows, JETZT) == [("/agent/score", 500)]

    def test_a_path_we_stopped_asking_for_leaves_the_line(self):
        """The hosted MCP server got 25 404s on /guard/skill/audit until
        24.10.2026, then the call went to MoltGuard directly. Without a
        freshness rule the line would name it until the window drops it."""
        alt = dict(zeile("/guard/skill/audit", "moltrust-mcp-server/1.0.0", 25),
                   last_seen=JETZT - timedelta(days=4))

        assert ar.nur_eigene_pfade([alt], JETZT) == []

    def test_a_path_we_still_ask_for_stays(self):
        """Silence probe: our hourly check's path, last 404 an hour ago, stays."""
        rows = [zeile("/agent/score", "moltrust-artefact-check/1.0", 480)]

        assert ar.nur_eigene_pfade(rows, JETZT) == [("/agent/score", 480)]

    def test_the_edge_of_the_window(self):
        knapp = dict(zeile("/x", "moltrust-auto-repair/1.0", 30),
                     last_seen=JETZT - ar.EIGENE_FRISCH)
        vorbei = dict(knapp, last_seen=JETZT - ar.EIGENE_FRISCH - timedelta(seconds=1))

        assert ar.nur_eigene_pfade([knapp], JETZT) == [("/x", 30)]
        assert ar.nur_eigene_pfade([vorbei], JETZT) == []
