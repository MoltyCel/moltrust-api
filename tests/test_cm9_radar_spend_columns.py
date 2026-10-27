"""CM9: the radar's reading, its sending and the CM5 measurement, apart.

Herkunft: ops CM9 (Auftrag Lars 22.10.2026). Eine volle Woche (Mo-So UTC)
soll je Tag drei Summen in drei Spalten zeigen; danach setzt Lars einen
Deckel fuer das Lesen. Vorher steht keine Zahl im Code (Kriterium 3).

Bis hierher trugen die Lesezeilen des Radars nur das URL-Ende als Quelle
("tweets", "recent", "mentions", eine Post-ID), und der Bericht fasste alles
eines Tages zusammen. Jede Pruefung hat ihre Gegenprobe.
"""
import datetime
import json
import re
from pathlib import Path

import pytest

from agents import reply_radar as rr
from agents import x_meter

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    path = tmp_path / "x_meter.jsonl"
    monkeypatch.setattr(x_meter, "LEDGER", str(path))
    return path


def rows(path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body
        self.text = ""

    def json(self):
        return self._body


# ── Die Kennzeichnung im Zaehler ──

def test_every_radar_read_carries_origin_radar(ledger, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(
        {"data": [{"id": "p1"}, {"id": "p2"}], "includes": {"users": [{"id": "u1"}]}}))
    monkeypatch.setattr(x_meter, "reads_paused", lambda *a, **k: None)
    rr._get("auth", f"https://api.twitter.com/2/lists/{rr.TARGETS_LIST_ID}/tweets", {})
    rr._get("auth", "https://api.twitter.com/2/users/1/mentions", {})
    [a, b] = rows(ledger)
    assert a["origin"] == b["origin"] == "radar"
    # The source stays what it was: check_radar_reading_list.py keys on it.
    assert a["source"] == "tweets" and b["source"] == "mentions"


def test_the_follower_read_is_the_radars_too(ledger, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(
        {"data": {"id": "u0", "public_metrics": {"followers_count": 27}}}))
    assert rr.followers_now("auth") == 27
    [row] = rows(ledger)
    assert row["origin"] == "radar" and row["source"] == "followers"


def test_other_readers_carry_no_origin(ledger, monkeypatch):
    """Gegenprobe: Digest, Herald und Proben bleiben ungekennzeichnet."""
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({"data": [{"id": "p9"}]}))
    x_meter.get("https://api.twitter.com/2/tweets", source="tweets-batch")
    [row] = rows(ledger)
    assert "origin" not in row


def test_a_radar_reply_write_is_marked_and_a_digest_is_not(ledger):
    x_meter.record_write("1", "a reply", source="reply")
    x_meter.record_write("2", "the digest", source="digest")
    a, b = rows(ledger)
    assert a["origin"] == "radar"
    assert "origin" not in b


def test_the_measurement_source_is_the_one_cm5_books_under():
    from scripts import lars_reply_metrics
    assert x_meter.CM5_MEASUREMENT_SOURCE == lars_reply_metrics.LEDGER_SOURCE


# ── Drei Spalten je Tag ──

def _write(path, *items):
    with open(path, "a") as f:
        for r in items:
            f.write(json.dumps(r) + "\n")


DAY = "2026-11-02"


def test_one_day_splits_into_three_columns(ledger):
    at = f"{DAY}T06:05:02+00:00"
    _write(ledger,
           # the radar: 3 distinct posts (p1 read twice) and one profile
           {"at": at, "kind": "read", "source": "tweets", "origin": "radar",
            "posts": ["p1", "p2"], "users": []},
           {"at": at, "kind": "read", "source": "mentions", "origin": "radar",
            "posts": ["p1", "p3"], "users": ["u1"]},
           # the CM5 measurement: two posts
           {"at": at, "kind": "read", "source": "lars-reply-metrics",
            "posts": ["r1", "r2"], "users": []},
           # out of scope: digest reads and a digest write
           {"at": at, "kind": "read", "source": "tweets-batch",
            "posts": ["d1", "d2", "d3"], "users": []},
           {"at": at, "kind": "write", "source": "digest", "id": "9",
            "with_url": True},
           # two radar replies, one with a link
           {"at": at, "kind": "write", "source": "reply", "origin": "radar",
            "id": "7", "with_url": False},
           {"at": at, "kind": "write", "source": "reply", "origin": "radar",
            "id": "8", "with_url": True},
           # another day: not this one
           {"at": "2026-11-03T06:05:02+00:00", "kind": "read", "source": "tweets",
            "origin": "radar", "posts": ["p7"], "users": []})

    c = x_meter.radar_split(DAY)
    assert c["radar_read"] == pytest.approx(3 * 0.005 + 1 * 0.010)
    assert c["radar_send"] == pytest.approx(0.015 + 0.200)
    assert c["radar_sends"] == 2
    assert c["cm5_read"] == pytest.approx(2 * 0.005)
    assert c["ledger"] is True


def test_reads_never_land_in_the_send_column(ledger):
    """Gegenprobe zu CM4: Lesen ueber 0,50 $ macht die Sendespalte nicht groesser."""
    _write(ledger, {"at": f"{DAY}T06:05:02+00:00", "kind": "read",
                    "source": "tweets", "origin": "radar",
                    "posts": [f"p{i}" for i in range(200)], "users": []})
    c = x_meter.radar_split(DAY)
    assert c["radar_read"] == pytest.approx(1.0)
    assert c["radar_send"] == 0.0


# ── Die Woche ──

def _report():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "x_spend_report", ROOT / "scripts" / "x_spend_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_week_table_runs_monday_to_sunday_in_three_columns(ledger):
    for i, n in enumerate([40, 39, 39, 40, 38, 39]):          # Mon..Sat
        day = (datetime.date(2026, 11, 2) + datetime.timedelta(days=i)).isoformat()
        _write(ledger, {"at": f"{day}T06:05:02+00:00", "kind": "read",
                        "source": "tweets", "origin": "radar",
                        "posts": [f"{day}-{k}" for k in range(n)], "users": []})
    out = _report().week_table("2026-11-02")
    text = "\n".join(out)
    assert "Radar lesen" in out[2] and "Radar senden" in out[2] and "CM5-Messung" in out[2]
    days = [ln for ln in out if re.match(r"\d{4}-\d{2}-\d{2}", ln)]
    assert [d[:10] for d in days] == [f"2026-11-0{d}" for d in range(2, 9)]
    assert days[0].split() == ["2026-11-02", "$0.200", "$0.000", "$0.000"]
    assert "Summe" in text and "$1.175" in text
    # Sunday has no row at all: said, not shown as a cheap day.
    assert "Ohne Ledger-Zeile (nicht gemessen): 2026-11-08" in text


def test_the_week_must_start_on_a_monday(ledger):
    with pytest.raises(ValueError):
        _report().week_table("2026-11-03")


def test_the_daily_report_carries_the_three_columns(ledger, monkeypatch, capsys):
    rep = _report()
    yday = (datetime.datetime.now(datetime.timezone.utc).date()
            - datetime.timedelta(days=1)).isoformat()
    _write(ledger, {"at": f"{yday}T06:05:02+00:00", "kind": "read",
                    "source": "tweets", "origin": "radar",
                    "posts": ["a", "b"], "users": []})
    monkeypatch.setattr(rep, "EXCLUDED_DAYS", {})
    rep.report(days=1, send=False)
    out = capsys.readouterr().out
    assert "Radar lesen $0.010 · Radar senden $0.000 · CM5-Messung $0.000" in out


# ── Kriterium 3: kein Lese-Deckel, bevor Lars die Zahl setzt ──

def test_no_read_cap_for_the_radar_in_the_code():
    for rel in ("agents/x_meter.py", "agents/reply_radar.py",
                "scripts/x_spend_report.py"):
        src = (ROOT / rel).read_text()
        assert not re.search(r"^\s*RADAR_READ\w*CAP\w*\s*=|^\s*\w*READ_CAP\w*\s*=",
                             src, re.M), rel
