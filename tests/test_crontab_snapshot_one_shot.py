"""A declared one-shot line whose time has passed may be gone from the live crontab (ops CJ7)."""
import datetime
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "check_crontab_snapshot", ROOT / "scripts" / "check_crontab_snapshot.py")
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 25, 7, 1, tzinfo=UTC)
ONE_SHOT = "0 7 25 10 * python scripts/x_spend_after_radar.py --send"


def test_a_one_shot_after_its_time_is_expired():
    assert snap.expired_one_shot(ONE_SHOT, NOW)


def test_a_one_shot_before_its_time_is_not():
    assert not snap.expired_one_shot(ONE_SHOT, NOW.replace(hour=6, minute=59))


def test_recurring_lines_never_expire():
    for job in ("40 6 * * 0 python x.py", "*/5 * * * * python y.py",
                "0 8 1 * * python monthly.py", "0 7 25 10 1 python weekday.py"):
        assert not snap.expired_one_shot(job, NOW)


class Run:
    def __init__(self, live):
        self.live = live

    def __call__(self, *a, **k):
        class R:
            returncode = 0
        r = R()
        r.stdout = self.live
        return r


def run_main(monkeypatch, tmp_path, declared, live, now):
    f = tmp_path / "crontab.txt"
    f.write_text(declared)
    monkeypatch.setattr(snap, "SNAPSHOT", f)
    monkeypatch.setattr(snap.subprocess, "run", Run(live))

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(snap.datetime, "datetime", Clock)
    return snap.main()


def test_an_expired_one_shot_missing_live_is_not_a_finding(monkeypatch, tmp_path):
    declared = "40 6 * * 0 python x.py\n" + ONE_SHOT + "\n"
    assert run_main(monkeypatch, tmp_path, declared, "40 6 * * 0 python x.py\n", NOW) == 0


def test_a_pending_one_shot_missing_live_is_still_a_finding(monkeypatch, tmp_path):
    declared = "40 6 * * 0 python x.py\n" + ONE_SHOT + "\n"
    early = NOW.replace(day=24)
    assert run_main(monkeypatch, tmp_path, declared, "40 6 * * 0 python x.py\n", early) == 1


def test_a_recurring_line_missing_live_is_still_a_finding(monkeypatch, tmp_path):
    declared = "40 6 * * 0 python x.py\n0 11 * * * python board.py\n"
    assert run_main(monkeypatch, tmp_path, declared, "40 6 * * 0 python x.py\n", NOW) == 1
