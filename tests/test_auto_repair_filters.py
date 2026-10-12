"""The digest asked for paths that already existed.

/a2a/health and /.well-known/security.txt were built on 2026-10-02 and kept
appearing as repair candidates for days afterwards, because a path fixed
yesterday still carries its whole 404 history inside the 30-day window. And
two classes of 404 are the correct answer and will never stop appearing.
"""
from unittest import mock

import scripts.auto_repair as ar


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
