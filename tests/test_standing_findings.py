#!/usr/bin/env python3
"""The digest names a finding that still stands (ops E2, 15.10.2026).

f-no-stale-escrow failed in every daily run from 08.10.2026 on. The 20:00Z
digest of 15.10. named it nowhere: its window (08:00-20:00) holds no daily
run, the finding is in neither register, and the digest had no group for a
finding that is red and not new. Each case below has a trigger probe and a
silence probe.
"""
import datetime as dt
import json
import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import selftest  # noqa: E402
from scripts import selftest_throttle as T  # noqa: E402

UTC = dt.timezone.utc
SLOT = dt.datetime(2026, 10, 15, 20, 0, tzinfo=UTC)
AB = SLOT - dt.timedelta(hours=T.STANDING_HOURS)


def lauf(finished, *ergebnisse, **extra):
    return {"finished": finished.isoformat(timespec="seconds"),
            "ergebnisse": [dict(e) for e in ergebnisse], **extra}


ESCROW_FAIL = {"id": "f-no-stale-escrow", "status": "FAIL",
               "detail": "1 (erwartet eq 0)"}
ESCROW_OK = {"id": "f-no-stale-escrow", "status": "OK"}
DAILY = dt.datetime(2026, 10, 15, 4, 1, 50, tzinfo=UTC)


# 1) The case of 15.10.: a daily FAIL at 04:01, digest at 20:00

def test_trigger_daily_fail_is_named_at_the_evening_digest():
    docs = [lauf(DAILY, ESCROW_FAIL)]
    st = T.standing_findings(docs, AB, SLOT)
    assert st == [("f-no-stale-escrow", "FAIL", DAILY)]
    z = T.digest_line(SLOT, 12, [], [], [], None, stehend=st)
    assert "1 stehend (f-no-stale-escrow FAIL zuletzt 15.10. 04:01Z)" in z


def test_silence_all_green_names_nothing():
    docs = [lauf(DAILY, ESCROW_OK), lauf(SLOT - dt.timedelta(hours=1),
                                         {"id": "c-x", "status": "OK"})]
    st = T.standing_findings(docs, AB, SLOT)
    assert st == []
    z = T.digest_line(SLOT, 12, [], [], [], None, stehend=st)
    assert "stehend" not in z
    # Without the argument the line is the one from before E2.
    assert z == T.digest_line(SLOT, 12, [], [], [], None)


# 2) The latest verdict decides

def test_silence_a_later_green_clears_an_earlier_red():
    docs = [lauf(DAILY - dt.timedelta(hours=20), ESCROW_FAIL),
            lauf(DAILY, ESCROW_OK)]
    assert T.standing_findings(docs, AB, SLOT) == []


def test_trigger_a_later_red_overrides_an_earlier_green():
    docs = [lauf(DAILY - dt.timedelta(hours=20), ESCROW_OK),
            lauf(DAILY, ESCROW_FAIL)]
    assert [s[0] for s in T.standing_findings(docs, AB, SLOT)] == [
        "f-no-stale-escrow"]


def test_warn_is_not_standing():
    docs = [lauf(DAILY, {"id": "c-w", "status": "WARN"})]
    assert T.standing_findings(docs, AB, SLOT) == []


def test_trigger_error_is_standing():
    docs = [lauf(DAILY, {"id": "c-e", "status": "ERROR"})]
    assert [s[:2] for s in T.standing_findings(docs, AB, SLOT)] == [
        ("c-e", "ERROR")]


# 3) Named elsewhere, or expected: not listed twice

def test_silence_skip_holds_what_the_digest_names_elsewhere():
    docs = [lauf(DAILY, ESCROW_FAIL)]
    assert T.standing_findings(docs, AB, SLOT,
                               skip={"f-no-stale-escrow"}) == []


def test_silence_declared_known_state_is_expected():
    r = dict(ESCROW_FAIL, bekannt_bis="2026-10-20", ueberfaellig=False)
    assert T.standing_findings([lauf(DAILY, r)], AB, SLOT) == []


def test_trigger_overdue_known_state_stands():
    r = dict(ESCROW_FAIL, bekannt_bis="2026-10-14", ueberfaellig=True)
    assert len(T.standing_findings([lauf(DAILY, r)], AB, SLOT)) == 1


# 4) What does not count as a run

def test_silence_runs_outside_the_lookback_do_not_count():
    alt = AB - dt.timedelta(minutes=1)
    assert T.standing_findings([lauf(alt, ESCROW_FAIL)], AB, SLOT) == []
    assert T.standing_findings([lauf(SLOT, ESCROW_FAIL)], AB, SLOT) == []


def test_silence_dry_run_does_not_count():
    docs = [lauf(DAILY, ESCROW_FAIL, dry_run=True)]
    assert T.standing_findings(docs, AB, SLOT) == []


def test_unreadable_finished_is_skipped():
    docs = [{"finished": "irgendwann", "ergebnisse": [ESCROW_FAIL]},
            {"ergebnisse": [ESCROW_FAIL]}]
    assert T.standing_findings(docs, AB, SLOT) == []


def test_lookback_covers_a_daily_run_at_both_slots():
    """04:00 UTC daily against 08:00 and 20:00 digests: 4 h and 16 h back."""
    assert T.STANDING_HOURS > 16


# 5) End to end through send_digest: report on disk, one message out

def _digest(tmp_path, monkeypatch, docs_by_day):
    out = tmp_path / "selftest"
    out.mkdir()
    for day, docs in docs_by_day.items():
        (out / f"{day}.json").write_text(json.dumps(docs))
    monkeypatch.setattr(selftest, "OUTDIR", str(out))
    monkeypatch.setattr(T, "state", lambda *a, **k: {})
    monkeypatch.setattr(T, "save_state", lambda *a, **k: None)
    monkeypatch.setattr(T, "load_register", lambda *a, **k: [])
    monkeypatch.setattr(T, "load_open", lambda *a, **k: [])
    monkeypatch.setattr(T, "deploy_lines", lambda *a, **k: [])
    monkeypatch.delenv("HEALTHCHECK_SELFTEST_DIGEST_URL", raising=False)
    import scripts
    for name, attr in (("webroot_drift", "lines"), ("telegram_repeats", "lines"),
                       ("module_effects", "digest_lines"),
                       ("checkout_writes", "lines"), ("nginx_state", "lines"),
                       ("ref_guard", "lines"), ("claude_procs", "lines"),
                       ("check_radar_reading_list", "lines")):
        fake = types.ModuleType(f"scripts.{name}")
        setattr(fake, attr, lambda *a, **k: [])
        monkeypatch.setitem(sys.modules, f"scripts.{name}", fake)
        monkeypatch.setattr(scripts, name, fake, raising=False)
    gesendet = []
    monkeypatch.setattr(selftest.notify, "send_telegram",
                        lambda text, **k: gesendet.append(text) or True)
    assert selftest.send_digest() == 0
    assert len(gesendet) == 1
    return gesendet[0]


def _slot_now():
    return T.due_digest_slot(dt.datetime.now(UTC), {})[1]


def test_trigger_end_to_end_a_red_run_outside_the_window(tmp_path, monkeypatch):
    slot = _slot_now()
    lauf_zeit = slot - dt.timedelta(hours=16)   # outside the 12 h window
    docs = {lauf_zeit.date().isoformat(): [lauf(lauf_zeit, ESCROW_FAIL)]}
    text = _digest(tmp_path, monkeypatch, docs)
    assert "0 Läufe" in text                    # the window itself is empty
    assert "1 stehend (f-no-stale-escrow FAIL zuletzt" in text


def test_silence_end_to_end_green(tmp_path, monkeypatch):
    slot = _slot_now()
    lauf_zeit = slot - dt.timedelta(hours=16)
    docs = {lauf_zeit.date().isoformat(): [lauf(lauf_zeit, ESCROW_OK)]}
    text = _digest(tmp_path, monkeypatch, docs)
    assert "stehend" not in text
