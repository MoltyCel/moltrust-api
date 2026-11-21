"""scripts/ai_sicht.py: fixed questions, both ways, verbatim storage, cost cap (ops AI-SICHT)."""
import datetime
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ai_sicht", ROOT / "scripts" / "ai_sicht.py")
ai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai)

ENV = {"ANTHROPIC_API_KEY": "x", "GEMINI_API_KEY": "x", "OPENAI_API_KEY": "x"}


def fake(text="Antwort", usd=None, tin=1000, tout=500, suchen=0):
    calls = []

    def f(model, frage, suche, key, http):
        calls.append((model, frage, suche))
        r = dict(text=text, version=model + "-v", quellen=["https://example.org/a"],
                 tin=tin, tout=tout, suchen=suchen if suche else 0)
        if usd is not None:
            r["usd"] = usd
        return r
    f.calls = calls
    return f


def test_fixed_order_and_both_ways():
    c, g = fake(), fake()
    rows = ai.befragen(["claude", "gemini"], ai.Budget(100), env=ENV, aufruf={"claude": c, "gemini": g}, schlaf=0)
    assert len(rows) == len(ai.FRAGEN) * 2 * 2
    # question by question, each first without and then with search, models in the given order
    assert [(r["frage_id"], r["weg"], r["modell"]) for r in rows[:4]] == [
        ("A1", "ohne_suche", "claude"), ("A1", "ohne_suche", "gemini"),
        ("A1", "mit_suche", "claude"), ("A1", "mit_suche", "gemini")]
    assert [x[2] for x in c.calls[:2]] == [False, True]
    assert rows[0]["text"] == "Antwort" and rows[0]["version"] == "claude-sonnet-5-5-v"
    assert {r["frage"] for r in rows} == {q for _, _, q in ai.FRAGEN}


def test_question_wording_is_fixed():
    # AR2: the wording is part of the measurement; a change needs a new id.
    assert [q for _, _, q in ai.FRAGEN] == [
        "Was ist MolTrust?", "Wer ist Lars Kroehl?", "Was ist der Agent Authorization Envelope?",
        "Wie prüfe ich, wozu ein Agent befugt war?",
        "Wie weise ich nach, dass ein Agent sein Mandat überschritten hat?",
        "Welche Standards gibt es für Agenten-Autorisierung?", "Wer haftet, wenn ein Agent falsch kauft?",
        "Welche Anbieter gibt es für Agenten-Identität und Nachweisbarkeit?", "ERC-8004 und Alternativen?"]


def test_cost_cap_stops_before_crossing():
    # Each call costs 0.04, within both reserves (0.05 without, 0.25 with search).
    # Cap 0.30: A1 without (0.04), A1 with (0.08), A2 without (0.12); A2 with
    # would need 0.12 + 0.25 > 0.30 and does not start, nor does anything after it.
    f = fake(usd=0.04)
    b = ai.Budget(0.30)
    rows = ai.befragen(["perplexity"], b, env={"PERPLEXITY_API_KEY": "x"},
                       aufruf={"perplexity": f}, schlaf=0)
    done = [r for r in rows if not r.get("fehler")]
    assert len(done) == 3
    assert b.summe <= b.deckel
    assert all("kostendeckel" in r["fehler"] for r in rows if r.get("fehler"))
    assert len(rows) == len(ai.FRAGEN) * 2


def test_cost_cap_silent_when_under():
    f = fake(usd=0.001)
    b = ai.Budget(5.0)
    rows = ai.befragen(["perplexity"], b, env={"PERPLEXITY_API_KEY": "x"}, aufruf={"perplexity": f}, schlaf=0)
    assert not any(r.get("fehler") for r in rows)
    assert b.summe < 0.1


def _status_error(code, body):
    req = httpx.Request("POST", "https://api.example/x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, text=body, request=req))


def test_no_credit_blocks_the_rest_of_the_run():
    n = []

    def broke(model, frage, suche, key, http):
        n.append(1)
        raise _status_error(429, '{"error":{"code":"insufficient_quota"}}')
    rows = ai.befragen(["chatgpt"], ai.Budget(5), env=ENV, aufruf={"chatgpt": broke}, schlaf=0)
    assert len(n) == 1
    assert rows[0]["fehler"].startswith("HTTP 429")
    assert all(r["fehler"].startswith("nicht angefragt") for r in rows[1:])


def test_rate_limit_without_quota_text_does_not_block():
    n = []

    def busy(model, frage, suche, key, http):
        n.append(1)
        raise _status_error(429, '{"error":"rate limited"}')
    ai.befragen(["chatgpt"], ai.Budget(5), env=ENV, aufruf={"chatgpt": busy}, schlaf=0)
    assert len(n) == len(ai.FRAGEN) * 2


def test_cost_from_list_prices():
    # Sonnet 5.5: 2 / 10 USD per MTok, search 10 USD per 1000
    assert ai.kosten("claude", 1_000_000, 100_000, 3) == pytest.approx(2.0 + 1.0 + 0.03)
    assert ai.kosten("perplexity", 1, 1, 1) is None


def test_keys_by_name_only():
    da, fehlt = ai.schluessel_stand({"ANTHROPIC_API_KEY": "s", "MISTRAL_API_KEY": "s"})
    assert da == ["claude", "mistral"]
    assert {"chatgpt", "gemini", "perplexity", "grok", "deepseek", "qwen"} <= set(fehlt)


def test_position_finds_name_and_stays_none_without():
    assert ai.position("Anbieter: Okta, Visa, MolTrust.") == pytest.approx(0.71, abs=0.01)
    assert ai.position("Anbieter: Okta, Visa, Skyfire.") is None
    assert ai.position("") is None


def test_judge_json_tolerates_prose_and_fills_keys():
    j = ai._judge_json('Here: {"genannt": false, "namen_davor": "Okta"}')
    assert j["genannt"] is False and j["namen_davor"] == [] and j["urteil"] is None
    with pytest.raises(ValueError):
        ai._judge_json("no json")


def test_sentence_per_model_counts_named_answers():
    bew = [{"modell": "claude", "weg": "mit_suche", "gruppe": "A", "genannt": True, "urteil": "richtig"},
           {"modell": "claude", "weg": "mit_suche", "gruppe": "A", "genannt": False, "urteil": "nicht_genannt"},
           {"modell": "claude", "weg": "mit_suche", "gruppe": "B", "genannt": False, "urteil": None}]
    s = ai.satz_je_modell(bew)[("claude", "mit_suche")]
    assert s == "claude (mit suche) nennt uns in A 1/2, B 0/1, C 0/2; Gruppe A: nicht_genannt 1, richtig 1."


def test_due_every_28_days(tmp_path):
    assert ai.faellig(tmp_path, datetime.date(2026, 11, 21))
    (tmp_path / "2026-11-21").mkdir()
    assert not ai.faellig(tmp_path, datetime.date(2026, 12, 18))
    assert ai.faellig(tmp_path, datetime.date(2026, 12, 19))


def test_facts_load_both_files():
    f = ai.fakten()
    assert "CHE-115.481.407" in f and "MANDATE / CONSTRAINTS / VALIDITY" in f


def test_no_telegram_in_script():
    src = (ROOT / "scripts" / "ai_sicht.py").read_text(encoding="utf-8")
    assert "notify" not in src and "api.telegram.org" not in src


def test_rows_are_json_serialisable(tmp_path):
    rows = ai.befragen(["claude"], ai.Budget(5), env=ENV, aufruf={"claude": fake()}, schlaf=0)
    ai._schreiben(tmp_path / "a.jsonl", rows)
    back = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert back[0]["frage_id"] == "A1" and back[0]["kanal"] == "api"
