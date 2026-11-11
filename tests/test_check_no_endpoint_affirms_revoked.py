"""The judge of c-no-endpoint-affirms-a-revoked-did, with both probes.

Each shape a route returns for a revoked DID is judged twice: the answer the
route gave before 2026-10-20 must count as an affirmation, and the answer it
gives now must not. A check that only ever fires, or never does, is guessing.
"""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_no_endpoint_affirms_a_revoked_did.py"
_spec = importlib.util.spec_from_file_location("check_revoked", _PATH)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

DID = "did:moltrust:0123456789abcdef"
REV = {"at": "2026-10-20T12:00:00", "reason": "test"}


def _svg(value):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" aria-label="MolTrust: {value}">'
            f"<title>MolTrust Trust Score: {value}</title></svg>")


CASES = [
    # (route, answer before the fix, answer after the fix)
    ("svg badge", _svg("85 / A"), _svg("REVOKED")),
    ("wallet", {"found": True, "grade": "A", "trust_score": 85.0},
     {"found": True, "verified": False, "revoked": REV, "grade": "REVOKED", "trust_score": None}),
    ("erc8004 by address", {"moltrust_did": DID, "verified": True},
     {"moltrust_did": DID, "verified": False, "revoked": REV}),
    ("erc8004 reverse", {"moltrust_did": DID, "moltrust_trust_score": {"score": 85.0, "grade": "A"}},
     {"moltrust_did": DID, "moltrust_verified": False, "moltrust_revoked": REV,
      "moltrust_trust_score": {"score": None, "grade": "REVOKED"}}),
    ("registration file", {"active": True}, {"active": False, "revoked": REV}),
    ("trust gate", {"decision": "ALLOW", "trust_score": 85.0},
     {"decision": "DENY", "reason": "revoked", "trust_score": None}),
    ("x402 verify", {"verified": True, "payment_ready": True},
     {"verified": False, "payment_ready": False, "reason": "revoked"}),
    ("a2a card", {"trust": {"score": 0.0, "verified": True}},
     {"trust": {"score": 0.0, "verified": False, "revoked": REV}}),
    ("swarm graph", {"did": DID, "nodes": [{"did": DID, "grade": "A", "score": 85.0}]},
     {"did": DID, "revoked": REV, "nodes": [{"did": DID, "grade": "REVOKED", "score": None}]}),
    ("attestation", {"did": DID, "value": 85.0, "grade": "A"},
     {"did": DID, "value": None, "grade": "REVOKED", "revoked": REV}),
]


def test_each_old_answer_is_a_finding():
    for name, before, _ in CASES:
        assert check.affirms(before, DID), f"{name}: the pre-fix answer must count"


def test_each_new_answer_is_silent():
    for name, _, after in CASES:
        assert check.affirms(after, DID) is None, f"{name}: the fixed answer must not count"


def test_an_endorser_node_does_not_speak_for_the_asked_did():
    # A live endorser with grade A is not a statement about the revoked DID.
    graph = {"did": DID, "nodes": [
        {"did": DID, "grade": "REVOKED", "score": None},
        {"did": "did:moltrust:ffffffffffffffff", "grade": "A", "score": 90.0},
    ]}
    assert check.affirms(graph, DID) is None


def test_unverified_svg_is_a_finding():
    # "unverified" is not "revoked": a badge for a revoked DID must say which.
    assert check.affirms(_svg("unverified"), DID)


def test_non_svg_text_is_not_judged():
    assert check.affirms("Internal Server Error", DID) is None


# Cache-Control of the badge routes, judged with both probes like the bodies.
STALE_HEADERS = [
    "max-age=3600, s-maxage=3600",              # both SVG badges until 2026-10-20
    None,                                       # the JSON badge: no header at all
    "public, max-age=60",
    "public, no-cache, stale-while-revalidate=60",
    "public, no-cache, s-maxage=60",
    "public, max-age=31536000, immutable",
]
FRESH_HEADERS = ["public, no-cache", "no-cache", "no-store", "private, no-cache, max-age=0"]


def test_each_reusable_cache_header_is_a_finding():
    for cc in STALE_HEADERS:
        assert check.cache_allows_stale(cc), f"{cc!r} lets a cache keep a revoked grade"


def test_each_revalidating_cache_header_is_silent():
    for cc in FRESH_HEADERS:
        assert check.cache_allows_stale(cc) is None, f"{cc!r} revalidates every use"


def test_the_badge_routes_are_judged_on_their_cache_header():
    names = {name for name, _ in check.ENDPOINTS}
    assert check.REVALIDATED <= names
    assert {"/identity/badge/<did>", "/identity/badge/<did>.svg", "/badge/<did>"} <= check.REVALIDATED


# --- ops SG-429: an answer that says nothing about the DID is no answer ------
# Until 2026-10-25 the check took every status but 404 and 0 as a reply to
# judge. /x402/verify answered 33 of 63 hourly questions with HTTP 429, and a
# 429 carries no `payment_ready`, so it read as "not affirmed" and the run was
# green.

import re  # noqa: E402

import pytest  # noqa: E402

REVOKED_ROWS = [[f"did:moltrust:{i:016x}", "", ""] for i in range(63)]
X402 = "/x402/verify?did=<did>"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.calls = []                       # (time, route name)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


def _route(url):
    path = url[len(check.BASE):]
    for name, tmpl in check.ENDPOINTS:
        prefix = tmpl.split("{", 1)[0]
        if path.startswith(prefix):
            return name
    raise AssertionError(url)


def _run(monkeypatch, capsys, answer, rows=REVOKED_ROWS):
    """Run main() against a fake API; `answer(name, n)` gives the n-th reply."""
    clock = FakeClock()
    seen = {}

    def fake_get(url):
        name = _route(url)
        seen[name] = seen.get(name, 0) + 1
        clock.calls.append((clock.now, name))
        return answer(name, seen[name])

    monkeypatch.setattr(check, "psql", lambda sql: rows)
    monkeypatch.setattr(check, "get", fake_get)
    monkeypatch.setattr(check, "_clock", clock.clock)
    monkeypatch.setattr(check, "_sleep", clock.sleep)
    rc = check.main()
    out = capsys.readouterr().out
    return rc, out, clock


REVOKED_ANSWER = (200, {"verified": False, "payment_ready": False, "reason": "revoked",
                        "grade": "REVOKED"}, {"cache-control": "public, no-cache"})


def _always(reply):
    return lambda name, n: reply


@pytest.mark.parametrize("status", [429, 500, 502, 503, 400, 401, 403, 422, 0])
def test_a_reply_that_says_nothing_about_the_did_makes_the_run_unreadable(
        monkeypatch, capsys, status):
    silent = (status, {"error": "Rate limit exceeded. Try again later."}, {})

    def answer(name, n):
        return silent if name == X402 else REVOKED_ANSWER

    rc, out, _ = _run(monkeypatch, capsys, answer)
    assert rc == 2
    assert out.strip().splitlines()[-1] == "-1"
    assert re.search(r"^UNREADABLE: \d+ Abfragen ohne Antwort$", out, re.M)
    assert "kein Endpunkt bejaht" not in out


def test_404_is_an_answer(monkeypatch, capsys):
    # Negative probe: "not listed" says something about the DID.
    def answer(name, n):
        return (404, {"detail": "DID not found"}, {}) if name == X402 else REVOKED_ANSWER

    rc, out, _ = _run(monkeypatch, capsys, answer)
    assert rc == 0 and out.strip().splitlines()[-1] == "0"
    assert "UNREADABLE" not in out


def test_a_429_is_asked_again_after_the_window(monkeypatch, capsys):
    # The limiter sends no Retry-After; the check waits one window and asks again.
    def answer(name, n):
        if name == X402 and n == 1:
            return (429, {"error": "Rate limit exceeded. Try again later."}, {})
        return REVOKED_ANSWER

    rc, out, clock = _run(monkeypatch, capsys, answer)
    assert rc == 0, out
    x402 = [t for t, name in clock.calls if name == X402]
    assert x402[1] - x402[0] >= check.RETRY_AFTER_DEFAULT_S


def test_retry_after_is_honoured(monkeypatch, capsys):
    def answer(name, n):
        if name == X402 and n == 1:
            return (429, {}, {"retry-after": "7"})
        return REVOKED_ANSWER

    rc, _, clock = _run(monkeypatch, capsys, answer)
    assert rc == 0
    x402 = [t for t, name in clock.calls if name == X402]
    assert 7 <= x402[1] - x402[0] < check.RETRY_AFTER_DEFAULT_S


def test_an_affirmation_still_counts(monkeypatch, capsys):
    def answer(name, n):
        if name == X402:
            return (200, {"verified": True, "payment_ready": True}, {})
        return REVOKED_ANSWER

    rc, out, _ = _run(monkeypatch, capsys, answer)
    assert rc == 1 and out.strip().splitlines()[-1] == "63"


def test_no_route_is_asked_faster_than_its_limit_allows(monkeypatch, capsys):
    # The measured case: 63 revoked DIDs, /x402/verify at 30 a minute.
    rc, _, clock = _run(monkeypatch, capsys, _always(REVOKED_ANSWER))
    assert rc == 0
    for name, limit in check.ROUTE_LIMITS.items():
        if not limit:
            continue
        times = [t for t, n in clock.calls if n == name]
        for i, t in enumerate(times):
            in_window = sum(1 for u in times[i:] if u < t + 60)
            assert in_window <= limit * check.PACE_SHARE, (name, t, in_window)
    assert sum(1 for _, n in clock.calls if n == X402) == 63
    # and it ends well inside the selftest's 300 s
    assert clock.now < check.RUN_BUDGET_S


def test_a_run_that_cannot_finish_in_time_is_unreadable(monkeypatch, capsys):
    rows = [[f"did:moltrust:{i:016x}", "", ""] for i in range(200)]
    rc, out, clock = _run(monkeypatch, capsys, _always(REVOKED_ANSWER), rows)
    assert rc == 2
    assert "NICHT GEFRAGT" in out
    assert clock.now < 300


def test_identity_erc8004_is_asked_only_for_an_evm_wallet(monkeypatch, capsys):
    rows = [["did:moltrust:00000000000000aa", "9i2RkwvBAiELcGPbwvNaTVo6XZUkHTD4oiC4P2EaENHa", ""],
            ["did:moltrust:00000000000000bb", "0x" + "ab" * 20, ""]]
    _, _, clock = _run(monkeypatch, capsys, _always(REVOKED_ANSWER), rows)
    asked = [n for _, n in clock.calls]
    assert asked.count("/identity/erc8004/<address>") == 1
    assert asked.count("/wallet/<address>") == 2


# The pace table must say what app/main.py declares; a limit lowered there
# without this table would bring the 429s back.
_DECORATED = {
    "/identity/verify/<did>": "/identity/verify/{did}",
    "/identity/badge/<did>": "/identity/badge/{did}",
    "/identity/badge/<did>.svg": "/identity/badge/{did}.svg",
    "/badge/<did>": "/badge/{did:path}",
    "/skill/trust-score/<did>": "/skill/trust-score/{did:path}",
    "/a2a/agent-card/<did>": "/a2a/agent-card/{did}",
    "/swarm/graph/<did>": "/swarm/graph/{did:path}",
    "/trust/gate/<did>": "/trust/gate/{did:path}",
    "/x402/verify?did=<did>": "/x402/verify",
    "/agents/<did>/erc8004": "/agents/{did}/erc8004",
    "/wallet/<address>": "/wallet/{address}",
    "/identity/erc8004/<address>": "/identity/erc8004/{address}",
    "/resolve/erc8004/<agent_id>": "/resolve/erc8004/{agent_id}",
    "/attestations/<id>?did=<did>": "/attestations/{attestation_id}",
}


def _declared_limit(source, path):
    m = re.search(r'@app\.get\("' + re.escape(path) + r'"\)\n((?:@.*\n)*)', source)
    assert m, f"route {path} not found in app/main.py"
    lim = re.search(r'@limiter\.limit\("(\d+)/(minute|hour)"\)', m.group(1))
    if not lim:
        return None
    n = int(lim.group(1))
    return n if lim.group(2) == "minute" else n / 60


def test_route_limits_match_app_main():
    source = (_PATH.parents[1] / "app" / "main.py").read_text()
    assert set(check.ROUTE_LIMITS) == {name for name, _ in check.ENDPOINTS} == set(_DECORATED)
    for name, path in _DECORATED.items():
        assert check.ROUTE_LIMITS[name] == _declared_limit(source, path), name
