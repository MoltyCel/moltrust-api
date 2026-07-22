"""Reseller-ADMIN privilege boundary — the load-bearing security test.

Anchored on the existing moltrust.ch/admin session (app.admin_auth). Proves:
 - a reseller login NEVER reaches admin endpoints,
 - a non-allowlisted admin is 403 (fail-closed),
 - an allowlisted admin without TOTP can only enroll, not act,
 - TOTP is unskippable: no elevated token without a valid code,
 - every admin write is audited.

Runs against moltstack_sandbox (conftest default).
"""
import os
import time
import uuid
import pytest
import pytest_asyncio

import app.main as _m
from app import admin_auth
from app import reseller_admin as RA

pytestmark = pytest.mark.asyncio(loop_scope="module")

LISTED = "lars-test"
UNLISTED = "other-admin-test"
TOTP_KEY = "test-totp-key-xyz"


def _pool():
    return _m.db_pool


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app_module():
    from app.main import app
    for h in getattr(app.router, "on_startup", []):
        await h()
    yield app


@pytest_asyncio.fixture(loop_scope="module")
async def admin_env(app_module):
    from httpx import AsyncClient, ASGITransport

    prev_users = os.environ.get("RESELLER_ADMIN_USERS")
    prev_key = os.environ.get("RESELLER_ADMIN_TOTP_KEY")
    os.environ["RESELLER_ADMIN_USERS"] = LISTED
    os.environ["RESELLER_ADMIN_TOTP_KEY"] = TOTP_KEY

    # Inject two test admins into the in-memory admin registry (no password needed:
    # create_session only reads the role).
    admin_auth.ADMIN_USERS[LISTED] = {"hash": "x", "role": "superadmin"}
    admin_auth.ADMIN_USERS[UNLISTED] = {"hash": "x", "role": "admin"}

    async with _pool().acquire() as conn:
        await RA.ensure_reseller_admin_tables(conn)
        # clean any prior test state
        for u in (LISTED, UNLISTED):
            await conn.execute("DELETE FROM reseller_admin_sessions WHERE username=$1", u)
            await conn.execute("DELETE FROM reseller_admin_2fa WHERE username=$1", u)

    transport = ASGITransport(app=app_module)
    client = AsyncClient(transport=transport, base_url="http://test")
    made_payers = []

    def admin_token(username):
        tok, _ = admin_auth.create_session(username)
        return tok

    async def req(method, path, admin_tok=None, elevated=None, **kw):
        headers = dict(kw.pop("headers", {}))
        if admin_tok:
            headers["Authorization"] = f"Bearer {admin_tok}"
        if elevated:
            headers["X-Reseller-Admin-Token"] = elevated
        last = None
        for _ in range(6):
            try:
                r = await client.request(method, path, headers=headers, **kw)
                r.json()
            except Exception:
                last = locals().get("r"); continue
            return r
        return last

    env = type("E", (), {"client": client, "admin_token": staticmethod(admin_token),
                         "req": staticmethod(req), "made_payers": made_payers})
    try:
        yield env
    finally:
        await client.aclose()
        async with _pool().acquire() as conn:
            for u in (LISTED, UNLISTED):
                await conn.execute("DELETE FROM reseller_admin_sessions WHERE username=$1", u)
                await conn.execute("DELETE FROM reseller_admin_2fa WHERE username=$1", u)
            for pr in made_payers:
                await conn.execute("DELETE FROM agent_payer WHERE payer_ref=$1", pr)
                await conn.execute("DELETE FROM reseller_sessions WHERE payer_ref=$1", pr)
                await conn.execute("DELETE FROM reseller_accounts WHERE payer_ref=$1", pr)
                await conn.execute("DELETE FROM accounts WHERE payer_ref=$1", pr)
        admin_auth.ADMIN_USERS.pop(LISTED, None)
        admin_auth.ADMIN_USERS.pop(UNLISTED, None)
        if prev_users is None:
            os.environ.pop("RESELLER_ADMIN_USERS", None)
        else:
            os.environ["RESELLER_ADMIN_USERS"] = prev_users
        if prev_key is None:
            os.environ.pop("RESELLER_ADMIN_TOTP_KEY", None)
        else:
            os.environ["RESELLER_ADMIN_TOTP_KEY"] = prev_key


async def _enroll_and_confirm(env, tok):
    r = await env.req("POST", "/admin/reseller/2fa/enroll", admin_tok=tok)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    code = RA.totp_at(secret, time.time())
    c = await env.req("POST", "/admin/reseller/2fa/confirm", admin_tok=tok, json={"code": code})
    assert c.status_code == 200, c.text
    return secret


# 1 — reseller login token NEVER reaches admin endpoints
async def test_reseller_token_cannot_reach_admin(admin_env):
    # a random/reseller-style bearer is not in the in-memory admin SESSIONS
    for path in ("/admin/reseller/2fa/status", "/admin/reseller/list"):
        r = await admin_env.req("GET", path, admin_tok="reseller-or-garbage-token")
        assert r.status_code in (401, 403), f"{path}: {r.status_code}"


# 2 — non-allowlisted admin: valid session but not listed => 403 (fail-closed by name)
async def test_unlisted_admin_forbidden(admin_env):
    tok = admin_env.admin_token(UNLISTED)
    for path in ("/admin/reseller/2fa/status", "/admin/reseller/2fa/enroll"):
        r = await admin_env.req("POST" if "enroll" in path else "GET", path, admin_tok=tok)
        assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"


# 3 — listed admin WITHOUT confirmed TOTP: can enroll, but cannot elevate or act
async def test_listed_without_totp_cannot_act(admin_env):
    tok = admin_env.admin_token(LISTED)
    st = await admin_env.req("GET", "/admin/reseller/2fa/status", admin_tok=tok)
    assert st.status_code == 200 and st.json()["confirmed"] is False
    # elevate without enrollment -> 403
    el = await admin_env.req("POST", "/admin/reseller/elevate", admin_tok=tok, json={"code": "000000"})
    assert el.status_code == 403
    # data endpoint without elevated token -> 401
    ls = await admin_env.req("GET", "/admin/reseller/list", admin_tok=tok)
    assert ls.status_code == 401


# 4 — full happy path: enroll -> confirm -> elevate -> act; TOTP is required
async def test_totp_flow_and_admin_actions(admin_env):
    tok = admin_env.admin_token(LISTED)
    secret = await _enroll_and_confirm(admin_env, tok)

    # elevate with WRONG code -> 403 (cannot skip the second factor)
    bad = await admin_env.req("POST", "/admin/reseller/elevate", admin_tok=tok, json={"code": "111111"})
    assert bad.status_code == 403

    # elevate with correct code -> elevated token
    good = await admin_env.req("POST", "/admin/reseller/elevate", admin_tok=tok, json={"code": RA.totp_at(secret, time.time())})
    assert good.status_code == 200, good.text
    elev = good.json()["token"]

    # cross-tenant: create a reseller, see it in /list, drill down
    login = f"tcadm_{uuid.uuid4().hex[:8]}"
    cr = await admin_env.req("POST", "/admin/reseller/create", elevated=elev,
                             json={"login": login, "password": "pw", "wholesale_price_cents": 400,
                                   "display_name": login.upper(), "email": f"{login}@t.local", "vat_id": "DE811569869"})
    assert cr.status_code == 200, cr.text
    pr = cr.json()["payer_ref"]; admin_env.made_payers.append(pr)

    lst = await admin_env.req("GET", "/admin/reseller/list", elevated=elev)
    assert lst.status_code == 200
    assert any(x["payer_ref"] == pr for x in lst.json()["resellers"])

    det = await admin_env.req("GET", f"/admin/reseller/tenant/{pr}", elevated=elev)
    assert det.status_code == 200 and det.json()["customer_vat_id"] == "DE811569869"

    # admin assigns an agent cross-tenant
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    asg = await admin_env.req("POST", f"/admin/reseller/tenant/{pr}/agents", elevated=elev, json={"did": did})
    assert asg.status_code == 200 and asg.json()["status"] == "created"

    # data endpoint with the admin SESSION token in the elevated slot must fail
    spoof = await admin_env.req("GET", "/admin/reseller/list", elevated=tok)
    assert spoof.status_code == 401

    # audit recorded the writes
    async with _pool().acquire() as conn:
        actions = {r["action"] for r in await conn.fetch(
            "SELECT action FROM reseller_admin_audit WHERE actor=$1", LISTED)}
    assert {"2fa_confirmed", "elevate", "create_reseller", "assign_agent"} <= actions


# 5 — re-enroll of a confirmed secret requires the current code
async def test_reenroll_requires_current_code(admin_env):
    tok = admin_env.admin_token(LISTED)
    secret = await _enroll_and_confirm(admin_env, tok)
    # re-enroll without code -> 403
    r = await admin_env.req("POST", "/admin/reseller/2fa/enroll", admin_tok=tok, json={})
    assert r.status_code == 403
    # with current code -> allowed
    r2 = await admin_env.req("POST", "/admin/reseller/2fa/enroll", admin_tok=tok, json={"code": RA.totp_at(secret, time.time())})
    assert r2.status_code == 200


# 6 — fail-closed: empty allowlist locks everyone out (even the listed admin)
async def test_failclosed_empty_allowlist(admin_env):
    tok = admin_env.admin_token(LISTED)
    prev = os.environ.get("RESELLER_ADMIN_USERS")
    os.environ["RESELLER_ADMIN_USERS"] = ""
    try:
        r = await admin_env.req("GET", "/admin/reseller/2fa/status", admin_tok=tok)
        assert r.status_code == 403
    finally:
        os.environ["RESELLER_ADMIN_USERS"] = prev


# 7 — fail-closed: no TOTP key => enrollment impossible
async def test_failclosed_no_totp_key(admin_env):
    tok = admin_env.admin_token(LISTED)
    prev = os.environ.get("RESELLER_ADMIN_TOTP_KEY")
    os.environ.pop("RESELLER_ADMIN_TOTP_KEY", None)
    try:
        st = await admin_env.req("GET", "/admin/reseller/2fa/status", admin_tok=tok)
        assert st.status_code == 200 and st.json()["key_configured"] is False
        en = await admin_env.req("POST", "/admin/reseller/2fa/enroll", admin_tok=tok)
        assert en.status_code == 503
    finally:
        os.environ["RESELLER_ADMIN_TOTP_KEY"] = prev


# admin: reseller list shows active as billing base, pending separately
async def test_admin_active_pending_counts(admin_env):
    tok = admin_env.admin_token(LISTED)
    secret = await _enroll_and_confirm(admin_env, tok)
    elev = (await admin_env.req("POST", "/admin/reseller/elevate", admin_tok=tok, json={"code": RA.totp_at(secret, time.time())})).json()["token"]
    login = f"tcadm_{uuid.uuid4().hex[:8]}"
    pr = (await admin_env.req("POST", "/admin/reseller/create", elevated=elev,
          json={"login": login, "password": "pw", "wholesale_price_cents": 400})).json()["payer_ref"]
    admin_env.made_payers.append(pr)
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    await admin_env.req("POST", f"/admin/reseller/tenant/{pr}/agents", elevated=elev, json={"did": did})
    # not in `agents` -> pending, not billed
    row = [x for x in (await admin_env.req("GET", "/admin/reseller/list", elevated=elev)).json()["resellers"] if x["payer_ref"] == pr][0]
    assert row["active_count"] == 0 and row["pending_count"] == 1 and row["month_total_cents"] == 0
    async with _pool().acquire() as conn:
        await conn.execute("INSERT INTO agents (did, display_name, platform, agent_type) VALUES ($1,'tc','test','external') ON CONFLICT (did) DO NOTHING", did)
    try:
        row2 = [x for x in (await admin_env.req("GET", "/admin/reseller/list", elevated=elev)).json()["resellers"] if x["payer_ref"] == pr][0]
        assert row2["active_count"] == 1 and row2["pending_count"] == 0 and row2["month_total_cents"] == 400
    finally:
        async with _pool().acquire() as conn:
            await conn.execute("DELETE FROM agent_payer WHERE did=$1", did)
            await conn.execute("DELETE FROM agents WHERE did=$1", did)
