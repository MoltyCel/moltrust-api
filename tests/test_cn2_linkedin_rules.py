"""CN2: LinkedIn drafts get their own rules, separate from X.

Herkunft: Lars, Chat 03.11.2026 (ops CN2); word limit for the drafter prompts
decided 09.11.2026 (moltrust-security#1 issuecomment-6752137172).

  K2  own rule set for LinkedIn only; agents/voice_gate.py (the X rules) is
      not touched by it
  K3  rule 1 (no protocol, field or code name) against config/linkedin_names.txt
      and the shape of a code identifier; rule 5 (at most 120 words) by a
      count; rules 2-4 are in every LinkedIn drafter prompt
  K4  a draft that breaks a rule does not reach Lars, on every path, and the
      watch message counts the refusal per rule
"""
import datetime
import json
import pathlib

import pytest

from agents import linkedin_feedback as lf
from agents import linkedin_post, linkedin_rules as lr, syndicate, voice_gate
from agents import watchdog as wd

UTC = datetime.timezone.utc
NOW = datetime.datetime.now(UTC)
REPO = pathlib.Path(__file__).resolve().parents[1]

GOOD = ("Visa said on Monday that agents may now pay for travel bookings.\n\n"
        "A bank that cannot see who allowed a payment carries the loss.\n\n"
        "https://moltrust.ch/blog/a.html")


def rfc822(d: datetime.datetime) -> str:
    return d.strftime("%a, %d %b %Y %H:%M:%S +0000")


def fresh_item():
    return {"title": "A", "link": "https://moltrust.ch/blog/a.html",
            "article_text": "x", "pub_date": rfc822(NOW - datetime.timedelta(days=1))}


def words(n: int) -> str:
    return " ".join(["word"] * (n - 1)) + " end.\n\nhttps://moltrust.ch/blog/a.html"


# ── K2 ──

def test_clean_text_passes_all_cn2_rules():
    assert lr.cn2_problems(GOOD) == []


def test_x_rules_are_not_the_linkedin_rules():
    # The X rule set does not know CN2; the LinkedIn set lives in its own file.
    src = (REPO / "agents" / "voice_gate.py").read_text()
    assert "CN2" not in src and "linkedin_names" not in src
    assert "voice_gate" not in (REPO / "agents" / "linkedin_rules.py").read_text().split('"""', 2)[2]


# ── K3, rule 1: names from the list ──

def test_name_list_lives_in_the_repo_and_is_read():
    names = lr.load_names()
    assert "AAE" in names and "x402" in names and "molcheck_record" in names
    assert not any(n.startswith("#") for n in names)


@pytest.mark.parametrize("name", ["AAE", "A2A", "MCP", "x402", "ERC-8004", "DIDs",
                                  "did:moltrust", "OAuth"])
def test_protocol_name_is_refused(name):
    probs = lr.name_problems(f"Visa now checks every {name} before it pays.")
    assert probs and probs[0].startswith("CN2-name") and name in probs[0]


def test_field_name_is_refused():
    assert lr.name_problems("Every record carries a revocation_reason now.")


@pytest.mark.parametrize("ident", ["agentDid", "verify()", "`grant`", "max_amount"])
def test_code_identifier_is_refused_by_its_shape(ident):
    assert lr.name_problems(f"Stripe added {ident} to its checkout.")


@pytest.mark.parametrize("text", [
    "Visa did not say who pays.",               # "did" is a verb, not DID
    "MolTrust and MolCheck are products.",      # brand names stay
    "A Mastercard rule added a fee.",           # "Mastercard" is not camelCase
    "See https://moltrust.ch/blog/aae_x402.html for more.",  # URL not prose
])
def test_plain_words_and_urls_pass(text):
    assert lr.name_problems(text) == []


def test_missing_name_list_raises(tmp_path):
    with pytest.raises(OSError):
        lr.load_names(str(tmp_path / "missing.txt"))
    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n")
    with pytest.raises(ValueError):
        lr.load_names(str(empty))


# ── K3, rule 5: the count ──

def test_120_words_pass():
    assert lr.word_count(words(120)) == 120
    assert lr.length_problems(words(120)) == []


def test_121_words_are_refused():
    probs = lr.length_problems(words(121))
    assert probs == ["CN2-words 121 Wörter, höchstens 120"]


# ── K3, rules 2-4: the drafter prompt ──

def test_every_linkedin_prompt_carries_the_rules(monkeypatch):
    item = {"title": "T", "link": "https://moltrust.ch/blog/a.html", "description": "d",
            "category": "Research"}
    regular = syndicate._thread_instructions(item, "low")
    revise = syndicate.LINKEDIN_REVISE.format(title="t", url="u", article="a", previous="p",
                                              feedback="f", rules=lr.drafter_rules())
    src = (REPO / "agents" / "syndicate.py").read_text()
    for prompt in (regular, revise):
        assert "At most 120 words" in prompt
        assert "first sentence names a person or an organisation" in prompt
        assert "one technical term" in prompt and "half explanation" in prompt
        assert "money, liability or time" in prompt
        assert "100-180" not in prompt
    assert src.count("linkedin_rules.drafter_rules()") == 3
    assert "100-180" not in src


# ── K4: refused, not sent, counted ──

@pytest.fixture
def tg(monkeypatch, tmp_path):
    seen = {"sent": []}

    def fake_send(text, **kw):
        seen["sent"].append({"text": text, **kw})
        return 6000 + len(seen["sent"])

    monkeypatch.setattr(syndicate.notify, "send_telegram_message", fake_send)
    monkeypatch.setattr(syndicate, "send_telegram",
                        lambda body, **kw: seen["sent"].append({"text": body, **kw}) or True)
    monkeypatch.setattr(syndicate.notify, "chat_id_for", lambda ch: "4711")
    monkeypatch.setattr(syndicate, "no_source_state_path", lambda: str(tmp_path / "nosrc.json"))
    monkeypatch.setattr(linkedin_post, "pending_path", lambda: str(tmp_path / "p.json"))
    monkeypatch.setattr(lr, "refusals_path", lambda: str(tmp_path / "refusals.jsonl"))
    monkeypatch.setattr(lf, "versions_path", lambda: str(tmp_path / "v.jsonl"))
    monkeypatch.setattr(voice_gate, "scan", lambda *a, **k: {"violations": []})
    monkeypatch.setattr(voice_gate, "format_report", lambda s: "PASS")
    monkeypatch.setattr(syndicate, "fetch_article_text", lambda url: "x")
    from app import ablage
    monkeypatch.setattr(ablage, "ablegen", lambda *a, **k: None)
    seen["refusals"] = tmp_path / "refusals.jsonl"
    return seen


def rows(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_name_violation_is_not_delivered(tg):
    bad = GOOD.replace("travel bookings", "travel bookings over x402")
    assert syndicate.deliver_linkedin(fresh_item(), bad) is False
    assert tg["sent"] == []
    assert linkedin_post.load_pending() == {}
    assert rows(tg["refusals"])[0]["rules"] == ["CN2-name"]


def test_word_violation_is_not_delivered(tg):
    assert syndicate.deliver_linkedin(fresh_item(), words(150)) is False
    assert tg["sent"] == []
    assert rows(tg["refusals"])[0]["rules"] == ["CN2-words"]


def test_clean_draft_is_still_delivered(tg):
    assert syndicate.deliver_linkedin(fresh_item(), GOOD) is True
    assert [m["art"] for m in tg["sent"]] == ["linkedin-entwurf"]
    assert not tg["refusals"].exists()


def test_unarmed_path_holds_back_a_violating_linkedin_text(tg):
    bad = GOOD + "\n\nThe AAE spec defines it."
    syndicate.hold_unarmed("neuer Post", fresh_item(), ["hook", "link"], bad, "test")
    msg = tg["sent"][0]["text"]
    assert "AAE spec" not in msg and "zurückgehalten" in msg and "CN2-name" in msg
    assert rows(tg["refusals"])[0]["where"] == "unarmed"


def test_unarmed_path_passes_a_clean_linkedin_text(tg):
    syndicate.hold_unarmed("neuer Post", fresh_item(), ["hook", "link"], GOOD, "test")
    assert "Visa said on Monday" in tg["sent"][0]["text"]


def test_redraft_that_breaks_a_rule_is_not_shown(tg, monkeypatch):
    linkedin_post.save_pending({"k1": {"text": GOOD, "title": "A",
                                       "source": "https://moltrust.ch/blog/a.html",
                                       "message_id": 10}})
    monkeypatch.setattr(syndicate, "redraft_linkedin",
                        lambda entry, fb: (words(200), "länger"))
    entry = linkedin_post.load_pending()["k1"]
    assert lf.redraft("k1", entry, "mehr Details", reply_to=10) == 2
    sent = tg["sent"][0]
    assert sent["art"] == "befehl-antwort" and "word word" not in sent["text"]
    assert "CN2-words" in sent["text"]
    p = linkedin_post.load_pending()["k1"]
    assert p["text"] == GOOD and p["versions"][0]["ok"] is False
    assert rows(tg["refusals"])[0]["where"] == "redraft"


def test_watch_message_counts_refusals_per_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "refusals_path", lambda: str(tmp_path / "r.jsonl"))
    now = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    lr.record_refusal(["CN2-name a", "CN2-name b", "CN2-words 130"], "deliver", now=now)
    lr.record_refusal(["CN2-words 140"], "deliver", now=now - datetime.timedelta(hours=1))
    lr.record_refusal(["CN2-name c"], "deliver", now=now - datetime.timedelta(hours=30))
    assert lr.refusal_counts(now) == {"CN2-name": 1, "CN2-words": 2}
    assert lr.refusal_line(now) == "LinkedIn CN2 24 h: CN2-name 1 · CN2-words 2"


def test_watch_message_is_quiet_without_refusals(tmp_path, monkeypatch):
    # Schweigeprobe: no file, and a file with only old rows, both count zero.
    monkeypatch.setattr(lr, "refusals_path", lambda: str(tmp_path / "r.jsonl"))
    now = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    assert lr.refusal_line(now) == "LinkedIn CN2 24 h: keine"
    lr.record_refusal(["CN2-words 140"], "deliver", now=now - datetime.timedelta(hours=25))
    assert lr.refusal_line(now) == "LinkedIn CN2 24 h: keine"


def test_radar_watch_detail_carries_the_linkedin_count(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "refusals_path", lambda: str(tmp_path / "r.jsonl"))
    now = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    lr.record_refusal(["CN2-words 140"], "deliver", now=now)
    monkeypatch.setattr(wd, "_radar_tail", lambda: [])
    out = wd.check_radar_deadman(now, crontab="")
    assert "LinkedIn CN2 24 h: CN2-words 1" in out["detail"]
