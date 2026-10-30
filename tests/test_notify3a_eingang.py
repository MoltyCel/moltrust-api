"""ops NOTIFY-3a: the inbox of the daily report.

A measurement report is filed in app/tagesbericht_eingang and travels with
the next selftest digest (08:00/20:00 UTC). The inbox is emptied only after
Telegram accepted the digest; an undelivered digest leaves it standing, so the
next one carries everything (NOTIFY-3 criterion 4).

Every test points MOLTRUST_NOTIFY_STATE_DIR at tmp_path: the real inbox is
production state and no test touches it.
"""
import datetime as dt
import json
import subprocess
import sys

import pytest

from app import tagesbericht_eingang as eingang

T0 = dt.datetime(2026, 10, 27, 20, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _own_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(tmp_path / "selftest"))


# --- the inbox itself -------------------------------------------------------

def test_a_filed_report_can_be_read_back():
    assert eingang.ablegen("lars_reply_metrics", "Median 31 Aufrufe", now=T0) is True
    e = eingang.lesen()
    assert [(x["quelle"], x["text"]) for x in e] == [("lars_reply_metrics", "Median 31 Aufrufe")]
    assert eingang.zeilen(e) == [
        "Messberichte seit dem letzten Bericht (1):",
        "— lars_reply_metrics, 27.10. 20:00Z:",
        "Median 31 Aufrufe",
    ]


def test_an_empty_inbox_adds_no_line():
    assert eingang.lesen() == []
    assert eingang.zeilen([]) == []


def test_empty_text_or_no_source_is_refused():
    assert eingang.ablegen("x", "   ") is False
    assert eingang.ablegen("", "text") is False
    assert eingang.lesen() == []


def test_an_unwritable_inbox_reports_false(tmp_path, monkeypatch):
    blocker = tmp_path / "datei"
    blocker.write_text("")
    # The state directory is a file: nothing can be created below it.
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(blocker))
    assert eingang.ablegen("x", "text") is False


def test_emptying_removes_only_what_was_read():
    eingang.ablegen("a", "eins")
    gelesen = eingang.lesen()
    eingang.ablegen("b", "zwei")  # arrives while the digest is on its way
    assert eingang.leeren({e["id"] for e in gelesen}) == 1
    assert [e["text"] for e in eingang.lesen()] == ["zwei"]


def test_a_broken_line_goes_out_once_and_is_then_emptied():
    eingang.ablegen("a", "eins")
    with open(eingang.pfad(), "a", encoding="utf-8") as fh:
        fh.write("{kein json\n")
    e = eingang.lesen()
    assert len(e) == 2 and e[1]["text"].startswith("unlesbare Zeile im Eingang")
    assert eingang.leeren({x["id"] for x in e}) == 2
    assert eingang.lesen() == []


def test_the_shell_entry_files_a_report(tmp_path):
    env = {"MOLTRUST_NOTIFY_STATE_DIR": str(tmp_path / "sh"), "PATH": "/usr/bin:/bin"}
    p = subprocess.run([sys.executable, "-m", "app.tagesbericht_eingang",
                        "--quelle", "weekly_traffic.sh", "--stdin"],
                       input="Besucher 41\n", text=True, env=env,
                       capture_output=True, cwd=str(pathlib_root()))
    assert p.returncode == 0, p.stderr
    zeile = json.loads((tmp_path / "sh" / eingang.DATEI).read_text())
    assert (zeile["quelle"], zeile["text"]) == ("weekly_traffic.sh", "Besucher 41")


def pathlib_root():
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent


# --- the digest reads it, and empties it only after delivery ----------------

class _Body:
    def read(self):
        return b""


def _digest(monkeypatch, tmp_path, delivered):
    from scripts import selftest
    sent = []
    monkeypatch.setattr(selftest, "OUTDIR", str(tmp_path / "out"))
    monkeypatch.setattr(selftest.throttle, "state", lambda: {"gesendet": {}})
    monkeypatch.setattr(selftest.throttle, "due_digest_slot",
                        lambda now, g: ("2026-10-27T20", T0))
    monkeypatch.setattr(selftest.throttle, "save_state", lambda st: None)
    monkeypatch.setattr(selftest.notify, "stehende_befunde", lambda now: [])

    def send(text, *, channel, **k):
        sent.append((text, channel, k))
        return delivered

    monkeypatch.setattr(selftest.notify, "send_telegram", send)
    monkeypatch.delenv("HEALTHCHECK_SELFTEST_DIGEST_URL", raising=False)
    monkeypatch.setattr(selftest.urllib.request, "urlopen",
                        lambda url, timeout=0: _Body())
    # Die Zustellung selbst wird geprueft, also braucht die Meldung einen
    # Handgriff (ops MELD-1).
    from tests.handgriff import erzwinge_handgriff
    erzwinge_handgriff(monkeypatch)
    return selftest.send_digest(), sent


def test_the_digest_carries_the_inbox_and_empties_it(monkeypatch, tmp_path):
    eingang.ablegen("lars_reply_metrics", "Zwischenstand: Median 31 Aufrufe", now=T0)
    rc, sent = _digest(monkeypatch, tmp_path, delivered=True)
    assert rc == 0
    text, channel, k = sent[0]
    assert channel == "stats" and k == {"drosseln": False, "chunk": True}
    assert "— lars_reply_metrics, 27.10. 20:00Z:" in text
    assert "Zwischenstand: Median 31 Aufrufe" in text
    # The inbox lines stand before the pointer to the detail file.
    assert text.rstrip().splitlines()[-1].startswith("Details: ")
    assert eingang.lesen() == []
    detail = (tmp_path / "out" / "digest-2026-10-27T20.md").read_text()
    assert "Zwischenstand: Median 31 Aufrufe" in detail


def test_a_report_line_that_looks_like_a_fingerprint_is_not_filtered(monkeypatch, tmp_path):
    eingang.ablegen("probe", "Messwert 0123456789abcdef")
    rc, sent = _digest(monkeypatch, tmp_path, delivered=True)
    assert "Messwert 0123456789abcdef" in sent[0][0]


def test_an_undelivered_digest_leaves_the_inbox_standing(monkeypatch, tmp_path):
    """NOTIFY-3 criterion 4: nothing is lost when a digest does not go out."""
    eingang.ablegen("lars_reply_metrics", "Zwischenstand", now=T0)
    rc, sent = _digest(monkeypatch, tmp_path, delivered=False)
    assert rc == 1
    assert "Zwischenstand" in sent[0][0]
    assert [e["text"] for e in eingang.lesen()] == ["Zwischenstand"]

    # Between the two digests a second report arrives; the next digest has both.
    eingang.ablegen("digest_metrics", "Antworten 4", now=T0)
    rc, sent = _digest(monkeypatch, tmp_path, delivered=True)
    assert rc == 0
    assert "Zwischenstand" in sent[0][0] and "Antworten 4" in sent[0][0]
    assert eingang.lesen() == []


def test_an_unreadable_inbox_costs_a_line_not_the_digest(monkeypatch, tmp_path):
    def kaputt(**k):
        raise OSError("weg")
    monkeypatch.setattr(eingang, "lesen", kaputt)
    rc, sent = _digest(monkeypatch, tmp_path, delivered=True)
    assert rc == 0
    assert "Eingang des Tagesberichts nicht lesbar: OSError: weg" in sent[0][0]
