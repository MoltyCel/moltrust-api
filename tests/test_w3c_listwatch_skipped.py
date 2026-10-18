"""ListWatch: a month index that was not fetched is reported, never "nothing new".

Until 18.10.2026 a 429 or 5xx on a month index was logged as "skipping this
month" and dropped. A run in which every fetch failed ended with "nothing new",
so an archive outage looked like a quiet list (ops L7, reproduced live with 429
from mailarchive.ietf.org). The report now names list, month and status.
"""
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("requests")

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))


@pytest.fixture
def lw(tmp_path, monkeypatch):
    # The module parses argv and opens its log under ~/moltstack at import.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["w3c_listwatch", "--dry-run"])
    sys.modules.pop("w3c_listwatch", None)
    mod = importlib.import_module("w3c_listwatch")
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(mod, "LISTS", [("public-agentprotocol", "w3c"), ("audit", "ietf")])
    monkeypatch.setattr(mod, "load_secrets_into_env", lambda: None)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    yield mod
    sys.modules.pop("w3c_listwatch", None)


def _fetch(ietf_status):
    def fetch(url):
        if "mailarchive.ietf.org" in url:
            return ietf_status, ""
        return 200, ""          # a W3C month that exists and has no messages
    return fetch


def test_all_ietf_fetches_429_is_reported_not_nothing_new(lw, monkeypatch, capsys, caplog):
    monkeypatch.setattr(lw, "fetch", _fetch(429))
    assert lw.main() == 0
    out = capsys.readouterr().out
    assert "nothing new" not in caplog.text
    assert "─── REPORT ───" in out
    assert "2 month index(es) not fetched" in out
    for mon in lw.month_keys("ietf"):
        assert f"[audit] {mon}: HTTP 429" in out


def test_network_error_is_named_as_such(lw, monkeypatch, capsys):
    monkeypatch.setattr(lw, "fetch", _fetch(0))
    lw.main()
    assert "network error" in capsys.readouterr().out


def test_report_is_sent_when_only_a_skip_happened(lw, monkeypatch):
    monkeypatch.setattr(lw, "fetch", _fetch(503))
    monkeypatch.setattr(lw.ARGS, "dry_run", False)
    sent = []
    monkeypatch.setattr(lw.notify, "send_telegram",
                        lambda text, **kw: sent.append(text) or True)
    assert lw.main() == 0
    assert len(sent) == 1 and "HTTP 503" in sent[0]


def test_quiet_lists_still_say_nothing_new(lw, monkeypatch, capsys, caplog):
    # Schweigeprobe: every index answers, none has a message -> no report.
    monkeypatch.setattr(lw, "fetch", _fetch(200))
    assert lw.main() == 0
    assert "nothing new" in caplog.text
    assert "REPORT" not in capsys.readouterr().out


def test_404_month_is_not_a_skip(lw, monkeypatch, capsys, caplog):
    # A month directory that does not exist yet is normal, not "not fetched".
    monkeypatch.setattr(lw, "fetch", _fetch(404))
    lw.main()
    assert "nothing new" in caplog.text
    assert "not fetched" not in capsys.readouterr().out


# --- the log guard behind docs/invariants/c-listwatch-no-silent-skip.yaml -----------

import listwatch_skip_check as G

NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)

OLD_SILENT_RUN = [
    "[2026-10-18T11:00:01Z] INFO: public-agentprotocol 2026Oct: 8 message(s), 0 new\n",
    "[2026-10-18T11:00:02Z] WARNING: audit 2026-10: HTTP 429, skipping this month\n",
    "[2026-10-18T11:00:03Z] INFO: nothing new\n",
]
FIXED_RUN = [
    "[2026-10-18T11:00:02Z] WARNING: audit 2026-10: HTTP 429, skipping this month\n",
    "[2026-10-18T11:00:04Z] INFO: report sent (0 new, 1 not fetched)\n",
]
QUIET_RUN = [
    "[2026-10-18T11:00:01Z] INFO: audit 2026-10: 100 message(s), 0 new\n",
    "[2026-10-18T11:00:02Z] INFO: nothing new\n",
]


def test_guard_fires_on_a_skip_that_ended_in_nothing_new():
    assert G.silent_skips(OLD_SILENT_RUN, NOW, 48) == (1, 1)


def test_guard_is_silent_on_a_reported_skip_and_on_a_quiet_run():
    assert G.silent_skips(FIXED_RUN + QUIET_RUN, NOW, 48) == (0, 2)


def test_skip_does_not_leak_into_the_next_run():
    assert G.silent_skips(FIXED_RUN + QUIET_RUN, NOW, 48)[0] == 0


def test_guard_without_runs_exits_2(tmp_path):
    empty = tmp_path / "empty.log"
    empty.write_text("")
    assert G.main([str(empty)]) == 2


def test_guard_without_runs_does_not_print_the_expected_value(tmp_path, capsys):
    """An empty window is not a pass, and it must not look like one.

    scripts/selftest.py run_query returns stdout and drops the exit code as
    long as stdout is non-empty, and judge() then only asks whether the output
    contains "listwatch-silent-skip=0". Measured on 18.10.2026 against the
    first version of this guard: an empty log gave that exact line with exit 2
    and selftest judged it green, so a ListWatch that had stopped running would
    have been reported as healthy. The word UNREADABLE is what selftest's
    unreadable() looks for, and it books the finding at FAIL.
    """
    empty = tmp_path / "empty.log"
    empty.write_text("")
    G.main([str(empty)])
    out = capsys.readouterr().out
    assert "listwatch-silent-skip=0" not in out
    assert out.strip().startswith("UNREADABLE")


def test_guard_with_runs_still_prints_the_expected_value(tmp_path, capsys):
    # Schweigeprobe to the test above: a measured window keeps the value the
    # invariant compares against, so the fix does not mute the guard.
    #
    # Written with timestamps relative to now, because main() reads the real
    # clock and a fixed date drops out of the 48 h window. The two runs are
    # held hours apart the way the cron puts them (11:00 and 23:00 UTC): main()
    # sorts all lines of all log files by timestamp to merge rotated files, so
    # two runs sharing a second would interleave and the skip of one would be
    # counted against the closing line of the other.
    now = datetime.now(timezone.utc)
    stamp = lambda mins: (now - timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = tmp_path / "runs.log"
    log.write_text(
        f"[{stamp(600)}] WARNING: audit 2026-10: HTTP 429, skipping this month\n"
        f"[{stamp(599)}] INFO: report sent (0 new, 1 not fetched)\n"
        f"[{stamp(60)}] INFO: audit 2026-10: 100 message(s), 0 new\n"
        f"[{stamp(59)}] INFO: nothing new\n"
    )
    assert G.main(["--hours", "48", str(log)]) == 0
    assert "listwatch-silent-skip=0 runs=2" in capsys.readouterr().out


ABORTED_THEN_QUIET = [
    "[2026-10-18T11:00:00Z] INFO: W3C LISTWATCH — 7 list(s)\n",
    "[2026-10-18T11:00:02Z] WARNING: audit 2026-10: HTTP 429, skipping this month\n",
    # the run dies here, no closing line
    "[2026-10-18T23:00:00Z] INFO: W3C LISTWATCH — 7 list(s)\n",
    "[2026-10-18T23:00:01Z] INFO: audit 2026-10: 100 message(s), 0 new\n",
    "[2026-10-18T23:00:02Z] INFO: nothing new\n",
]

LATER = datetime(2026, 10, 19, 0, 0, 0, tzinfo=timezone.utc)


def test_a_skip_from_an_aborted_run_is_not_charged_to_the_next_run():
    """A run that dies after a skip must not make the next quiet run look bad.

    Measured on 18.10.2026 without the run marker: the dangling skip was
    counted against the closing line of the following run, so a quiet list
    reported a silent skip it never had. The marker bounds the run.
    """
    assert G.silent_skips(ABORTED_THEN_QUIET, LATER, 48) == (0, 1)


def test_a_real_silent_skip_is_still_caught_with_the_marker():
    # Schweigeprobe to the test above: the marker must not swallow the finding
    # it was added next to. Same run, skip and "nothing new" together.
    lines = [
        "[2026-10-18T23:00:00Z] INFO: W3C LISTWATCH — 7 list(s)\n",
        "[2026-10-18T23:00:02Z] WARNING: audit 2026-10: HTTP 429, skipping this month\n",
        "[2026-10-18T23:00:03Z] INFO: nothing new\n",
    ]
    assert G.silent_skips(lines, LATER, 48) == (1, 1)
