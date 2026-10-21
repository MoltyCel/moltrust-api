"""The judge of c-signing-routes-guarded, with both probes.

The current app/main.py has to pass, and each way the guard can be lost has to
fail: guard removed, and a signing call moved above the guard.
"""
import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_signing", _ROOT / "scripts" / "check_signing_routes_guarded.py")
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

MAIN = (_ROOT / "app" / "main.py").read_text(encoding="utf-8")


def test_current_main_is_guarded():
    assert check.check(MAIN) == []


def test_missing_guard_is_found():
    src = MAIN.replace(
        "await _require_did_owner_or_admin(request, api_key, body.subject_did)",
        "pass", 1)
    assert src != MAIN
    problems = check.check(src)
    assert any(p.startswith("/credentials/issue") and "no call" in p for p in problems)


def test_side_effect_before_guard_is_found():
    src = '''
@app.post("/music/credential/issue")
async def h(request, body, x_api_key):
    await conn.execute("INSERT ...")
    await _require_did_owner_or_admin(request, x_api_key, body.agent_did)

@app.post("/credentials/issue")
async def a(request, body, api_key):
    await _require_did_owner_or_admin(request, api_key, body.subject_did)

@app.post("/anchors/batch")
async def b(api_key):
    await _sign_guard.require_bound_key(db_pool, api_key)

@app.post("/identity/erc8004/validate")
async def c(api_key):
    await _sign_guard.require_bound_key(db_pool, api_key)
'''
    problems = check.check(src)
    assert len(problems) == 1
    assert problems[0].startswith("/music/credential/issue") and "execute" in problems[0]


def test_missing_handler_is_found():
    assert any("handler not found" in p for p in check.check("x = 1\n"))
