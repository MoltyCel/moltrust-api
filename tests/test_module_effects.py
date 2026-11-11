"""A module body defines, it does not act (WORKFLOW 18, 2026-10-09)."""
import ast
import pathlib
import subprocess
import sys

from scripts import module_effects as me

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_no_new_module_level_effects_in_this_repository():
    r = subprocess.run([sys.executable, "scripts/module_effects.py", "--check", "."],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def _kinds(src):
    tree = ast.parse(src)
    out = []
    for st in me.module_level_statements(tree.body):
        for c in me.calls_in(st):
            out.append(me.classify(me.dotted(c.func), c))
    return out


def test_a_send_at_module_level_is_found_and_a_guarded_one_is_not():
    assert "send" in _kinds("import requests\nrequests.post('https://x', json={})\n")
    assert "send" not in _kinds("import requests\nif __name__ == '__main__':\n    requests.post('x')\n")
    assert "send" not in _kinds("def main():\n    requests.post('x')\n")


def test_effects_inside_module_level_if_try_and_loops_are_found():
    src = "try:\n    notify.send_telegram('x', channel='a')\nexcept Exception:\n    pass\n" \
          "for x in y:\n    os.remove(x)\nif FLAG:\n    asyncio.run(main())\n"
    k = _kinds(src)
    assert "send" in k and "delete" in k and "process/exit/sleep" in k


def test_file_write_mode_matters():
    assert "file write" in _kinds("open('f', 'w').write('x')\n")
    assert "file write" not in _kinds("data = open('f').read()\n")


def test_every_baseline_entry_has_a_reason():
    import json
    d = json.loads((ROOT / "config" / "module_effects_baseline.json").read_text())
    assert d["entries"] and all(e.get("reason") for e in d["entries"])
    assert not any(e["kind"] in ("send", "delete", "db write", "process (runs main)") for e in d["entries"])
