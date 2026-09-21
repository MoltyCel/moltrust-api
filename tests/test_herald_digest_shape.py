"""The digest posts a hook and puts the link in a reply.

The dashboard URL used to sit in the digest itself. X suppresses the reach of a
post that sends the reader away, and the hook is the tweet the timeline decides
on — so the link was costing the digest the audience it was written for. It also
ate 42 of the 280 characters, which truncated long digests mid-sentence.

These tests run over the source rather than the running agent: `run_digest`
needs the feed, Claude, the X credentials and a rendered PNG, none of which
belong in a unit test. What can be checked without any of that is the shape,
and the shape is what regressed.
"""

import ast
import os

import pytest

from agents import voice_gate

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERALD = os.path.join(REPO, "agents", "herald_v3.py")
DASHBOARD_URL = "https://moltrust.ch/integrity.html"


@pytest.fixture(scope="module")
def run_digest_source() -> str:
    tree = ast.parse(open(HERALD, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_digest":
            return ast.get_source_segment(open(HERALD, encoding="utf-8").read(), node)
    pytest.fail("run_digest not found in agents/herald_v3.py")


def test_the_hook_does_not_carry_the_link(run_digest_source):
    """`hook` is built from the generated text alone. A line that appends the
    dashboard URL to it is the regression this guards."""
    assert "hook = text" in run_digest_source
    for line in run_digest_source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "hook" not in stripped:
            continue
        if "=" not in stripped or stripped.startswith("f.write"):
            continue
        lhs, _, rhs = stripped.partition("=")
        if lhs.strip() != "hook":
            continue
        assert "DASHBOARD_URL" not in rhs, f"the link is back in the hook: {stripped}"


def test_the_reply_carries_the_link(run_digest_source):
    assert "body = " in run_digest_source
    body_lines = [ln for ln in run_digest_source.splitlines()
                  if ln.strip().startswith("body = ")]
    assert body_lines, "no body assignment"
    assert any("DASHBOARD_URL" in ln for ln in body_lines)


def test_both_parts_go_through_the_scan_as_a_thread(run_digest_source):
    """A single-part scan in "post" mode would accept a link in the hook. The
    thread mode is what requires the link to sit in the last part."""
    assert 'voice_gate.scan([hook, body], mode="thread")' in run_digest_source


def test_the_thread_is_posted_as_a_reply_chain(run_digest_source):
    assert "x_post.post_thread([hook, body]" in run_digest_source
    assert "x_post.post(tweet" not in run_digest_source


# ---------------------------------------------------------------------------
# The scan itself, on the shape the digest produces
# ---------------------------------------------------------------------------

def test_the_scan_accepts_a_linkless_hook_with_the_link_in_the_reply():
    hook = ("MoltGuard scanned 412 Polymarket markets today. Three came back with "
            "funding patterns that trace to one wallet cluster.")
    body = f"Method, the markets behind each flag, and the signals:\n{DASHBOARD_URL}"
    result = voice_gate.scan([hook, body], mode="thread")
    link_failures = [v for v in result["violations"] if "link" in v.lower()]
    assert not link_failures, result["violations"]


def test_the_scan_rejects_the_link_in_the_hook():
    """The old shape. If this ever passes, the thread mode stopped enforcing
    where the link goes and the source checks above are the only guard left."""
    hook = (f"MoltGuard scanned 412 Polymarket markets today. Three came back "
            f"with funding patterns that trace to one wallet cluster.\n\n{DASHBOARD_URL}")
    body = f"Method, the markets behind each flag, and the signals:\n{DASHBOARD_URL}"
    result = voice_gate.scan([hook, body], mode="thread")
    assert not result["ok"], "two links in a thread should not pass"
