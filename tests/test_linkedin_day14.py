"""LinkedIn series, ops CK9-CK11 (2026-11-08).

CK9: a reading carries the day the panel was read (`read_at` in the input)
and the button figure has its own column. CK10: the figure that counts is the
first reading at least 14 days after posting, later than day 17 it is marked
late, and the Sunday prompt asks for posts that turned 14. CK11: the table
shows the read date of the day-14 reading and of the newest one.
"""
import datetime
import importlib.util
import json
import pathlib
import sys

import pytest

URL = "https://www.linkedin.com/feed/update/urn:li:share:7522261469170843648/"
OLD = ("https://www.linkedin.com/posts/kerstenkroehl_since-4-march-2026-the-amazon-"
       "services-business-share-7515540568408711168-SnCv")


def _lm():
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "linkedin_metrics.py"
    spec = importlib.util.spec_from_file_location("li_metrics_ck10", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lm(tmp_path):
    m = _lm()
    m.SERIES = str(tmp_path / "linkedin_metrics.jsonl")
    m.DRAFTS = str(tmp_path / "linkedin_drafts.jsonl")
    return m


def write(path, rows):
    pathlib.Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


def record(lm, tmp_path, entry):
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"posted_at": "2026-10-31", "url": URL, **entry}))
    code = lm.record(str(f))
    rows = lm.read(lm.series_path())
    return code, (rows[-1] if rows else None)


def reading(read_at, reached, posted="2026-10-01T09:00:00+00:00", url=OLD, **kw):
    return {"url": url, "posted_at": posted, "read_at": read_at,
            "members_reached": reached, "impressions": reached * 2, **kw}


# ── CK9: the read date is given, checked, and kept ──

def test_a_given_read_date_is_kept_not_replaced_by_now(lm, tmp_path):
    code, row = record(lm, tmp_path, {"read_at": "2026-11-08", "impressions": 163})
    assert code == 0 and row["read_at"] == "2026-11-08"


def test_without_a_read_date_the_time_of_writing_is_used(lm, tmp_path):
    _, row = record(lm, tmp_path, {"impressions": 163})
    assert lm.day_of(row["read_at"]) == lm.today()


@pytest.mark.parametrize("bad,why", [("08.11.2026", "kein Datum"),
                                      ("2999-01-01", "in der Zukunft"),
                                      ("2026-10-30", "vor posted_at")])
def test_an_impossible_read_date_refuses_the_row(lm, tmp_path, capsys, bad, why):
    code, row = record(lm, tmp_path, {"read_at": bad, "impressions": 163})
    assert code == 1 and row is None
    assert why in capsys.readouterr().out


def test_the_button_figure_is_a_column_and_an_unnamed_figure_stays_absent(lm, tmp_path, capsys):
    _, row = record(lm, tmp_path, {"read_at": "2026-11-08", "impressions": 163,
                                   "members_reached": 93, "reactions": 2,
                                   "link_engagements": 1, "button_clicks": 1})
    assert row["button_clicks"] == 1 and row["link_engagements"] == 1
    for k in ("comments", "clicks", "reposts", "saves", "sends"):
        assert k not in row, f"{k} was set although the reading did not name it"
    assert "nicht uebernommen" not in capsys.readouterr().out


def test_a_named_zero_is_kept(lm, tmp_path):
    _, row = record(lm, tmp_path, {"read_at": "2026-11-08", "reactions": 0})
    assert row["reactions"] == 0


# ── CK10: the day-14 reading counts ──

def test_before_day_14_no_figure_counts_and_the_reading_is_the_newest(lm):
    out = lm.day14_per_post([reading("2026-10-11T09:00:00+00:00", 40)])[0]
    assert "day14_read_at" not in out and "members_reached" not in out
    assert out["day14"] == "2026-10-15"
    assert out["latest"]["members_reached"] == 40 and out["latest"]["age"] == 10


def test_on_day_14_the_reading_counts_and_is_not_late(lm):
    out = lm.day14_per_post([reading("2026-10-11T09:00:00+00:00", 40),
                             reading("2026-10-15T13:00:00+00:00", 50),
                             reading("2026-10-23T11:00:00+00:00", 70)])[0]
    assert out["members_reached"] == 50 and out["impressions"] == 100
    assert out["day14_age"] == 14 and out["day14_late"] is False
    assert out["latest"]["members_reached"] == 70


def test_day_17_is_still_on_time_day_18_is_late_with_its_age(lm):
    on = lm.day14_per_post([reading("2026-10-18T09:00:00+00:00", 50)])[0]
    late = lm.day14_per_post([reading("2026-10-19T09:00:00+00:00", 50)])[0]
    assert (on["day14_age"], on["day14_late"]) == (17, False)
    assert (late["day14_age"], late["day14_late"]) == (18, True)


def test_a_late_reading_is_the_first_after_day_14_not_the_newest(lm):
    out = lm.day14_per_post([reading("2026-11-08", 154), reading("2026-11-20", 160)])[0]
    assert out["members_reached"] == 154 and out["day14_age"] == 38
    assert out["day14_late"] is True and out["latest"]["members_reached"] == 160


def test_a_figure_the_day14_reading_lacks_is_not_borrowed(lm):
    out = lm.day14_per_post([reading("2026-10-11", 40, comments=3),
                             reading("2026-10-15", 50)])[0]
    assert "comments" not in out


def test_day_14_is_counted_on_the_zurich_calendar(lm):
    # 12.10. 22:35 UTC is 13.10. in Zurich, Lars' list says 13.10.
    out = lm.day14_per_post([reading("2026-10-26T10:00:00+00:00", 149,
                                     posted="2026-10-12T22:35:00+00:00")])[0]
    assert out["day14"] == "2026-10-27" and "day14_read_at" not in out


def test_the_comparison_takes_the_day14_figure_not_the_newest(lm):
    rows = []
    for i in range(3):
        u = f"https://x/share-{7500000000000000000 + i}-a"
        rows += [reading("2026-10-15", 200, url=u, hook_type="actor"),
                 reading("2026-11-08", 20, url=u, hook_type="actor")]
    for i in range(3):
        u = f"https://x/share-{7600000000000000000 + i}-a"
        rows += [reading("2026-10-15", 50, url=u, hook_type="claim"),
                 reading("2026-11-08", 900, url=u, hook_type="claim")]
    assert "Urteil: Akteur erreicht mehr" in lm.compare_hooks(rows)


def test_a_post_without_a_day14_reading_is_not_compared(lm):
    rows = [reading("2026-10-10", 900 + i, url=f"https://x/share-{7500000000000000000 + i}-a",
                    hook_type="actor") for i in range(6)]
    assert "noch nicht auswertbar: 0 von 5" in lm.compare_hooks(rows)


# ── CK10: the Sunday prompt asks for posts that turned 14 ──

# The series of 08.11.2026 in short: the 31.10. post was read on day 8, the
# 27.10. post on day 12, the 20.10. post on day 19.
SERIES_0811 = [
    reading("2026-11-08", 93, posted="2026-10-31", url=URL, topic="North Korea"),
    reading("2026-11-08", 127, posted="2026-10-27",
            url="https://www.linkedin.com/feed/update/urn:li:share:7520580742603735040/",
            topic="Sandbags"),
    reading("2026-11-08", 78, posted="2026-10-20T08:34:00+00:00",
            url="https://x/share-7517564558576832512-MDEG", topic="Negotiations"),
]


def test_the_31_10_post_is_due_on_14_11_and_asked_on_sunday_15_11(lm):
    write(lm.SERIES, SERIES_0811)
    text = lm.prompt(as_of=datetime.date(2026, 11, 15))
    assert "2 Post(s) haben Tag 14 erreicht" in text
    assert "North Korea" in text and "14 Tage alt am 14.11." in text
    assert "Sandbags" in text and "14 Tage alt am 10.11." in text
    assert "Negotiations" not in text     # has its day-14 reading (late, day 19)


def test_on_08_11_none_of_them_is_due(lm):
    write(lm.SERIES, SERIES_0811)
    assert lm.due_day14(SERIES_0811, as_of=datetime.date(2026, 11, 8)) == []
    assert "Keine offenen Posts." in lm.prompt(as_of=datetime.date(2026, 11, 8))


def test_a_post_that_turned_14_before_the_last_prompt_is_not_asked_again(lm):
    due = lm.due_day14(SERIES_0811, as_of=datetime.date(2026, 11, 22))
    assert due == []


def test_since_widens_the_window_after_a_missed_sunday(lm):
    due = lm.due_day14(SERIES_0811, as_of=datetime.date(2026, 11, 22),
                       since=datetime.date(2026, 11, 8))
    assert [r["topic"] for r in due] == ["Sandbags", "North Korea"]


def test_a_probe_with_as_of_never_sends(lm, monkeypatch):
    sent = []
    monkeypatch.setattr(lm.notify, "send_telegram", lambda *a, **k: sent.append(a))
    assert lm.main(["--prompt", "--send", "--as-of", "2026-11-15"]) == 2
    assert sent == []


# ── CK11: the table shows both read dates ──

def test_the_table_shows_the_day14_read_date_and_the_newest(lm):
    write(lm.SERIES, [reading("2026-10-15T13:00:00+00:00", 50),
                      reading("2026-11-08", 70)] + SERIES_0811[:1])
    head, first, second = lm.table().splitlines()[:3]
    assert "T14 am" in head and "neu: Reach" in head
    assert "2026-10-15   14" in first and "70" in first and "2026-11-08" in first
    assert "ab 11-14" in second and "93" in second
