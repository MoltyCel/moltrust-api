"""LinkedIn series, ops LI1 (2026-10-15): out-of-network share, hook type, media.

Each new field has a test that fires and a test that stays quiet, and the
comparison after five posts has both too.
"""
import importlib.util
import json
import pathlib
import sys

import pytest


def _lm():
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "linkedin_metrics.py"
    spec = importlib.util.spec_from_file_location("li_metrics_li1", path)
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


def _record(lm, tmp_path, entry):
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"posted_at": "2026-10-20T09:05:00+00:00",
                             "url": "https://www.linkedin.com/feed/update/x/", **entry}))
    code = lm.record(str(f))
    rows = lm.read(lm.series_path())
    return code, (rows[-1] if rows else None)


# ── the rule for the hook type ──

def test_hook_rule_fires_a_digit_in_the_first_sentence_is_a_number():
    lm = _lm()
    assert lm.hook_type_of("Since 4 March 2026, the agreement includes an Agent Policy. More.") == "number"


def test_hook_rule_quiet_a_story_without_a_digit_is_narrative():
    lm = _lm()
    assert lm.hook_type_of("An agent can show you what it did. It has 3 logs.") == "narrative", \
        "a digit in the second sentence must not make it a number hook"


def test_hook_rule_quiet_no_text_gives_no_type():
    assert _lm().hook_type_of("") is None


# ── record(): the four fields ──

def test_record_fires_the_fields_are_kept(lm, tmp_path):
    code, row = _record(lm, tmp_path, {
        "impressions": 120, "members_reached": 80, "out_of_network_share": 35,
        "hook": "RSAC 2026 named Gap 1 as unsolved. Then we tested it.",
        "media": "image"})
    assert code == 0
    assert row["out_of_network_share"] == 35 and row["media"] == "image"
    assert row["hook"] == "RSAC 2026 named Gap 1 as unsolved."
    assert row["hook_type"] == "number"


def test_record_quiet_a_row_without_them_has_none_of_them(lm, tmp_path):
    _, row = _record(lm, tmp_path, {"impressions": 59, "members_reached": 20})
    for k in ("out_of_network_share", "hook", "hook_type", "media"):
        assert k not in row, f"{k} was filled in although nobody read it"


@pytest.mark.parametrize("bad", [150, -1, "35%", True])
def test_record_fires_an_impossible_share_is_refused_and_named(lm, tmp_path, capsys, bad):
    _, row = _record(lm, tmp_path, {"impressions": 1, "out_of_network_share": bad})
    assert "out_of_network_share" not in row
    assert "keine Zahl von 0 bis 100" in capsys.readouterr().out


def test_record_quiet_zero_and_hundred_are_shares(lm, tmp_path):
    _, row = _record(lm, tmp_path, {"impressions": 1, "out_of_network_share": 0})
    assert row["out_of_network_share"] == 0
    _, row = _record(lm, tmp_path, {"impressions": 1, "out_of_network_share": 100})
    assert row["out_of_network_share"] == 100


def test_record_fires_an_unknown_media_is_refused_and_named(lm, tmp_path, capsys):
    _, row = _record(lm, tmp_path, {"impressions": 1, "media": "carousel"})
    assert "media" not in row and "carousel" in capsys.readouterr().out


def test_record_quiet_link_preview_is_a_media(lm, tmp_path, capsys):
    _, row = _record(lm, tmp_path, {"impressions": 1, "media": "link_preview"})
    assert row["media"] == "link_preview"
    assert "media" not in capsys.readouterr().out


def test_record_fires_a_type_that_disagrees_with_the_rule_is_named(lm, tmp_path, capsys):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook": "In 2026 an agent tried.",
                                    "hook_type": "narrative"})
    assert row["hook_type"] == "narrative"
    assert "weicht von der Regel ab (number)" in capsys.readouterr().out


def test_record_quiet_a_type_that_agrees_is_not_named(lm, tmp_path, capsys):
    _record(lm, tmp_path, {"impressions": 1, "hook": "An agent tried.", "hook_type": "narrative"})
    assert "weicht" not in capsys.readouterr().out


def test_record_fires_an_unknown_type_is_refused(lm, tmp_path, capsys):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook_type": "question"})
    assert "hook_type" not in row and "question" in capsys.readouterr().out


# ── prompt and table ──

def test_prompt_fires_it_asks_for_the_share_and_the_media(lm, monkeypatch):
    monkeypatch.setattr(lm, "outstanding", lambda: [{"title": "t", "at": "2026-10-20T09:05:00+00:00"}])
    text = lm.prompt()
    assert "Anteil out-of-network" in text and "Bild oder Link-Vorschau" in text


def test_prompt_quiet_nothing_owed_asks_for_nothing(lm, monkeypatch):
    monkeypatch.setattr(lm, "outstanding", lambda: [])
    assert "out-of-network" not in lm.prompt()


def test_table_shows_the_new_columns(lm, tmp_path):
    _record(lm, tmp_path, {"impressions": 120, "members_reached": 80,
                           "out_of_network_share": 35, "hook": "An agent tried.",
                           "media": "link_preview"})
    t = lm.table()
    assert "OON%" in t and "Erz." in t and "Link" in t


# ── the comparison after five posts ──

def _post(n, kind, reached):
    return {"key": f"k{n}", "posted_at": f"2026-10-{10 + n:02d}T09:00:00+00:00",
            "hook_type": kind, "members_reached": reached, "impressions": reached * 3}


def test_compare_quiet_four_posts_are_not_enough(lm):
    rows = [_post(i, "narrative" if i % 2 else "number", 50) for i in range(4)]
    assert "noch nicht auswertbar: 4 von 5" in lm.compare_hooks(rows)


def test_compare_fires_a_clear_gap_is_named(lm):
    rows = [_post(0, "narrative", 90), _post(1, "narrative", 80), _post(2, "narrative", 100),
            _post(3, "number", 20), _post(4, "number", 30)]
    out = lm.compare_hooks(rows)
    assert "Urteil: Erzählung erreicht mehr" in out
    assert "Erzählung: 3 Posts, erreicht Median 90" in out


def test_compare_quiet_overlap_is_not_called_a_difference(lm):
    rows = [_post(0, "narrative", 90), _post(1, "narrative", 25), _post(2, "narrative", 100),
            _post(3, "number", 20), _post(4, "number", 30)]
    assert "nicht messbar verschieden" in lm.compare_hooks(rows)


def test_compare_quiet_one_type_only_is_not_comparable(lm):
    rows = [_post(i, "number", 10 * i) for i in range(5)]
    assert "nicht vergleichbar" in lm.compare_hooks(rows)


def test_compare_quiet_a_post_without_reach_does_not_count(lm):
    rows = [_post(i, "narrative", 50) for i in range(4)] + [
        {"key": "k9", "posted_at": "2026-10-30T09:00:00+00:00", "hook_type": "number",
         "pending": True}]
    assert "noch nicht auswertbar: 4 von 5" in lm.compare_hooks(rows)
