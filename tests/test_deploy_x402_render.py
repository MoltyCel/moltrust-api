"""deploy.sh renders /.well-known/x402.json from BASE_ADDR (ops backlog S2).

The served manifest carries the receiving address. It is rendered on every web
deploy from moltrust-web's .well-known/x402.json.tpl and the BASE_ADDR line of
the secrets file, so moving the wallet needs no commit: change the secrets
file, deploy the same commit again. The tests run the deploy.sh functions
themselves, sourced the way failure_paths.py does it.
"""
import json
import os
import pathlib
import subprocess

import pytest

DEPLOY_SH = pathlib.Path(__file__).resolve().parents[1] / "ops" / "deploy" / "deploy.sh"
A = "0x" + "a1" * 20
B = "0x" + "b2" * 20
TPL = {"x402Version": 2, "accepts": [{"network": "eip155:8453", "payTo": "{{BASE_ADDR}}"}],
       "payTo": "{{BASE_ADDR}}"}


def run(script: str, secrets: pathlib.Path) -> subprocess.CompletedProcess:
    env = {"DEPLOY_SH_FUNCTIONS_ONLY": "1", "PATH": os.environ["PATH"],
           "SECRETS_FILE": str(secrets)}
    return subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_SH}"; LOG=/dev/null; log() {{ echo "$1" >&2; }}; {script}'],
        capture_output=True, text=True, env=env)


@pytest.fixture()
def web(tmp_path):
    d = tmp_path / "web" / ".well-known"
    d.mkdir(parents=True)
    (d / "x402.json.tpl").write_text(json.dumps(TPL, indent=2) + "\n")
    return tmp_path / "web"


def manifest(web):
    return json.loads((web / ".well-known" / "x402.json").read_text())


def test_a_change_of_base_addr_reaches_the_manifest_without_a_commit(tmp_path, web):
    secrets = tmp_path / "secrets"
    secrets.write_text(f"FOO=1\nBASE_ADDR={A}\nBAR=2\n")
    assert run(f'render_x402 "{web}"', secrets).returncode == 0
    assert manifest(web)["payTo"] == A and manifest(web)["accepts"][0]["payTo"] == A
    secrets.write_text(f"FOO=1\nexport BASE_ADDR=\"{B}\"\n")
    assert run(f'render_x402 "{web}"', secrets).returncode == 0
    assert manifest(web)["payTo"] == B and manifest(web)["accepts"][0]["payTo"] == B
    assert "{{BASE_ADDR}}" in (web / ".well-known" / "x402.json.tpl").read_text()


@pytest.mark.parametrize("line", ["", "BASE_ADDR=", "BASE_ADDR=0x1234", "BASE_ADDRX=" + A,
                                  "# BASE_ADDR=" + A])
def test_missing_or_malformed_base_addr_fails_and_writes_nothing(tmp_path, web, line):
    secrets = tmp_path / "secrets"
    secrets.write_text(line + "\n")
    r = run(f'render_x402 "{web}"', secrets)
    assert r.returncode == 1
    assert not (web / ".well-known" / "x402.json").exists()


def test_no_template_is_reported_not_failed(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.write_text(f"BASE_ADDR={A}\n")
    (tmp_path / "old" / ".well-known").mkdir(parents=True)
    assert run(f'render_x402 "{tmp_path / "old"}"', secrets).returncode == 2


def test_template_without_placeholder_fails(tmp_path, web):
    (web / ".well-known" / "x402.json.tpl").write_text('{"payTo": "' + A + '"}\n')
    secrets = tmp_path / "secrets"
    secrets.write_text(f"BASE_ADDR={B}\n")
    assert run(f'render_x402 "{web}"', secrets).returncode == 1


def test_base_addr_reads_only_its_own_line(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.write_text(f"BASE_ADDR_OLD=0x{'cc' * 20}\nBASE_ADDR={A}\n")
    r = run("base_addr", secrets)
    assert r.returncode == 0 and r.stdout == A
