"""The Agent-Card check compares skill ids with the repo card (ops WD-1027).

Until 2026-10-27 the watchdog compared the number of skills on the live card
with EXPECTED_AGENT_CARD_SKILLS = 15. leak-canary (#905, 2026-10-22) made the
card 16, nobody bumped the number, and the check was red for days while the
live card matched the repo exactly. The source of the card is
.well-known/agent-card.json in this repo, so that file is the expectation.
"""
import copy
import json
from pathlib import Path

from agents import watchdog as wd

REPO = Path(__file__).resolve().parents[1]


def _card(*ids):
    return {"name": "MolTrust", "skills": [{"id": i, "name": i} for i in ids]}


def test_same_ids_are_green():
    v = wd.agent_card_skill_verdict(_card("a", "b", "c"), _card("a", "b", "c"))
    assert v["ok"] is True
    assert v["surface"] == "Agent-Card"
    assert "3 skills" in v["detail"]


def test_order_does_not_matter():
    assert wd.agent_card_skill_verdict(_card("c", "a", "b"), _card("a", "b", "c"))["ok"]


def test_an_id_missing_live_is_red_and_named():
    v = wd.agent_card_skill_verdict(_card("a", "b"), _card("a", "b", "leak-canary"))
    assert v["ok"] is False
    assert "missing live: leak-canary" in v["detail"]
    assert "not in the repo" not in v["detail"]
    assert v["advice"]


def test_an_extra_id_live_is_red_and_named():
    v = wd.agent_card_skill_verdict(_card("a", "b", "hand-edit"), _card("a", "b"))
    assert v["ok"] is False
    assert "live but not in the repo: hand-edit" in v["detail"]
    assert "missing live" not in v["detail"]


def test_both_directions_at_once_name_both():
    v = wd.agent_card_skill_verdict(_card("a", "x"), _card("a", "y"))
    assert v["ok"] is False
    assert "missing live: y" in v["detail"]
    assert "live but not in the repo: x" in v["detail"]


def test_a_card_without_a_skill_list_is_red():
    assert wd.agent_card_skill_verdict({"name": "x"}, _card("a"))["ok"] is False
    assert wd.agent_card_skill_verdict(_card("a"), {"skills": "a"})["ok"] is False
    assert wd.agent_card_skill_verdict({"skills": [{"name": "no id"}]}, _card("a"))["ok"] is False


def test_the_repo_card_against_itself_is_green():
    """Silence check: the card as it is deployed raises nothing."""
    card = json.loads((REPO / ".well-known" / "agent-card.json").read_text())
    assert Path(wd.AGENT_CARD_SOURCE) == REPO / ".well-known" / "agent-card.json"
    v = wd.agent_card_skill_verdict(copy.deepcopy(card), card)
    assert v["ok"] is True, v


def test_the_card_of_2026_10_27_against_the_old_baseline():
    """The state that alarmed: 16 ids live, 16 in the repo. Same ids, so green."""
    card = json.loads((REPO / ".well-known" / "agent-card.json").read_text())
    ids = [s["id"] for s in card["skills"]]
    assert "leak-canary" in ids and "ads-verdict-gate" in ids
    assert wd.agent_card_skill_verdict(card, card)["ok"] is True
    assert not hasattr(wd, "EXPECTED_AGENT_CARD_SKILLS")


def test_check_discovery_drift_reads_the_repo_card(monkeypatch, tmp_path):
    source = tmp_path / "agent-card.json"
    source.write_text(json.dumps(_card("a", "b")))
    monkeypatch.setattr(wd, "AGENT_CARD_SOURCE", str(source))

    class Resp:
        def json(self):
            return _card("a")

    monkeypatch.setattr(wd.httpx, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(wd, "_live_mcp_tool_count", lambda: None)
    monkeypatch.setattr(wd, "_check_x402_discovery",
                        lambda: {"surface": "x402-discovery", "ok": True, "detail": "ok"})
    monkeypatch.setattr(wd, "_is_weekly_slot", lambda now, day: False)
    import datetime
    out = wd.check_discovery_drift(datetime.datetime(2026, 10, 27, 9, tzinfo=datetime.timezone.utc))
    card = [r for r in out if r["surface"] == "Agent-Card"]
    assert len(card) == 1 and card[0]["ok"] is False
    assert "missing live: b" in card[0]["detail"]
