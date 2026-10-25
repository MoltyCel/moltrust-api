"""GET /geo/lang (ops UC-VID): German for DE, AT, CH, LI; English otherwise and when unknown.

Named to sort after tests/test_mp_*: a TestClient module that runs before the
async tests can leave them with a closed event loop (seen on PR #938).
"""
import datetime as dt
import gzip
import importlib.util
import io
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import geo_lang

ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeReader:
    def __init__(self, table):
        self.table = table

    def get(self, ip):
        code = self.table.get(ip)
        return {"country": {"iso_code": code}} if code else None


TABLE = {"80.156.86.1": "DE", "91.138.0.1": "CH", "193.170.0.1": "AT", "82.117.0.1": "LI",
         "8.8.8.8": "US", "2.16.0.1": "FR"}


@pytest.fixture()
def reader(monkeypatch):
    monkeypatch.setattr(geo_lang, "_open", lambda: FakeReader(TABLE))


@pytest.mark.parametrize("ip,country,lang", [
    ("80.156.86.1", "DE", "de"), ("91.138.0.1", "CH", "de"), ("193.170.0.1", "AT", "de"),
    ("82.117.0.1", "LI", "de"), ("8.8.8.8", "US", "en"), ("2.16.0.1", "FR", "en"),
    ("10.0.0.1", None, "en"), ("not-an-ip", None, "en"), (None, None, "en"),
])
def test_country_and_language(reader, ip, country, lang):
    assert geo_lang.country_for(ip) == country
    assert geo_lang.lang_for(geo_lang.country_for(ip)) == lang


def test_without_a_database_everyone_gets_english(monkeypatch, tmp_path):
    monkeypatch.setattr(geo_lang, "DB_PATH", tmp_path / "missing.mmdb")
    monkeypatch.setattr(geo_lang, "_reader", None)
    assert geo_lang.country_for("80.156.86.1") is None


def test_a_broken_database_is_english_not_an_error(monkeypatch, tmp_path):
    bad = tmp_path / "bad.mmdb"
    bad.write_bytes(b"not a database")
    monkeypatch.setattr(geo_lang, "DB_PATH", bad)
    monkeypatch.setattr(geo_lang, "_reader", None)
    assert geo_lang.country_for("80.156.86.1") is None


# --- the route ------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(m, "db_pool", None)
    monkeypatch.setattr(m.limiter, "enabled", False)
    return TestClient(m.app)


def test_route_answers_from_the_client_ip(client, reader, monkeypatch):
    monkeypatch.setattr(m, "_get_client_ip", lambda request: "91.138.0.1")
    r = client.get("/geo/lang")
    assert r.status_code == 200
    assert r.json() == {"lang": "de", "country": "CH"}
    assert r.headers["cache-control"] == "no-store"


def test_route_without_database_answers_english(client, monkeypatch, tmp_path):
    monkeypatch.setattr(geo_lang, "DB_PATH", tmp_path / "missing.mmdb")
    monkeypatch.setattr(geo_lang, "_reader", None)
    r = client.get("/geo/lang")
    assert r.status_code == 200 and r.json() == {"lang": "en", "country": None}


@pytest.mark.parametrize("origin", ["https://moltrust.ch", "https://www.moltrust.ch"])
def test_the_site_may_call_it(client, reader, origin):
    r = client.get("/geo/lang", headers={"Origin": origin})
    assert r.headers.get("access-control-allow-origin") == origin


def test_route_needs_no_key_and_costs_nothing():
    from app.credits import get_endpoint_cost
    assert get_endpoint_cost("GET", "/geo/lang") == 0


def test_route_is_not_logged():
    assert "/geo/lang" in m.SKIP_LOG_PATHS


# --- the monthly refresh ----------------------------------------------------------

spec = importlib.util.spec_from_file_location("geo_db_refresh", ROOT / "scripts" / "geo_db_refresh.py")
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)

NOW = dt.datetime(2026, 11, 3, 4, 40, tzinfo=dt.timezone.utc)


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "TARGET", tmp_path / "geo" / "db.mmdb")
    monkeypatch.setattr(refresh, "HEARTBEAT", tmp_path / "hb.json")
    return tmp_path


def test_months_tries_current_then_previous():
    assert refresh.months(NOW) == ["2026-11", "2026-10"]
    assert refresh.months(dt.datetime(2027, 1, 2, tzinfo=dt.timezone.utc)) == ["2027-01", "2026-12"]


def test_previous_month_when_the_current_is_not_out(paths, monkeypatch):
    seen = []

    def fetch(month, dest):
        seen.append(month)
        if month == "2026-11":
            raise OSError("404")
        dest.write_bytes(b"db " + month.encode())
    monkeypatch.setattr(refresh, "check", lambda p: p.stat().st_size)
    assert refresh.main(now=NOW, fetch=fetch) == 0
    assert seen == ["2026-11", "2026-10"]
    assert (paths / "geo" / "db.mmdb").read_bytes() == b"db 2026-10"
    assert json.loads((paths / "hb.json").read_text())["status"] == "ok"


def test_a_file_that_fails_the_check_never_replaces_the_good_one(paths, monkeypatch):
    target = paths / "geo" / "db.mmdb"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"good")

    def fetch(month, dest):
        dest.write_bytes(b"garbage")

    def check(p):
        raise ValueError("8.8.8.8 resolves to None, expected US")
    monkeypatch.setattr(refresh, "check", check)
    assert refresh.main(now=NOW, fetch=fetch) == 1
    assert target.read_bytes() == b"good"
    hb = json.loads((paths / "hb.json").read_text())
    assert hb["status"] == "error" and "kept the previous file" in hb["detail"]


def test_download_unpacks_gzip(tmp_path, monkeypatch):
    payload = gzip.compress(b"mmdb bytes")

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(refresh.urllib.request, "urlopen", lambda req, timeout: Resp(payload))
    dest = tmp_path / "out.mmdb"
    refresh.download("2026-10", dest)
    assert dest.read_bytes() == b"mmdb bytes"
