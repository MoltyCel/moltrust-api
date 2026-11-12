"""ops MELD-BERICHT (12.11.2026): the reports Lars ordered reach him again.

NOTIFY-4 (#1032, deployed 07.11.2026 11:26:44Z) filed every `scout-bericht`,
and with it News Scout and Content-Scout, which Lars reads on every run. The
kind is HANDELN again and goes out uncut. The other senders that shared the
kind (ThreadWatch, W3C ListWatch, PR-Monitor, the review runs, the watchdog
report) moved to the existing ABLAGE kind `pipeline-bericht`; what NOTIFY-4
was built to stop stays stopped.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app import meldungsarten as MA
from app import notify

ROOT = Path(__file__).resolve().parent.parent

# The ordered reports: file, and the first words of their message.
BESTELLT = {
    "agents/news_scout.py": "MolTrust News Scout",
    "workers/content_scout/pipeline.py": "Content-Scout",
}
# What shared the kind until 12.11.2026 and stays filed.
ABGELEGT = (
    "scripts/threadwatch.py",
    "scripts/w3c_listwatch.py",
    "agents/pr_monitor.py",
    "agents/ai_review.py",
    "agents/ai_review_v2.py",
    "scripts/concept_review.py",
    "agents/watchdog.py",
)


def _arten(rel: str) -> list[str]:
    """Every literal art= keyword in the file."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "art" and isinstance(kw.value, ast.Constant):
                    out.append(kw.value.value)
    return out


def test_the_ordered_reports_are_handeln_now():
    ein = MA.einordnen("scout-bericht", "📡 MolTrust News Scout — 2026-11-12")
    assert (ein.klasse, ein.zustellung) == (MA.HANDELN, MA.SOFORT)


@pytest.mark.parametrize("rel", sorted(BESTELLT))
def test_news_scout_and_content_scout_send_as_scout_bericht(rel):
    assert _arten(rel).count("scout-bericht") == 1, rel


@pytest.mark.parametrize("rel", ABGELEGT)
def test_the_former_sharers_are_filed_under_pipeline_bericht(rel):
    arten = _arten(rel)
    assert "scout-bericht" not in arten, rel
    assert "pipeline-bericht" in arten, rel
    assert MA.ARTEN["pipeline-bericht"].klasse == MA.ABLAGE


def test_only_the_two_ordered_reports_use_the_kind():
    """A third sender under scout-bericht would reach Lars unasked."""
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    users = []
    for rel in tracked:
        if rel.startswith("tests/"):
            continue
        try:
            if "scout-bericht" in _arten(rel):
                users.append(rel)
        except SyntaxError:
            continue
    assert sorted(users) == sorted(BESTELLT)


# ------------------------------------------------------------------ behaviour

@pytest.fixture
def posted(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_NOTIFY", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100test")
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MOLTRUST_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(notify, "SENT_LOG", str(tmp_path / "state" / "sent.jsonl"))
    monkeypatch.setattr(notify, "FINGERPRINTS", str(tmp_path / "state" / "fp.json"))
    notify._ENV_CACHE.clear()
    calls = []

    class R:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"result": {"message_id": 41}}

    def post(url, data=None, timeout=None):
        calls.append(data)
        return R()

    monkeypatch.setattr(notify.requests, "post", post)
    return calls


def _log(tmp_path) -> list:
    p = tmp_path / "state" / "sent.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_a_long_report_goes_out_whole(posted, tmp_path):
    report = "📡 <b>MolTrust News Scout — 2026-11-12</b>\n\n" + "\n".join(
        f"• Fundstelle {i}" for i in range(1, 15))
    assert notify.send_telegram(report, channel=notify.WORKLOG, parse_mode="HTML",
                                art="scout-bericht") is True
    assert len(posted) == 1
    assert "Fundstelle 14" in posted[0]["text"]
    assert "Tagesablage" not in posted[0]["text"]
    line = _log(tmp_path)[-1]
    assert (line["art"], line["klasse"], line["erfolg"], line["http_status"]) == \
        ("scout-bericht", "HANDELN", True, 200)


def test_a_threadwatch_report_is_still_filed(posted, tmp_path):
    """The negative control of criterion 3."""
    assert notify.send_telegram("🔭 ThreadWatch — 2026-11-12 08:00 UTC\n📌 roster",
                                channel=notify.WORKLOG, art="pipeline-bericht") is True
    assert posted == []
    line = _log(tmp_path)[-1]
    assert (line["grund"], line["klasse"], line["erfolg"]) == ("ablage", "ABLAGE", False)


def test_other_handeln_kinds_are_still_cut(posted):
    long = "\n".join(f"Zeile {i}" for i in range(1, 12))
    notify.send_telegram(long, channel=notify.ALERTS, art="endpunkt-ausfall")
    assert len(notify.handeln_zeilen(posted[0]["text"])) == 4


# ------------------------------------------------------------------ the guard

def _zeile(ts, anfang, *, erfolg=True, grund=None, art="scout-bericht"):
    z = {"ts": ts, "kanal": "worklog", "erfolg": erfolg, "text_anfang": anfang, "art": art}
    if grund:
        z["grund"] = grund
    return json.dumps(z)


def _check(tmp_path, zeilen, capsys):
    import datetime as dt

    from scripts import check_bestellte_berichte as cb
    p = tmp_path / "sent.jsonl"
    p.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    rc = cb.main(str(p), dt.datetime(2026, 11, 13, 4, 0, tzinfo=dt.timezone.utc))
    return rc, capsys.readouterr().out.strip().splitlines()


def test_guard_is_silent_on_a_normal_day(tmp_path, capsys):
    rc, out = _check(tmp_path, [
        _zeile("2026-11-12T06:30:15+00:00", "🔎 Content-Scout: 0 lead(s)"),
        _zeile("2026-11-12T17:00:47+00:00", "📡 <b>MolTrust News Scout — 2026-11-12</b>"),
        _zeile("2026-11-12T17:30:12+00:00", "🔎 Content-Scout: 1 lead(s)"),
    ], capsys)
    assert (rc, out) == (0, ["0"])


def test_guard_fires_on_the_days_after_07_11(tmp_path, capsys):
    """What the log held from 07.11. on: the reports filed, nothing delivered."""
    rc, out = _check(tmp_path, [
        _zeile("2026-11-12T06:30:15+00:00", "🔎 Content-Scout: 0 lead(s)",
               erfolg=False, grund="ablage"),
        _zeile("2026-11-12T17:00:47+00:00", "📡 <b>MolTrust News Scout — 2026-11-12</b>",
               erfolg=False, grund="ablage"),
    ], capsys)
    assert rc == 1
    assert out[-1] == "2"


def test_guard_counts_one_missing_content_scout_run(tmp_path, capsys):
    rc, out = _check(tmp_path, [
        _zeile("2026-11-12T06:30:15+00:00", "🔎 Content-Scout: 0 lead(s)"),
        _zeile("2026-11-12T17:00:47+00:00", "📡 <b>MolTrust News Scout — 2026-11-12</b>"),
    ], capsys)
    assert (rc, out) == (1, ["Content-Scout: 1 of 2 delivered in 24 h", "1"])


def test_guard_ignores_a_pipeline_bericht_with_the_same_words(tmp_path, capsys):
    rc, out = _check(tmp_path, [
        _zeile("2026-11-12T06:30:15+00:00", "🔎 Content-Scout: 0 lead(s)", art="pipeline-bericht"),
        _zeile("2026-11-12T17:00:47+00:00", "📡 <b>MolTrust News Scout — 2026-11-12</b>"),
        _zeile("2026-11-12T17:30:12+00:00", "🔎 Content-Scout: 1 lead(s)"),
    ], capsys)
    assert out[-1] == "1"


def test_guard_reports_an_unreadable_log(tmp_path, capsys):
    import datetime as dt

    from scripts import check_bestellte_berichte as cb
    rc = cb.main(str(tmp_path / "nope.jsonl"), dt.datetime.now(dt.timezone.utc))
    assert rc == 2
    assert capsys.readouterr().out.startswith("UNREADABLE")


# ------------------------------------------------------------- the catch-up

def test_catch_up_reads_only_the_ordered_reports_since_the_deploy(tmp_path):
    import datetime as dt

    from scripts import meld_bericht_nachreichung as nr
    (tmp_path / "2026-11-07.md").write_text(
        "## 06:30:10Z · scout-bericht · worklog\n\n🔎 Content-Scout: 2 lead(s)\n\n"
        "## 17:00:47Z · scout-bericht · worklog\n\n📡 MolTrust News Scout — 2026-11-07\n• A\n\n"
        "## 18:04:22Z · scout-bericht · worklog\n\n(1/2)\n🔭 ThreadWatch — 2026-11-07\n\n"
        "## 23:00:23Z · scout-bericht · worklog\n\nW3C ListWatch — 13 new message(s)\n",
        encoding="utf-8")
    (tmp_path / "2026-11-08.md").write_text(
        "## 06:30:10Z · scout-bericht · worklog\n\n🔎 Content-Scout: 0 lead(s)\n\n"
        "## 07:00:00Z · messbericht · stats\n\nContent-Scout: nicht gemeint\n",
        encoding="utf-8")
    rs = nr.laeufe(str(tmp_path), dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc))
    assert [(f"{r['ts']:%d.%m. %H:%M}", r["name"]) for r in rs] == [
        ("07.11. 17:00", "News Scout"), ("08.11. 06:30", "Content-Scout")]
    text = nr.nachricht(rs)
    assert text.startswith("Nachreichung: 2 Läufe von Content-Scout und News Scout")
    assert "— 07.11. 17:00Z · News Scout\n📡 MolTrust News Scout — 2026-11-07\n• A" in text
    assert "ThreadWatch" not in text and "ListWatch" not in text


def test_catch_up_sends_once(tmp_path, monkeypatch, posted):
    from scripts import meld_bericht_nachreichung as nr
    rep = tmp_path / "reports"
    rep.mkdir(exist_ok=True)
    (rep / "2026-11-09.md").write_text(
        "## 17:00:50Z · scout-bericht · worklog\n\n📡 MolTrust News Scout — 2026-11-09\n",
        encoding="utf-8")
    monkeypatch.setattr(nr, "MARKE", tmp_path / "marke.json")
    assert nr.main(["--senden", "--bis", "2026-11-12T16:00:00+00:00"]) == 0
    assert len(posted) == 1 and "09.11. 17:00Z · News Scout" in posted[0]["text"]
    assert nr.main(["--senden", "--bis", "2026-11-12T16:00:00+00:00"]) == 3
    assert len(posted) == 1
