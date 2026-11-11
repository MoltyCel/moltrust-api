"""The week before and after the radar switch-off: a total only for a full week."""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "x_spend_after_radar", ROOT / "scripts" / "x_spend_after_radar.py")
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)

ON_THE_DAY = datetime.date(2026, 10, 25)


@pytest.fixture(autouse=True)
def change_log_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(rep.crontab_change, "BACKUPS", str(tmp_path / "crontab-backups"))
    return tmp_path / "crontab-backups"


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


def test_send_files_the_report_for_the_daily_report(monkeypatch):
    """ops NOTIFY-3c: a measurement goes into the daily report, not to Telegram."""
    ledger = {**days("2026-10-11", 7, 1.0), **days("2026-10-18", 7, 1.0)}
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend(ledger))
    monkeypatch.setattr(rep, "remove_own_cron_lines", lambda: None)
    sent = []
    monkeypatch.setattr(rep.tagesbericht_eingang, "ablegen",
                        lambda quelle, text, **k: sent.append((quelle, k)) or True)
    assert rep.report(send=True, today=ON_THE_DAY) == 0
    assert sent == [("x_spend_after_radar", {"html": True})]


# ── CJ7: the job removes its own lines, in the run that sent ──

CRONTAB = """MAILTO=""
*/5 * * * * other job
# X-Verbrauch je Woche vor und nach der Radar-Abschaltung — einmalig, 25.10.2026 07:00 UTC (Lars, CJ5).
# x_spend_after_radar.py traegt diese drei Zeilen nach dem Versand selbst aus (CJ7).
0 7 25 10 * cd /srv && python scripts/x_spend_after_radar.py --send >> log 2>&1
40 6 * * 0 cd /srv && python scripts/x_spend_report.py --days 7 --send
"""


class FakeCrontab:
    def __init__(self, text, list_rc=0, put_rc=0):
        self.text, self.list_rc, self.put_rc, self.written = text, list_rc, put_rc, None

    def __call__(self, cmd, input=None, **k):
        class R:
            pass
        r = R()
        if cmd == ["crontab", "-l"]:
            r.returncode, r.stdout = self.list_rc, self.text
        else:
            self.written = input
            r.returncode, r.stdout = self.put_rc, ""
            if self.put_rc == 0:
                self.text = input
        return r


def test_the_three_own_lines_go_and_every_other_line_stays():
    fake = FakeCrontab(CRONTAB)
    assert rep.remove_own_cron_lines(run=fake) is None
    assert fake.written == ('MAILTO=""\n*/5 * * * * other job\n'
                            "40 6 * * 0 cd /srv && python scripts/x_spend_report.py --days 7 --send\n")


def test_the_removal_is_logged_with_a_copy_before_and_after(change_log_in_tmp):
    fake = FakeCrontab(CRONTAB)
    assert rep.remove_own_cron_lines(run=fake) is None
    entry = json.loads((change_log_in_tmp / "changes.jsonl").read_text())
    assert entry["who"] == "x_spend_after_radar.py (CJ7)"
    assert (entry["result"], entry["removed"], entry["added"]) == ("ok", 3, 0)
    assert Path(entry["before"]).read_text() == CRONTAB
    assert Path(entry["after"]).read_text() == fake.written


def test_a_failed_write_is_named_and_logged(change_log_in_tmp):
    fake = FakeCrontab(CRONTAB, put_rc=1)
    assert rep.remove_own_cron_lines(run=fake) == "crontab - exit 1"
    entry = json.loads((change_log_in_tmp / "changes.jsonl").read_text())
    assert entry["result"] == "crontab - exit 1" and entry["removed"] == 0


def test_an_unreadable_crontab_writes_nothing():
    fake = FakeCrontab(CRONTAB, list_rc=1)
    assert rep.remove_own_cron_lines(run=fake) == "crontab -l exit 1"
    assert fake.written is None


def test_a_crontab_without_the_lines_writes_nothing():
    fake = FakeCrontab('MAILTO=""\n*/5 * * * * other job\n')
    assert "0 own lines found" in rep.remove_own_cron_lines(run=fake)
    assert fake.written is None


def test_a_failed_delivery_keeps_the_lines(monkeypatch, capsys):
    ledger = {**days("2026-10-11", 7, 1.0), **days("2026-10-18", 7, 1.0)}
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend(ledger))
    monkeypatch.setattr(rep.tagesbericht_eingang, "ablegen", lambda quelle, text, **k: False)
    monkeypatch.setattr(rep, "remove_own_cron_lines",
                        lambda: (_ for _ in ()).throw(AssertionError("removed")))
    rep.report(send=True, today=ON_THE_DAY)
    assert "the crontab lines stay" in capsys.readouterr().out


def test_a_failed_removal_after_delivery_exits_3(monkeypatch, capsys):
    ledger = {**days("2026-10-11", 7, 1.0), **days("2026-10-18", 7, 1.0)}
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend(ledger))
    monkeypatch.setattr(rep.tagesbericht_eingang, "ablegen", lambda quelle, text, **k: True)
    monkeypatch.setattr(rep, "remove_own_cron_lines", lambda: "crontab - exit 1")
    assert rep.report(send=True, today=ON_THE_DAY) == 3
    assert "NOT removed: crontab - exit 1" in capsys.readouterr().out


def test_nothing_is_sent_without_the_flag(monkeypatch):
    ledger = {**days("2026-10-11", 7, 1.0), **days("2026-10-18", 7, 1.0)}
    monkeypatch.setattr(rep.x_meter, "spend", fake_spend(ledger))
    monkeypatch.setattr(rep.tagesbericht_eingang, "ablegen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    assert rep.report(today=ON_THE_DAY) == 0
