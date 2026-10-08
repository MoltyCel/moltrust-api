"""Second factor for /admin/login (app/admin_totp.py, 2026-10-08)."""
import asyncio

import pytest

from app import admin_totp as at
from app import reseller_admin as ra

SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
NOW = 1_760_000_000.0


def test_matching_step_finds_the_current_and_neighbour_steps():
    for w in (-1, 0, 1):
        code = ra.totp_at(SECRET, NOW + w * 30)
        assert at.matching_step(SECRET, code, now=NOW) == int((NOW + w * 30) // 30)


def test_matching_step_refuses_old_wrong_and_malformed_codes():
    assert at.matching_step(SECRET, ra.totp_at(SECRET, NOW - 120), now=NOW) is None
    assert at.matching_step(SECRET, "000000", now=NOW) in (None, int(NOW // 30) - 1,
                                                           int(NOW // 30), int(NOW // 30) + 1)
    for bad in ("", None, "12345a", "1234567", " "):
        assert at.matching_step(SECRET, bad, now=NOW) is None


class _Conn:
    def __init__(self):
        self.used = set()
    async def execute(self, *a):
        return None
    async def fetchval(self, sql, user, step):
        if (user, step) in self.used:
            return None
        self.used.add((user, step))
        return step


class _Pool:
    def __init__(self, conn):
        self.conn = conn
    def acquire(self):
        conn = self.conn
        class _Ctx:
            async def __aenter__(self_inner):
                return conn
            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


@pytest.fixture
def wired(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(ra, "_totp_key", lambda: "k")
    monkeypatch.setattr(ra, "_pool", lambda: _Pool(conn))

    async def read(c, user, key):
        return (SECRET, True) if user == "lars" else (None, False)
    monkeypatch.setattr(ra, "_read_secret", read)
    return conn


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_a_valid_code_is_accepted_once_and_refused_the_second_time(wired):
    code = ra.totp_at(SECRET, __import__("time").time())
    assert _run(at.check("lars", code)) == (True, "ok")
    assert _run(at.check("Lars", code)) == (False, "replayed")


def test_reasons_for_each_refusal(wired, monkeypatch):
    assert _run(at.check("lars", None)) == (False, "missing")
    assert _run(at.check("lars", "abc")) == (False, "invalid")
    assert _run(at.check("bernd", "123456")) == (False, "not-enrolled")
    monkeypatch.setattr(ra, "_totp_key", lambda: None)
    assert _run(at.check("lars", "123456")) == (False, "no-key")


def test_a_database_error_is_a_refusal_not_an_exception(monkeypatch):
    monkeypatch.setattr(ra, "_totp_key", lambda: "k")
    monkeypatch.setattr(ra, "_pool", lambda: None)
    ok, why = _run(at.check("lars", "123456"))
    assert ok is False and why.startswith("error-")


def test_the_switch_defaults_to_off(monkeypatch):
    monkeypatch.delenv("ADMIN_TOTP_REQUIRED", raising=False)
    assert at.required() is False
    monkeypatch.setenv("ADMIN_TOTP_REQUIRED", "1")
    assert at.required() is True
