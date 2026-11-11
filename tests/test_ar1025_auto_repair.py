"""Auto-repair digest of 25.10.2026 (ops AR-1025): literal path templates are
expected 404s, and only a stranger's request sets the last status."""
from datetime import datetime, timedelta, timezone

import scripts.auto_repair as ar

T = datetime(2026, 10, 25, 3, 10, tzinfo=timezone.utc)
VIER = ("/identity/spiffe/{spiffe_uri}", "/music/credential/{credential_id}",
        "/music/verify/{credential_id}", "/attestations/{attestation_id}")


def letzte(endpoint, ua, status, minuten):
    """One row as fetch_last_requests returns it."""
    return {"endpoint": endpoint, "user_agent": ua, "status_code": status,
            "ts": T - timedelta(minutes=minuten)}


class TestPlatzhalter:
    def test_the_four_candidates_of_25_10_are_expected(self):
        for p in VIER:
            assert ar.expected_reason(p), p

    def test_an_encoded_template_is_expected_too(self):
        assert ar.expected_reason("/music/verify/%7Bcredential_id%7D")
        assert ar.expected_reason("/identity/spiffe/{spiffe_uri:path}")

    def test_a_real_identifier_stays_a_candidate(self):
        for p in ("/music/verify/abc123", "/attestations/att_1", "/identity/spiffe/x",
                  "/violation/{", "/x/{}"):
            assert ar.expected_reason(p) is None, p


class TestLetzterStatus:
    def test_our_own_request_does_not_set_the_last_status(self):
        # 03:10Z: our recheck asked last with GET (404); a stranger's POST
        # before it got 405. The path answers, whoever asked last.
        rows = [letzte("/p", "moltrust-auto-repair/1.0 (internal 404 recheck)", 404, 0),
                letzte("/p", "moltrust-artefact-check/1.0", 404, 5),
                letzte("/p", "node", 405, 48)]
        assert ar.letzter_status(rows) == {"/p": 405}

    def test_the_latest_stranger_wins(self):
        rows = [letzte("/p", "node", 405, 30), letzte("/p", "agents-trust-probe/1", 404, 10)]
        assert ar.letzter_status(rows) == {"/p": 404}

    def test_the_class_no_longer_flips_with_our_recheck(self):
        fremd = [letzte("/p", "node", 405, 60)]
        vor = ar.letzter_status(fremd)
        nach = ar.letzter_status(fremd + [letzte("/p", "moltrust-auto-repair/1.0", 404, 0)])
        assert vor == nach == {"/p": 405}

    def test_no_user_agent_is_a_stranger(self):
        assert ar.letzter_status([letzte("/p", "", 200, 1),
                                  letzte("/p", "moltrust-x/1", 404, 0)]) == {"/p": 200}

    def test_a_path_only_we_asked_has_no_last_status(self):
        assert ar.letzter_status([letzte("/p", "moltrust-auto-repair/1.0", 404, 0)]) == {}
