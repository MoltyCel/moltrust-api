"""Agent Skills Discovery index at /.well-known/agent-skills/index.json.

Specification: cloudflare/agent-skills-discovery-rfc v0.2.0, read at commit
1bd1167983fa5ac9cd47987710c525308eda1a98 (README.md). The parts this module
relies on:

  :165   the index lives at /.well-known/agent-skills/index.json
  :169   top-level "$schema" with the 0.2.0 schema URI, plus "skills"
  :214   each entry: name, type ("skill-md" | "archive"), description, url,
         digest — all required
  :227   url resolves against the index URL; absolute URLs are allowed
  :249   digest = "sha256:" + 64 lowercase hex over the artifact's raw bytes
  :398   index as application/json, SKILL.md as text/markdown or text/plain,
         GET and HEAD, 404 for skills that do not exist

The schema URI is an opaque identifier (:175) and does not resolve, so there is
no JSON Schema document to validate against. `validate_index` below checks the
field table at :207-220 instead, and the tests run it over the served index.

Two public skills exist (ops AR-1024, 24.10.2026):

  moltrust-identity  skills/moltrust-identity/SKILL.md in this repo. Served
                     here, digest computed from the file bytes at import, so
                     the index cannot disagree with what the route returns.
  moltrust           openclaw-skill/SKILL.md in MoltyCel/moltrust-sdk. Linked
                     at a pinned commit on raw.githubusercontent.com: the bytes
                     behind a commit URL cannot change, so a fixed digest stays
                     true. A newer revision of that file is a new pin here.
"""
from __future__ import annotations

import hashlib
import os
import re

import yaml

INDEX_PATH = "/.well-known/agent-skills/index.json"
SCHEMA_URI = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
SKILL_MD_TYPE = "text/markdown; charset=utf-8"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Skills whose SKILL.md lives in this repo and is served by this API.
LOCAL_SKILLS = {
    "moltrust-identity": "skills/moltrust-identity/SKILL.md",
}

# Skills published elsewhere, linked at an immutable URL. Digest recorded from
# the raw bytes at that URL on 24.10.2026.
_SDK_COMMIT = "ed93eafcd3c80d32cb734a53578afda9ba863784"
PINNED_SKILLS = [
    {
        "name": "moltrust",
        "type": "skill-md",
        "description": (
            "Trust layer for AI agents. Register DIDs, verify identities, issue W3C "
            "Verifiable Credentials, query reputation scores, and rate other agents "
            "via the MolTrust API."
        ),
        "url": (f"https://raw.githubusercontent.com/MoltyCel/moltrust-sdk/{_SDK_COMMIT}"
                "/openclaw-skill/SKILL.md"),
        "digest": "sha256:5554ce31e7ed51fa9486e7d1a951f17b8b2eafe27fe79d9e5aec24d4db5b8514",
    },
]

_NAME = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def skill_md_path(name: str) -> str:
    return f"/.well-known/agent-skills/{name}/SKILL.md"


def _frontmatter(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md without YAML frontmatter")
    head, _, _ = text[4:].partition("\n---")
    return yaml.safe_load(head) or {}


def _load_local() -> dict[str, bytes]:
    out = {}
    for name, rel in LOCAL_SKILLS.items():
        with open(os.path.join(_REPO_ROOT, rel), "rb") as f:
            out[name] = f.read()
    return out


# Read once. A changed SKILL.md reaches production with a deploy, and the
# deploy restarts the process, so bytes and digest move together.
LOCAL_BYTES = _load_local()


def build_index() -> dict:
    skills = []
    for name, raw in LOCAL_BYTES.items():
        fm = _frontmatter(raw)
        if fm.get("name") != name:
            raise ValueError(f"{LOCAL_SKILLS[name]}: frontmatter name {fm.get('name')!r} != {name!r}")
        skills.append({
            "name": name,
            "type": "skill-md",
            "description": fm["description"],
            "url": skill_md_path(name),
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        })
    skills.extend(dict(s) for s in PINNED_SKILLS)
    return {"$schema": SCHEMA_URI, "skills": skills}


def validate_index(index: dict) -> list[str]:
    """Problems with an index under the v0.2.0 field table; empty means valid."""
    problems = []
    if index.get("$schema") != SCHEMA_URI:
        problems.append(f"$schema is {index.get('$schema')!r}")
    skills = index.get("skills")
    if not isinstance(skills, list):
        return problems + ["skills is not an array"]
    seen = set()
    for i, s in enumerate(skills):
        where = f"skills[{i}]"
        for field in ("name", "type", "description", "url", "digest"):
            if not isinstance(s.get(field), str) or not s.get(field):
                problems.append(f"{where}.{field} missing or not a string")
        name = s.get("name", "")
        if isinstance(name, str) and not _NAME.match(name):
            problems.append(f"{where}.name {name!r} breaks the naming rule")
        if name in seen:
            problems.append(f"{where}.name {name!r} twice")
        seen.add(name)
        if s.get("type") not in ("skill-md", "archive"):
            problems.append(f"{where}.type {s.get('type')!r}")
        if isinstance(s.get("description"), str) and len(s["description"]) > 1024:
            problems.append(f"{where}.description over 1024 characters")
        if isinstance(s.get("digest"), str) and not _DIGEST.match(s["digest"]):
            problems.append(f"{where}.digest {s['digest']!r}")
    return problems
