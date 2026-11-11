"""The agent-skills guard (scripts/agent_skills_guard.py): every linked skill
loads and matches its digest, each failure named (ops AR-1024)."""
import hashlib
import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import agent_skills_guard as G  # noqa: E402

A, B = b"---\nname: a\n---\n", b"---\nname: b\n---\n"


def _index(*pairs):
    return {"skills": [{"name": n, "url": f"/{n}", "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
                       for n, raw in pairs]}


def test_all_skills_load_and_match():
    ok, lines = G.judge(_index(("a", A), ("b", B)), {"a": A, "b": B})
    assert ok
    assert lines[-1].startswith("agent-skills-guard=ok 2 skills")


def test_a_skill_behind_a_404_is_red():
    # The state of 26.10.2026: index valid, second link not loadable.
    ok, lines = G.judge(_index(("a", A), ("b", B)), {"a": A, "b": "UNLOADABLE x: HTTPError 404"})
    assert not ok
    assert lines[-1] == "agent-skills-guard=FAIL b unreadable"


def test_changed_bytes_are_red():
    ok, lines = G.judge(_index(("a", A)), {"a": A + b"x"})
    assert not ok
    assert "a digest" in lines[-1]


def test_an_empty_index_is_red():
    ok, lines = G.judge({"skills": []}, {})
    assert not ok


def test_only_https_is_fetched():
    try:
        G._get("http://example.com/x")
    except G.Unreadable as e:
        assert "not https" in str(e)
    else:
        raise AssertionError("http was fetched")
