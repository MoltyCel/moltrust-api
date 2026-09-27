"""The three-day rate: which days count, and which must not."""
import datetime
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "x_spend_report", ROOT / "scripts" / "x_spend_report.py")
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)


def day(n):
    return (datetime.datetime.now(datetime.timezone.utc).date()
            - datetime.timedelta(days=n)).isoformat()


def fake_spend(usd_by_day):
    def _spend(stamp, ledger=None):
        if stamp in usd_by_day:
            return {"day": stamp, "usd": usd_by_day[stamp], "posts": 10,
                    "users": 2, "writes": 1, "by_source": {"list": 10},
                    "ledger": True}
        return {"day": stamp, "usd": 0.0, "ledger": False}
    return _spend


def test_the_outage_and_catchup_days_are_left_out(monkeypatch, capsys):
    monkeypatch.setattr(rep, "EXCLUDED_DAYS", {day(1): "Nachholung"})
    monkeypatch.setattr(rep.x_meter, "spend",
                        fake_spend({day(2): 0.70, day(3): 0.90, day(4): 0.80,
                                    day(1): 99.0}))
    rep.report(days=3, send=False)
    out = capsys.readouterr().out
    assert "$0.80/Tag" in out, out
    assert "99" not in out, "an excluded day reached the average"


def test_a_rate_over_the_target_says_so(monkeypatch, capsys):
    monkeypatch.setattr(rep, "EXCLUDED_DAYS", {})
    monkeypatch.setattr(rep.x_meter, "spend",
                        fake_spend({day(1): 1.20, day(2): 1.20, day(3): 1.20}))
    rep.report(days=3, send=False)
    out = capsys.readouterr().out
    assert "über dem Soll" in out
    assert "$36.00/Monat" in out


def test_nothing_measured_is_not_a_zero(monkeypatch, capsys):
    monkeypatch.setattr(rep, "EXCLUDED_DAYS", {})
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend({}))
    assert rep.report(days=3, send=False) == 1
    assert "no regular days" in capsys.readouterr().out
