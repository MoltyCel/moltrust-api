"""NOTIFY-1, the parts around the throttle: standing findings in the daily
report with their owner, the digest pings only after a delivery, and the
web-root watch treats a throttled message as delivered earlier."""
import datetime as dt
import json

import pytest

from app import befund_jobs, notify as n

T0 = dt.datetime(2026, 10, 23, 8, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def store(tmp_path):
    return str(tmp_path / "fp.json")


def test_a_finding_seen_again_is_standing_with_its_first_line(store):
    n.throttle("<b>[FAIL]</b> c-x — kaputt\nDetail", now=T0, path=store)
    assert n.stehende_befunde(T0, store) == []          # sent once, not repeated yet
    n.throttle("<b>[FAIL]</b> c-x — kaputt\nDetail", now=T0 + dt.timedelta(hours=1), path=store)
    [b] = n.stehende_befunde(T0 + dt.timedelta(hours=1), store)
    assert b["anfang"] == "[FAIL] c-x — kaputt"
    assert b["erstmals"].startswith("2026-10-23T08:00")


def test_a_gone_finding_is_not_standing(store):
    for h in (0, 1):
        n.throttle("[FAIL] c-x — kaputt", now=T0 + dt.timedelta(hours=h), path=store)
    # last interval 1 h, gone after 3 h
    assert n.stehende_befunde(T0 + dt.timedelta(hours=5), store) == []


def test_an_old_entry_without_text_gets_it_on_the_next_sighting(store):
    fp = n.fingerprint("[WARN] c-y — alt")
    json.dump({fp: {"erstmals": T0.isoformat(), "zuletzt_gesehen": T0.isoformat(),
                    "zuletzt_gesendet": T0.isoformat(), "gesamt": 1,
                    "seit_letzter_meldung": 0}}, open(store, "w"))
    n.throttle("[WARN] c-y — alt", now=T0 + dt.timedelta(hours=1), path=store)
    assert n.stehende_befunde(T0 + dt.timedelta(hours=1), store)[0]["anfang"] == "[WARN] c-y — alt"


@pytest.mark.parametrize("text,job", [
    ("Web-Root: 1 Schreibvorgang/-vorgaenge ausserhalb deploy.sh in /var/www/html", "WRW-3"),
    ("[WARN] c-webroot-write-watch-runs — ohne 'frisch '", "WRW-2"),
    ("[FAIL] c-radar-reading-list-only — 2 (erwartet eq 0)", "RADAR-LOG"),
    ("[FAIL] c-context-urls-resolve — ohne 'gleich '", "E3"),
    ("[WARN] c-external-schedule-fires — 1 (erwartet eq 0)", None),
])
def test_the_owner_comes_from_one_file(text, job):
    assert befund_jobs.owner(text) == job


def test_the_digest_line_names_the_job_or_says_there_is_none():
    from scripts import selftest
    lines = selftest.stehend_lines([
        {"fp": "a", "erstmals": "2026-10-21T09:00:00+00:00",
         "anfang": "Web-Root: 1 Schreibvorgang/-vorgaenge ausserhalb deploy.sh in /var/www/html"},
        {"fp": "b", "erstmals": "2026-10-23T12:37:00+00:00",
         "anfang": "[WARN] c-external-schedule-fires — 1 (erwartet eq 0)"},
    ])
    assert lines[0].startswith("Steht seit 21.10. (Job WRW-3): Web-Root:")
    assert lines[1].startswith("Steht seit 23.10. (kein Job besitzt ihn): [WARN] c-external")


def test_a_step_for_lars_without_a_link_is_refused(tmp_path):
    p = tmp_path / "bj.json"
    p.write_text(json.dumps({"eintraege": [{"muster": "x", "job": "J", "lars": "mach was"}]}))
    with pytest.raises(ValueError):
        befund_jobs.entries(p)


def test_an_entry_without_job_is_refused(tmp_path):
    p = tmp_path / "bj.json"
    p.write_text(json.dumps({"eintraege": [{"muster": "x"}]}))
    with pytest.raises(ValueError):
        befund_jobs.entries(p)


# --- the digest: unthrottled, and no ping without a delivery ----------------

def _digest(monkeypatch, delivered, tmp_path):
    from scripts import selftest
    pinged, sent = [], []
    # send_digest writes its detail file into OUTDIR; never the real one.
    monkeypatch.setattr(selftest, "OUTDIR", str(tmp_path))
    monkeypatch.setattr(selftest.throttle, "state", lambda: {"gesendet": {}})
    monkeypatch.setattr(selftest.throttle, "due_digest_slot",
                        lambda now, g: ("2026-10-23T08", T0))
    monkeypatch.setattr(selftest.throttle, "save_state", lambda st: None)
    monkeypatch.setattr(selftest.notify, "stehende_befunde", lambda now: [])

    def send(text, *, channel, **k):
        sent.append(k)
        return delivered

    monkeypatch.setattr(selftest.notify, "send_telegram", send)
    monkeypatch.setenv("HEALTHCHECK_SELFTEST_DIGEST_URL", "https://hc.example/ping")
    monkeypatch.setattr(selftest.urllib.request, "urlopen",
                        lambda url, timeout=0: pinged.append(url) or _Body())
    rc = selftest.send_digest()
    return rc, sent, pinged


class _Body:
    def read(self):
        return b""


def test_a_delivered_digest_pings(monkeypatch, tmp_path):
    rc, sent, pinged = _digest(monkeypatch, True, tmp_path)
    assert rc == 0 and pinged == ["https://hc.example/ping"]
    assert sent == [{"drosseln": False}]


def test_an_undelivered_digest_does_not_ping(monkeypatch, tmp_path):
    rc, sent, pinged = _digest(monkeypatch, False, tmp_path)
    assert rc == 1 and pinged == []


# --- the web-root watch -----------------------------------------------------

def test_the_same_write_in_two_runs_ends_the_second_with_0(tmp_path, monkeypatch):
    """Criterion 9: the first run delivers, the second is throttled (same
    finding) and still advances its state instead of returning 1 forever."""
    import time
    from scripts import webroot_write_watch as w
    root = tmp_path / "www"; root.mkdir()
    ledger, state, bl = tmp_path / "l.jsonl", tmp_path / "s.json", tmp_path / "b.json"
    bl.write_text(json.dumps({"generated": {}}))
    reason = {"v": ""}
    monkeypatch.setattr(n, "letzter_grund", lambda: reason["v"])
    calls = []

    def send(text, *, channel, **k):
        calls.append(text)
        if len(calls) == 1:
            reason["v"] = ""
            return True
        reason["v"] = "gedrosselt"
        return False

    monkeypatch.setattr(n, "send_telegram", send)
    args = ["--root", str(root), "--ledger", str(ledger), "--state", str(state),
            "--baseline", str(bl)]
    state.write_text(json.dumps({"cursor": time.time() - 5}))
    (root / "a.html").write_text("a")
    assert w.main(args) == 0
    # the watch reads the same write again, e.g. its state was reset
    state.write_text(json.dumps({"cursor": time.time() - 5}))
    assert w.main(args) == 0 and len(calls) == 2


def test_a_failed_webroot_send_still_keeps_the_state(tmp_path, monkeypatch):
    import time
    from scripts import webroot_write_watch as w
    root = tmp_path / "www"; root.mkdir()
    ledger, state, bl = tmp_path / "l.jsonl", tmp_path / "s.json", tmp_path / "b.json"
    bl.write_text(json.dumps({"generated": {}}))
    monkeypatch.setattr(n, "letzter_grund", lambda: "fehlgeschlagen")
    monkeypatch.setattr(n, "send_telegram", lambda text, *, channel, **k: False)
    state.write_text(json.dumps({"cursor": time.time() - 5}))
    (root / "a.html").write_text("a")
    before = json.loads(state.read_text())["cursor"]
    assert w.main(["--root", str(root), "--ledger", str(ledger), "--state", str(state),
                   "--baseline", str(bl)]) == 1
    assert json.loads(state.read_text())["cursor"] == before
