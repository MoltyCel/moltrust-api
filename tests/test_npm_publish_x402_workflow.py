"""The npm publish workflow for @moltrust/x402 keeps the rules of the script.

Origin (ops NPM-TP, 13.11.2026): @moltrust/x402 moves from a stored npm token
(scripts/publish_npm_x402.sh, removed 16.11.2026) to Trusted Publishing from
.github/workflows/npm-publish-x402.yml. npm trusts that file by name, so the
name is part of the npm-side configuration and must not drift. The workflow
has to carry the same checks as the script, and the provenance publish fails
unless package.json names this repository.
"""
import json
import os
import pathlib
import re
import subprocess
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "npm-publish-x402.yml"
PKG = ROOT / "packages" / "x402" / "package.json"
WARTE = ROOT / "scripts" / "npm_warte_auf_version.sh"


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


def test_the_token_script_is_gone():
    # NPM-TP step 4: no stored token, so no script that reads one.
    assert not (ROOT / "scripts" / "publish_npm_x402.sh").exists()


# Install probe (run 43832080931, 16.11.2026): wait for the registry, not for
# a guessed time.

def test_verify_waits_for_the_registry_before_installing():
    verify = wf().split("\n  verify:\n", 1)[1]
    warten = verify.index('bash scripts/npm_warte_auf_version.sh "$NAME" "$VERSION"')
    install = verify.index("npm install --no-audit --no-fund --prefer-online")
    assert warten < install
    assert "sleep 30" not in verify
    assert "sparse-checkout: scripts/npm_warte_auf_version.sh" in verify


def _fake_npm(tmp_path, antworten):
    """An npm that answers `view` from a list, one line per call."""
    liste = tmp_path / "antworten"
    liste.write_text("\n".join(antworten) + "\n")
    zaehler = tmp_path / "aufrufe"
    npm = tmp_path / "npm"
    npm.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> {zaehler}
        n=$(wc -l < {zaehler})
        a=$(sed -n "${{n}}p" {liste})
        [ -z "$a" ] && a=$(tail -n1 {liste})
        [ "$a" = "-" ] && {{ echo "npm error code E404" >&2; exit 1; }}
        echo "$a"
        """))
    npm.chmod(0o755)
    return npm, zaehler


def _warte(npm, versuche, pause, timeout):
    env = {**os.environ, "NPM": str(npm),
           "NPM_WARTE_VERSUCHE": str(versuche), "NPM_WARTE_PAUSE": str(pause)}
    return subprocess.run(["bash", str(WARTE), "@moltrust/x402", "2.2.1"],
                          env=env, capture_output=True, text=True, timeout=timeout)


def test_silence_probe_first_answer_hits_and_nothing_waits(tmp_path):
    # Pause 60 s, timeout 10 s: a sleep would fail the test.
    npm, zaehler = _fake_npm(tmp_path, ["2.2.1"])
    r = _warte(npm, 40, 60, 10)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "attempt 1 of 40" in r.stdout
    assert len(zaehler.read_text().splitlines()) == 1
    assert "--prefer-online" in zaehler.read_text()


def test_late_version_is_found_on_a_later_attempt(tmp_path):
    npm, zaehler = _fake_npm(tmp_path, ["-", "-", "2.2.1"])
    r = _warte(npm, 5, 0, 10)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "attempt 3 of 5" in r.stdout
    assert len(zaehler.read_text().splitlines()) == 3


def test_finding_probe_version_never_comes_and_the_bound_holds(tmp_path):
    npm, zaehler = _fake_npm(tmp_path, ["-"])
    r = _warte(npm, 4, 0, 10)
    assert r.returncode == 1
    assert "::error::@moltrust/x402@2.2.1 is not on the registry after 4 attempts" in r.stdout
    assert len(zaehler.read_text().splitlines()) == 4


def test_an_older_version_does_not_count(tmp_path):
    # npm view NAME@VERSION never answers another version, but a fake or a
    # proxy might; only the exact version ends the wait.
    npm, _ = _fake_npm(tmp_path, ["2.2.0"])
    r = _warte(npm, 2, 0, 10)
    assert r.returncode == 1


def test_missing_arguments_are_a_usage_error():
    r = subprocess.run(["bash", str(WARTE)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 2


def test_dry_run_is_skipped_when_a_pull_request_does_not_bump_the_version():
    # Run 43865400726 (16.11.2026): npm publish --dry-run fails with "cannot
    # publish over the previously published versions" on every PR after 2.2.1.
    src = wf()
    assert "id: onnpm" in src
    assert 'echo "exists=true" >> "$GITHUB_OUTPUT"' in src
    dry = src.split("- name: npm publish --dry-run", 1)[1].split("\n\n", 1)[0]
    assert "if: steps.onnpm.outputs.exists != 'true'" in dry
