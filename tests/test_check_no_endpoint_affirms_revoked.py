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
