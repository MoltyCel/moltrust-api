"""LinkedIn series, ops LI1 (2026-10-15): out-of-network share, hook type, media.

The hook type is actor or claim, given by whoever records the post (CE1).
Whether the first sentence carries a date or an amount is a field of its own (CG3).

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


# ── the hook type is given, not derived (CE1) ──

def test_hook_type_fires_a_hook_without_a_type_is_named_not_guessed(lm, tmp_path, capsys):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook": "A columnist asked an AI agent."})
    assert row["hook"] == "A columnist asked an AI agent."
    assert "hook_type" not in row
    assert "hook_type fehlt" in capsys.readouterr().out


def test_hook_type_quiet_no_hook_no_note(lm, tmp_path, capsys):
    _record(lm, tmp_path, {"impressions": 1})
    assert "hook_type" not in capsys.readouterr().out


@pytest.mark.parametrize("old", ["narrative", "number"])
def test_hook_type_fires_the_digit_values_are_refused(lm, tmp_path, capsys, old):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook": "x.", "hook_type": old})
    assert "hook_type" not in row and old in capsys.readouterr().out


def test_hook_type_quiet_actor_and_claim_are_kept(lm, tmp_path):
    for t in ("actor", "claim"):
        _, row = _record(lm, tmp_path, {"impressions": 1, "hook": "x.", "hook_type": t})
        assert row["hook_type"] == t


def test_hook_type_a_digit_does_not_decide(lm, tmp_path):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook_type": "claim",
                                    "hook": "Five sources counted 3 things in 2026."})
    assert row["hook_type"] == "claim"


# ── date or amount in the first sentence, its own field (CG3) ──

def test_date_or_amount_fires_a_hook_without_it_is_named(lm, tmp_path, capsys):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook": "x.", "hook_type": "actor"})
    assert "hook_date_or_amount" not in row
    assert "hook_date_or_amount fehlt" in capsys.readouterr().out


def test_date_or_amount_quiet_no_hook_no_note(lm, tmp_path, capsys):
    _record(lm, tmp_path, {"impressions": 1})
    assert "hook_date_or_amount" not in capsys.readouterr().out


@pytest.mark.parametrize("v", [True, False])
def test_date_or_amount_quiet_true_and_false_are_kept(lm, tmp_path, capsys, v):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook": "x.", "hook_type": "claim",
                                    "hook_date_or_amount": v})
    assert row["hook_date_or_amount"] is v
    assert "hook_date_or_amount" not in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["ja", 1, None])
def test_date_or_amount_fires_anything_else_is_refused(lm, tmp_path, capsys, bad):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook_date_or_amount": bad})
    assert "hook_date_or_amount" not in row
    assert "nicht true oder false" in capsys.readouterr().out


def test_date_or_amount_does_not_change_the_type(lm, tmp_path):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook_type": "actor",
                                    "hook": "A columnist asked an AI agent to buy cheap eggs.",
                                    "hook_date_or_amount": False})
    assert row["hook_type"] == "actor" and row["hook_date_or_amount"] is False


# ── record(): the four fields ──

def test_record_fires_the_fields_are_kept(lm, tmp_path):
    code, row = _record(lm, tmp_path, {
        "impressions": 120, "members_reached": 80, "out_of_network_share": 35,
        "hook": "RSAC 2026 named Gap 1 as unsolved. Then we tested it.",
        "hook_type": "actor", "media": "image"})
    assert code == 0
    assert row["out_of_network_share"] == 35 and row["media"] == "image"
    assert row["hook"] == "RSAC 2026 named Gap 1 as unsolved."
    assert row["hook_type"] == "actor"


def test_record_quiet_a_row_without_them_has_none_of_them(lm, tmp_path):
    _, row = _record(lm, tmp_path, {"impressions": 59, "members_reached": 20})
    for k in ("out_of_network_share", "hook", "hook_type", "media", "hook_date_or_amount"):
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


def test_record_fires_an_unknown_type_is_refused(lm, tmp_path, capsys):
    _, row = _record(lm, tmp_path, {"impressions": 1, "hook_type": "question"})
    assert "hook_type" not in row and "question" in capsys.readouterr().out


# ── prompt and table ──

def test_prompt_fires_it_asks_for_the_share_and_the_media(lm, monkeypatch):
    monkeypatch.setattr(lm, "outstanding", lambda: [{"title": "t", "at": "2026-10-20T09:05:00+00:00"}])
    text = lm.prompt()
    assert "Anteil out-of-network" in text and "Bild oder Link-Vorschau" in text
    assert "actor (benennbare Person oder Organisation, die etwas getan hat) oder claim" in text
    assert "erster Satz mit Zeitpunkt oder Betrag: ja oder nein" in text


def test_prompt_quiet_nothing_owed_asks_for_nothing(lm, monkeypatch):
    monkeypatch.setattr(lm, "outstanding", lambda: [])
    assert "out-of-network" not in lm.prompt()


def test_table_shows_the_new_columns(lm, tmp_path):
    _record(lm, tmp_path, {"impressions": 120, "members_reached": 80,
                           "out_of_network_share": 35, "hook": "An agent tried.",
                           "hook_type": "actor", "media": "link_preview",
                           "hook_date_or_amount": True})
    t = lm.table()
    assert "OON%" in t and "Akt." in t and "Link" in t
    assert "Z/B" in t and "  ja  " in t


# ── the comparison after five posts ──

def _post(n, kind, reached):
    return {"key": f"k{n}", "posted_at": f"2026-10-{10 + n:02d}T09:00:00+00:00",
            "hook_type": kind, "members_reached": reached, "impressions": reached * 3}


def test_compare_quiet_four_posts_are_not_enough(lm):
    rows = [_post(i, "actor" if i % 2 else "claim", 50) for i in range(4)]
    assert "noch nicht auswertbar: 4 von 5" in lm.compare_hooks(rows)


def test_compare_fires_a_clear_gap_is_named(lm):
    rows = [_post(0, "actor", 368), _post(1, "actor", 143),
            _post(2, "claim", 50), _post(3, "claim", 48), _post(4, "claim", 60)]
    out = lm.compare_hooks(rows)
    assert "Urteil: Akteur erreicht mehr" in out
    assert "Akteur: 2 Posts, erreicht Median 255.5" in out


def test_compare_quiet_overlap_is_not_called_a_difference(lm):
    rows = [_post(0, "actor", 90), _post(1, "actor", 25), _post(2, "actor", 100),
            _post(3, "claim", 20), _post(4, "claim", 30)]
    assert "nicht messbar verschieden" in lm.compare_hooks(rows)


def test_compare_quiet_one_type_only_is_not_comparable(lm):
    rows = [_post(i, "claim", 10 * i) for i in range(5)]
    assert "nicht vergleichbar" in lm.compare_hooks(rows)


def test_compare_quiet_a_post_without_reach_does_not_count(lm):
    rows = [_post(i, "actor", 50) for i in range(4)] + [
        {"key": "k9", "posted_at": "2026-10-30T09:00:00+00:00", "hook_type": "claim",
         "pending": True}]
    assert "noch nicht auswertbar: 4 von 5" in lm.compare_hooks(rows)
