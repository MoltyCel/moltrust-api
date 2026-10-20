"""The chain-only example replays the same way the python reference does.

Two implementations of one recipe, so a host that writes a third can check it
against both. The recipe is published in registry-proof.json's own header:
leaf_preimage = sha256(credential_id|subject_did|credential_type|issued_at|proof_value)
and calldata_prefix = MolTrust/VC/v1.
"""
import hashlib
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EX = ROOT / "examples" / "gate-without-an-account"
node = shutil.which("node")


def sha(b):
    return hashlib.sha256(b).digest()


def replay(leaf, path):
    node_ = leaf
    for s in path:
        sib = bytes.fromhex(s["hash"])
        node_ = sha(sib + node_) if s["position"] == "left" else sha(node_ + sib)
    return node_


def test_the_example_files_exist_and_parse():
    for name in ("confirm-anchor.js", "server.js", "README.md"):
        assert (EX / name).exists(), name
    if node:
        for name in ("confirm-anchor.js", "server.js"):
            r = subprocess.run([node, "--check", str(EX / name)],
                               capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not node, reason="node not available")
def test_js_and_python_agree_on_a_replay():
    # A three-step path, both directions used.
    leaf = sha(b"leaf")
    path = [{"hash": sha(b"a").hex(), "position": "left"},
            {"hash": sha(b"b").hex(), "position": "right"},
            {"hash": sha(b"c").hex(), "position": "left"}]
    want = replay(leaf, path).hex()

    script = (f'const m = require({str(EX / "confirm-anchor.js")!r});\n'
              f'const crypto = require("node:crypto");\n'
              f'const leaf = Buffer.from({leaf.hex()!r}, "hex");\n'
              f'const path = {json.dumps(path)};\n'
              f'process.stdout.write(m.replay(leaf, path).toString("hex"));\n')
    r = subprocess.run([node, "-e", script], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == want


@pytest.mark.skipif(not node, reason="node not available")
def test_a_missing_anchor_tx_is_an_error_not_a_pass():
    script = (f'const m = require({str(EX / "confirm-anchor.js")!r});\n'
              f'm.confirmAnchor({{merkle_proof: {{leaf: "00", path: []}}}})\n'
              f'  .then(() => process.stdout.write("PASSED"))\n'
              f'  .catch(e => process.stdout.write("THREW: " + e.message));\n')
    r = subprocess.run([node, "-e", script], capture_output=True, text=True,
                       timeout=60)
    assert "THREW" in r.stdout and "anchor_tx missing" in r.stdout


def test_the_readme_carries_the_discount_line_and_the_measurement():
    t = (EX / "README.md").read_text()
    assert "DISCOUNT_BPS" in t
    assert "1 second" in t and "zero dependencies" in t
    # The honest limits have to stay in: each check's blind spot named.
    assert "does not prove the numbers are true" in t
    assert "does not prove the credential was anchored" in t
    assert "Failure is full price" in t
