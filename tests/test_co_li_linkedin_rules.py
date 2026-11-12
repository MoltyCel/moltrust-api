"""CO-LI: LinkedIn drafts — source at most 7 days old, density, compressed negation.

Herkunft: Lars, Issue #1 08.11.2026 (CO4, CO5) und Chat 09.11.2026 (CO7-CO9).

  K2  CO4/CO8 source date checked in code: 7 days passes, 8 refused, none refused
  K3  CO5 no fresh source: one message a day, no draft
  K4  CO7 density: three sentences a paragraph, blank line between paragraphs,
      one number a sentence except the first
  K5  CO9 compressed negation refused; "doesn't say anything" passes
  K6  CO8 an open draft on an old source is closed and not offered again
"""
import datetime
import json

import pytest

from agents import linkedin_feedback as lf
from agents import linkedin_post, linkedin_rules as lr, syndicate

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 12, 6, 0, tzinfo=UTC)


def rfc822(d: datetime.datetime) -> str:
    return d.strftime("%a, %d %b %Y %H:%M:%S +0000")


def item(days_old: float | None, link="https://moltrust.ch/blog/a.html"):
    out = {"title": "A", "link": link, "article_text": "x"}
    if days_old is not None:
        out["pub_date"] = rfc822(NOW - datetime.timedelta(days=days_old))
    return out


# ── K2 ──

def test_source_seven_days_old_passes():
    assert lr.source_problem(item(7), now=NOW) is None


def test_source_eight_days_old_is_refused():
    assert "älter als 7 Tage" in lr.source_problem(item(8), now=NOW)


def test_source_without_date_is_refused():
    assert lr.source_problem(item(None), now=NOW) == "Quelle ohne Datum"


def test_without_source_is_refused():
    assert lr.source_problem({"title": "A"}, now=NOW) == "keine Quelle"


def test_iso_source_date_is_read():
    assert lr.source_problem({"source": "x", "source_date": (NOW - datetime.timedelta(days=1)).isoformat()},
                             now=NOW) is None


@pytest.fixture
def tg(monkeypatch, tmp_path):
    seen = {"sent": []}

    def fake_send(text, **kw):
        seen["sent"].append({"text": text, **kw})
        return 5000 + len(seen["sent"])

    monkeypatch.setattr(syndicate.notify, "send_telegram_message", fake_send)
    monkeypatch.setattr(syndicate, "send_telegram",
                        lambda body, **kw: seen["sent"].append({"text": body, **kw}) or True)
    monkeypatch.setattr(syndicate.notify, "chat_id_for", lambda ch: "4711")
    monkeypatch.setattr(syndicate, "NO_SOURCE_STATE", str(tmp_path / "nosrc.json"))
    monkeypatch.setattr(linkedin_post, "pending_path", lambda: str(tmp_path / "p.json"))
    monkeypatch.setattr(syndicate.voice_gate, "scan", lambda *a, **k: {"violations": []})
    monkeypatch.setattr(syndicate.voice_gate, "format_report", lambda s: "PASS")
    from app import ablage
    monkeypatch.setattr(ablage, "ablegen", lambda *a, **k: None)
    return seen


GOOD = ("Agents now hold 40% of API keys in the survey.\n\n"
        "A key names who holds it. It does not name what the holder may do.\n\n"
        "https://moltrust.ch/blog/a.html")


def test_old_source_is_not_offered(tg, monkeypatch):
    monkeypatch.setattr(lr, "source_problem", lambda it, now=None: "Quelle vom 2026-05-02, älter als 7 Tage")
    assert syndicate.deliver_linkedin(item(190), GOOD) is False
    assert linkedin_post.load_pending() == {}
    assert all(m.get("art") != "linkedin-entwurf" for m in tg["sent"])


def test_fresh_source_is_offered_with_source_and_date(tg):
    it = {**item(None), "pub_date": rfc822(datetime.datetime.now(UTC) - datetime.timedelta(days=1))}
    assert syndicate.deliver_linkedin(it, GOOD) is True
    msg = [m for m in tg["sent"] if m.get("art") == "linkedin-entwurf"][0]
    assert "Quelle: https://moltrust.ch/blog/a.html · " in msg["text"]
    entry = list(linkedin_post.load_pending().values())[0]
    assert entry["source_date"]


# ── K3 ──

def test_no_fresh_source_reports_once_a_day(tg):
    day = datetime.date(2026, 11, 12)
    assert syndicate.note_no_source(item(30), "älter", today=day) is True
    assert syndicate.note_no_source(item(31), "älter", today=day) is False
    assert syndicate.note_no_source(item(31), "älter", today=day + datetime.timedelta(days=1)) is True
    msgs = [m for m in tg["sent"] if m.get("art") == "linkedin-keine-quelle"]
    assert len(msgs) == 2 and msgs[0]["text"].startswith("Kein LinkedIn-Entwurf")


# ── K4 ──

def test_density_passes_a_clean_text():
    assert lr.density_problems(GOOD) == []


def test_four_sentences_in_a_paragraph_is_refused():
    t = "One. Two. Three. Four.\n\nhttps://x.y/z"
    assert any("4 Sätze" in p for p in lr.density_problems(t))


def test_line_break_without_blank_line_is_refused():
    t = "First sentence.\nSecond sentence.\n\nhttps://x.y/z"
    assert any("Leerzeile" in p for p in lr.density_problems(t))


def test_two_numbers_in_a_later_sentence_are_refused():
    t = "Hook with 40% and 3 cases.\n\nThen 12 agents held 7 keys.\n\nhttps://x.y/2026/z"
    probs = lr.density_problems(t)
    assert len(probs) == 1 and "zwei Zahlen" in probs[0]


def test_two_numbers_in_the_hook_pass_and_urls_do_not_count():
    t = "Hook with 40% and 3 cases.\n\nhttps://moltrust.ch/blog/2026/11/12.html"
    assert lr.density_problems(t) == []


def test_decimal_number_counts_once_and_does_not_end_a_sentence():
    t = "Hook.\n\nThe share rose to 4.2 percent.\n\nhttps://x.y/z"
    assert lr.density_problems(t) == []


# ── K5 ──

@pytest.mark.parametrize("phrase", ["says nothing", "does nothing", "means nothing",
                                    "adds nothing", "proves nothing", "Says Nothing"])
def test_compressed_negation_is_refused(phrase):
    assert lr.negation_problems(f"The badge {phrase} about scope.")


@pytest.mark.parametrize("phrase", ["doesn't say anything", "is nothing new",
                                    "for nothing", "almost nothing", "nothing changes"])
def test_other_forms_pass(phrase):
    assert lr.negation_problems(f"The badge {phrase} about scope.") == []


def test_negation_blocks_the_draft(tg):
    it = {**item(None), "pub_date": rfc822(datetime.datetime.now(UTC))}
    bad = "A key says nothing about scope.\n\nhttps://moltrust.ch/blog/a.html"
    assert syndicate.deliver_linkedin(it, bad) is False
    blocked = [m for m in tg["sent"] if m.get("art") == "entwurf-blockiert"]
    assert blocked and "CO9" in blocked[0]["text"]
    assert linkedin_post.load_pending() == {}


# ── K6 ──

def test_open_draft_on_old_source_is_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(linkedin_post, "pending_path", lambda: str(tmp_path / "p.json"))
    monkeypatch.setattr(lf, "versions_path", lambda: str(tmp_path / "v.jsonl"))
    linkedin_post.save_pending({
        "old": {"text": "Polymarket", "title": "Polymarket May", "source": "https://moltrust.ch/blog/p.html",
                "source_date": "2026-05-20T00:00:00+00:00", "message_id": 1},
        "nodate": {"text": "x", "title": "before CO-LI", "source": "https://moltrust.ch/blog/q.html",
                   "message_id": 2},
        "fresh": {"text": "y", "title": "new", "source": "https://moltrust.ch/blog/r.html",
                  "source_date": (NOW - datetime.timedelta(days=2)).isoformat(), "message_id": 3},
        "posted": {"text": "z", "result": "gepostet", "source": "s", "message_id": 4},
    })
    assert sorted(lf.sweep_stale(now=NOW)) == ["nodate", "old"]
    p = linkedin_post.load_pending()
    assert p["old"]["result"] == "verworfen" and "CO8" in p["old"]["closed_reason"]
    assert "result" not in p["fresh"] and p["posted"]["result"] == "gepostet"
    rows = [json.loads(x) for x in (tmp_path / "v.jsonl").read_text().splitlines()]
    assert {r["draft_id"] for r in rows} == {"old", "nodate"}
    monkeypatch.setattr(lf.linkedin_rules, "source_problem",
                        lambda it, now=None: None)
    assert 1 not in lf.awaiting() and 3 in lf.awaiting()
