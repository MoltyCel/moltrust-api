"""/.well-known/agent-skills/index.json after Agent Skills Discovery v0.2.0
(ops AR-1024). The index lists our public skills; each digest must match the
bytes behind its url."""
import hashlib

from fastapi.testclient import TestClient

import app.main as main
from app import skills_discovery as sd


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool, and neither route
    # touches it.
    return TestClient(main.app)


def test_the_index_is_served_as_json():
    r = _client().get(sd.INDEX_PATH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["access-control-allow-origin"] == "*"


def test_head_on_the_index_answers():
    r = _client().head(sd.INDEX_PATH)
    assert r.status_code == 200
    assert r.content == b""


def test_the_index_is_valid_under_the_field_table():
    index = _client().get(sd.INDEX_PATH).json()
    assert sd.validate_index(index) == []
    assert index["$schema"] == "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
    assert {s["name"] for s in index["skills"]} == {"moltrust-identity", "moltrust"}


def test_a_local_digest_matches_the_served_bytes():
    c = _client()
    for s in c.get(sd.INDEX_PATH).json()["skills"]:
        if not s["url"].startswith("/"):
            continue
        r = c.get(s["url"])
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        assert s["digest"] == "sha256:" + hashlib.sha256(r.content).hexdigest()


def test_the_local_digest_is_over_the_file_in_the_repo():
    with open("skills/moltrust-identity/SKILL.md", "rb") as f:
        raw = f.read()
    entry = next(s for s in sd.build_index()["skills"] if s["name"] == "moltrust-identity")
    assert entry["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_the_description_is_the_frontmatter_one():
    entry = next(s for s in sd.build_index()["skills"] if s["name"] == "moltrust-identity")
    assert entry["description"].startswith("Register a keyless agent identity")


def test_a_pinned_skill_points_at_a_commit_not_a_branch():
    # A branch URL would change under a fixed digest.
    for s in sd.PINNED_SKILLS:
        assert "/main/" not in s["url"] and "/master/" not in s["url"]
        assert sd._SDK_COMMIT in s["url"]


def test_an_unknown_skill_is_404():
    r = _client().get("/.well-known/agent-skills/nope/SKILL.md")
    assert r.status_code == 404


class TestValidateIndex:
    def _ok(self):
        return {"$schema": sd.SCHEMA_URI, "skills": [{
            "name": "a-b", "type": "skill-md", "description": "d",
            "url": "/x", "digest": "sha256:" + "0" * 64}]}

    def test_a_good_index_passes(self):
        assert sd.validate_index(self._ok()) == []

    def test_each_rule_fails_on_its_own(self):
        cases = [
            ("$schema", None, "$schema"),
            ("name", "A-b", "naming rule"),
            ("name", "a--b", "naming rule"),
            ("name", "-ab", "naming rule"),
            ("type", "zip", "type"),
            ("digest", "sha256:ABC", "digest"),
            ("description", "x" * 1025, "1024"),
            ("url", "", "url missing"),
        ]
        for field, value, expect in cases:
            idx = self._ok()
            target = idx if field == "$schema" else idx["skills"][0]
            target[field] = value
            problems = sd.validate_index(idx)
            assert any(expect in p for p in problems), (field, value, problems)
