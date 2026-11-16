"""The npm publish workflow for @moltrust/mpp follows the one for @moltrust/x402.

Origin (ops NPM-HYG, 16.11.2026): the npm page of @moltrust/mpp linked
MoltyCel/moltrust-mpp, a repository that does not exist, and the published
1.0.3 carried an option (failBehavior) this repo did not have. 1.0.4 names
this repository with its directory, restores the option, and goes out over
Trusted Publishing from .github/workflows/npm-publish-mpp.yml. npm trusts that
file by name.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "npm-publish-mpp.yml"
PKG = ROOT / "packages" / "mpp" / "package.json"


def wf():
    return WF.read_text(encoding="utf-8")


def test_workflow_file_name_is_the_one_npm_trusts():
    assert WF.is_file()
    assert "workflow npm-publish-mpp.yml" in wf()


def test_only_the_publish_job_gets_an_oidc_token():
    src = wf()
    assert src.count("id-token: write") == 1
    publish = src.split("\n  publish:\n", 1)[1].split("\n  verify:\n", 1)[0]
    assert "id-token: write" in publish
    assert "environment: npm-publish" in publish
    assert "--provenance" in publish
    assert "working-directory: packages/mpp" in publish


def test_no_stored_token_is_read():
    src = wf()
    assert "secrets." not in src
    assert "NODE_AUTH_TOKEN:" not in src
    assert "_authToken" not in src
    assert "registry-url:" not in src


def test_checks():
    src = wf()
    assert "  PKG: packages/mpp\n" in src
    assert 'npm view "$NAME@$VERSION" version' in src
    assert 'grep -niE "generated with|claude code|co-authored-by"' in src
    assert "node --test packages/mpp/test/*.test.js" in src
    assert "NPM_CONFIG_USERCONFIG: /dev/null" in src


def test_tag_must_match_version_and_be_on_main():
    src = wf()
    assert '"mpp-v*"' in src
    assert '"mpp-v$VERSION"' in src
    assert "merge-base --is-ancestor" in src
    assert "refs/heads/main" in src


def test_verify_waits_for_the_registry_before_installing():
    verify = wf().split("\n  verify:\n", 1)[1]
    warten = verify.index('bash scripts/npm_warte_auf_version.sh "$NAME" "$VERSION"')
    install = verify.index("npm install --no-audit --no-fund --prefer-online")
    assert warten < install


def test_actions_pinned_by_full_sha():
    for line in wf().splitlines():
        m = re.search(r"uses:\s*(\S+)", line)
        if m:
            assert re.search(r"@[0-9a-f]{40}$", m.group(1)), line


def test_package_names_this_repository_and_a_newer_version():
    pkg = json.loads(PKG.read_text(encoding="utf-8"))
    repo = pkg.get("repository") or {}
    assert repo.get("url") == "git+https://github.com/MoltyCel/moltrust-api.git"
    assert repo.get("directory") == "packages/mpp"
    # 1.0.3 is on npm and cannot be republished.
    major, minor, patch = (int(x) for x in pkg["version"].split("."))
    assert (major, minor, patch) > (1, 0, 3)


def test_fail_behavior_is_back_in_code_and_types():
    js = (ROOT / "packages" / "mpp" / "index.js").read_text(encoding="utf-8")
    dts = (ROOT / "packages" / "mpp" / "index.d.ts").read_text(encoding="utf-8")
    assert "failBehavior = 'open'" in js and "failBehavior === 'closed'" in js
    assert "failBehavior?: 'open' | 'closed';" in dts
