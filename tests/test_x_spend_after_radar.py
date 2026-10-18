"""The week before and after the radar switch-off: a total only for a full week."""
import datetime
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "x_spend_after_radar", ROOT / "scripts" / "x_spend_after_radar.py")
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)

ON_THE_DAY = datetime.date(2026, 10, 25)


def fake_spend(usd_by_day):
    def _spend(stamp, ledger=None):
        if stamp in usd_by_day:
            return {"day": stamp, "usd": usd_by_day[stamp], "posts": 10,
                    "users": 2, "writes": 1, "by_source": {}, "ledger": True}
        return {"day": stamp, "usd": 0.0, "ledger": False}
    return _spend


def days(start, n, usd):
    d = datetime.date.fromisoformat(start)
    return {(d + datetime.timedelta(days=i)).isoformat(): usd for i in range(n)}


def test_two_full_weeks_give_both_totals_and_the_difference(monkeypatch, capsys):
    ledger = {**days("2026-10-11", 7, 1.90), **days("2026-10-18", 7, 0.50)}
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend(ledger))
    assert rep.report(today=ON_THE_DAY) == 0
    out = capsys.readouterr().out
    assert "2026-10-11 bis 2026-10-17: $13.30" in out
    assert "2026-10-18 bis 2026-10-24: $3.50" in out
    assert "Differenz: $-9.80 je Woche" in out


def test_a_missing_day_gives_no_total_for_its_week(monkeypatch, capsys):
    ledger = {**days("2026-10-11", 7, 1.90), **days("2026-10-18", 6, 0.50)}
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend(ledger))
    assert rep.report(today=ON_THE_DAY) == 2
    out = capsys.readouterr().out
    assert "keine Summe, Ledger fehlt für 2026-10-24" in out
    assert "· 2026-10-24: kein Ledger" in out
    assert "Differenz: nicht berechnet" in out


def test_it_refuses_before_the_second_week_is_over(monkeypatch, capsys):
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend({}))
    assert rep.report(today=datetime.date(2026, 10, 24)) == 1
    assert "not over yet" in capsys.readouterr().out


def test_send_goes_through_notify_to_stats(monkeypatch):
    ledger = {**days("2026-10-11", 7, 1.0), **days("2026-10-18", 7, 1.0)}
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend(ledger))
    sent = []
    monkeypatch.setattr(rep.notify, "send_telegram",
                        lambda text, **k: sent.append(k) or True)
    rep.report(send=True, today=ON_THE_DAY)
    assert sent == [{"channel": rep.notify.STATS, "parse_mode": "HTML"}]


def test_nothing_is_sent_without_the_flag(monkeypatch):
    ledger = {**days("2026-10-11", 7, 1.0), **days("2026-10-18", 7, 1.0)}
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend(ledger))
    monkeypatch.setattr(rep.notify, "send_telegram",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    assert rep.report(today=ON_THE_DAY) == 0
