"""No test or tool in packages/x402 registers a DID at api.moltrust.ch.

Origin (ops X402-SELFREG): on 09.11.2026 21:50:10Z the npm publish of
@moltrust/x402 2.2.0 ran `node --test packages/x402/test/`. Node's runner also
executes every .js file in a directory named test, so make-fixtures.js ran; its
gate had no operatorDid and no opt-out, and autoregister.js registered a real
DID with platform usage-x402. That DID counts in the "usage" bucket as if a
stranger had installed the package.

Every .js file under packages/x402/test that loads the gate sets
MOLTRUST_NO_AUTO_REGISTER before it does, and the publish script exports it
and runs only *.test.js. A new file without the opt-out turns this red.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "packages" / "x402" / "test"
OPT_OUT = "process.env.MOLTRUST_NO_AUTO_REGISTER = '1'"
LOADS_GATE = re.compile(r"require\(['\"]\.\./(index|autoregister)?['\"]\)|require\(['\"]\.\.['\"]\)")


def files_loading_the_gate():
    return [p for p in sorted(TEST_DIR.glob("*.js")) if LOADS_GATE.search(p.read_text(encoding="utf-8"))]


def test_every_file_that_loads_the_gate_opts_out_first():
    files = files_loading_the_gate()
    assert any(p.name == "make-fixtures.js" for p in files)
    for p in files:
        src = p.read_text(encoding="utf-8")
        if p.name == "autoregister.test.js":
            continue  # injects a fake fetch; nothing reaches the network
        assert OPT_OUT in src, p.name
        assert src.index(OPT_OUT) < LOADS_GATE.search(src).start(), p.name


def test_the_counter_check_finds_a_file_without_the_opt_out(tmp_path):
    bad = "const { gateFor } = require('../index');\n"
    assert LOADS_GATE.search(bad) and OPT_OUT not in bad


def test_the_publish_script_opts_out_and_runs_only_test_files():
    sh = (ROOT / "scripts" / "publish_npm_x402.sh").read_text(encoding="utf-8")
    assert "export MOLTRUST_NO_AUTO_REGISTER=1" in sh
    assert "node --test packages/x402/test/*.test.js" in sh
    assert "node --test packages/x402/test/ " not in sh
