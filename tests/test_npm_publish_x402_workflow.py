"""The npm publish workflow for @moltrust/x402 keeps the rules of the script.

Origin (ops NPM-TP, 13.11.2026): @moltrust/x402 moves from a stored npm token
(scripts/publish_npm_x402.sh) to Trusted Publishing from
.github/workflows/npm-publish-x402.yml. npm trusts that file by name, so the
name is part of the npm-side configuration and must not drift. The workflow
has to carry the same checks as the script, and the provenance publish fails
unless package.json names this repository.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "npm-publish-x402.yml"
PKG = ROOT / "packages" / "x402" / "package.json"


def wf():
    return WF.read_text(encoding="utf-8")


def test_workflow_file_name_is_the_one_npm_trusts():
    assert WF.is_file()
    assert "workflow npm-publish-x402.yml" in wf()


def test_only_the_publish_job_gets_an_oidc_token():
    src = wf()
    assert src.count("id-token: write") == 1
    publish = src.split("\n  publish:\n", 1)[1].split("\n  verify:\n", 1)[0]
    assert "id-token: write" in publish
    assert "environment: npm-publish" in publish
    assert "--provenance" in publish


def test_no_stored_token_is_read():
    src = wf()
    assert "secrets." not in src
    assert "NODE_AUTH_TOKEN:" not in src
    assert "_authToken" not in src
    assert "registry-url:" not in src


def test_same_checks_as_the_script():
    src = wf()
    assert 'npm view "$NAME@$VERSION" version' in src
    assert 'grep -niE "generated with|claude code|co-authored-by"' in src
    assert "node --test packages/x402/test/*.test.js" in src
    assert "node --test packages/x402/test/ " not in src
    assert 'MOLTRUST_NO_AUTO_REGISTER: "1"' in src
    # install probe without any token, from an empty directory
    assert "NPM_CONFIG_USERCONFIG: /dev/null" in src
    assert "mktemp -d" in src


def test_tag_must_match_version_and_be_on_main():
    src = wf()
    assert '"x402-v*"' in src
    assert '"x402-v$VERSION"' in src
    assert "merge-base --is-ancestor" in src
    assert 'refs/heads/main' in src


def test_actions_pinned_by_full_sha():
    for line in wf().splitlines():
        m = re.search(r"uses:\s*(\S+)", line)
        if m:
            assert re.search(r"@[0-9a-f]{40}$", m.group(1)), line


def test_package_names_this_repository():
    repo = json.loads(PKG.read_text(encoding="utf-8")).get("repository") or {}
    assert repo.get("url") == "git+https://github.com/MoltyCel/moltrust-api.git"
    assert repo.get("directory") == "packages/x402"
