"""The admin auth names must resolve at call time, not just at import time.

PR #332 deleted a block that contained the shadowed register-batch route and,
unnoticed, the import

    from app.admin_auth import (
        verify_password, create_session, verify_session,
        invalidate_session, ADMIN_USERS,
    )

`import app.main` still succeeded — a missing name only raises when the line
that uses it runs. So the suite stayed green while /admin/login, /admin/logout
and the 18 routes behind _get_admin_session all raised

    NameError: name 'verify_session' is not defined

on every call. These tests exercise the code paths rather than the module.
"""
import pytest

ADMIN_AUTH_NAMES = (
    "verify_password",
    "create_session",
    "verify_session",
    "invalidate_session",
    "ADMIN_USERS",
)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """/admin/login is capped at 5/minute; the cap is not what is under test."""
    import app.main as m
    monkeypatch.setattr(m.limiter, "enabled", False, raising=False)


def test_admin_auth_names_are_bound_in_app_main():
    """The direct guard: every name the admin routes call must exist."""
    import app.main as m
    missing = [n for n in ADMIN_AUTH_NAMES if not hasattr(m, n)]
    assert not missing, (
        f"missing from app.main: {missing} — the admin routes will raise "
        "NameError at call time while the module still imports cleanly"
    )


# ---------------------------------------------------------------------------
# The same thing through the routes, because a name can be bound and still be
# the wrong object.
# ---------------------------------------------------------------------------
async def test_admin_login_rejects_rather_than_crashes(async_client):
    """Unknown user reaches `body.username not in ADMIN_USERS` and returns 401.

    A 500 here means the name did not resolve.
    """
    resp = await async_client.post(
        "/admin/login", json={"username": "nope-not-a-user", "password": "x"}
    )
    assert resp.status_code == 401, f"{resp.status_code} {resp.text[:200]}"


async def test_admin_login_with_a_known_user_reaches_the_password_check(async_client):
    """A real username with a wrong password must pass ADMIN_USERS and land in
    verify_password — proving that name resolves too, still without needing a
    valid credential."""
    import app.main as m

    if not m.ADMIN_USERS:
        pytest.skip("no admin users configured in this environment")

    username = next(iter(m.ADMIN_USERS))
    resp = await async_client.post(
        "/admin/login", json={"username": username, "password": "definitely-wrong"}
    )
    assert resp.status_code == 401, f"{resp.status_code} {resp.text[:200]}"


async def test_admin_session_check_answers_401_not_500(async_client):
    """_get_admin_session calls verify_session even with an empty token, so an
    unauthenticated request is enough to exercise it."""
    resp = await async_client.get("/admin/me")
    assert resp.status_code == 401, f"{resp.status_code} {resp.text[:200]}"


async def test_admin_dashboard_answers_401_not_500(async_client):
    """The route that surfaced the outage."""
    resp = await async_client.get("/admin/dashboard/overview")
    assert resp.status_code == 401, f"{resp.status_code} {resp.text[:200]}"


async def test_admin_logout_answers_rather_than_crashes(async_client):
    """invalidate_session runs unconditionally on this route."""
    resp = await async_client.post("/admin/logout")
    assert resp.status_code < 500, f"{resp.status_code} {resp.text[:200]}"
