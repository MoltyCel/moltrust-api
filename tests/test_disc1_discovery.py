"""ops DISC-1: the discovery run tells Lars only about threads he has to act on.

Input of the job: the run of 31.10.2026 06:01Z sent "11 new, 10 pruned, 145
total" to Telegram, with five of eleven threads listed, all of them
coordination channels, backlogs or digests. The fixture holds those eleven
(repo, number, title, labels, comment count as read from the GitHub API on
31.10.2026), and every one has to be dropped with a reason.
"""
import json
import pathlib
import sys
import types

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "scripts"))
sys.path.insert(0, str(WURZEL))

import discovery as D  # noqa: E402

FIXTURE = json.loads((WURZEL / "tests" / "fixtures" / "disc1_candidates_2026-10-31.json").read_text())


# ── Criterion 3: filter ─────────────────────────────────────────────────────

def test_fixture_is_the_eleven_of_31_october():
    assert len(FIXTURE) == 11
    assert {f"{c['repo']}#{c['number']}" for c in FIXTURE} >= {
        "pachaninm-lab/pachanin-demo#5469", "Contribuda/trustwork#137"}


@pytest.mark.parametrize("cand", FIXTURE, ids=lambda c: f"{c['repo']}#{c['number']}")
def test_every_candidate_of_31_october_is_dropped(cand):
    keep, reason = D.classify(cand)
    assert keep is False, reason
    assert "ohne Bezug zu Identität, Vertrauen oder Autorisierung" in reason


def test_ten_of_eleven_are_recognised_as_hubs():
    hubs = [c for c in FIXTURE if D.classify(c)[1].startswith("Koordinations-")]
    others = [f"{c['repo']}#{c['number']}" for c in FIXTURE if c not in hubs]
    assert others == ["TandemVector-Labs/ChatGPTRemoteCommander#211"]


def test_repo_name_does_not_count():
    """Contribuda/trustwork: 'trust' in the repo name says nothing about the thread."""
    c = {"repo": "x/trustwork", "number": 1, "title": "Agent safety evaluation across modules",
         "labels": [], "comments": 0}
    assert D.classify(c)[0] is False


@pytest.mark.parametrize("title", [
    "Agent identity: resolve did:web for tool callers",
    "Support verifiable credentials for agent onboarding",
    "x402 payment authorization for autonomous agents",
    "Delegation chains between A2A agents",
    "Trust score for MCP servers",
])
def test_on_topic_titles_are_kept(title):
    keep, reason = D.classify({"title": title, "labels": [], "comments": 3})
    assert keep is True
    assert reason.startswith("Titel/Label nennt ")


def test_hub_with_on_topic_title_is_kept():
    keep, _ = D.classify({"title": "Coordination: agent identity and DID method choice",
                          "labels": ["coordination"], "comments": 12})
    assert keep is True


def test_label_alone_can_make_a_hub():
    keep, reason = D.classify({"title": "Platform items 2026-05", "labels": ["roadmap"], "comments": 2})
    assert keep is False and "roadmap" in reason


def test_comment_count_alone_makes_a_hub():
    keep, reason = D.classify({"title": "Daily notes", "labels": [], "comments": 1200})
    assert keep is False and "1200 Kommentare" in reason


@pytest.mark.parametrize("title", [
    "Release checklist without identity changes",
    "Refactor: no authorization logic touched",
    "Kein Vertrauen nötig: keine trust Schicht",
])
def test_negated_term_is_no_relation(title):
    """Negativfall: a negated term must not keep the thread."""
    assert D.classify({"title": title, "labels": [], "comments": 0})[0] is False


# ── Criterion 4: message format ─────────────────────────────────────────────

def _kept(url, empf, entwurf=""):
    return {"url": url, "repo": "o/r", "number": 1, "title": "t",
            "text": {"satz": f"Satz zu {url}.", "empfehlung": empf, "entwurf": entwurf, "quelle": "test"}}


def test_message_per_candidate_link_sentence_one_recommendation():
    msg = D.build_message([
        _kept("https://github.com/a/b/issues/1", "antworten", "Draft body."),
        _kept("https://github.com/c/d/issues/2", "lesen"),
    ], "2026-11-01")
    blocks = msg.split("\n\n")[1:]
    assert len(blocks) == 2
    for b in blocks:
        assert b.count("Empfehlung:") == 1
        assert "https://github.com/" in b and "Satz zu" in b
    assert "Entwurf:\nDraft body." in blocks[0]
    assert "Entwurf" not in blocks[1]


def test_message_has_no_counters():
    msg = D.build_message([_kept("https://github.com/a/b/issues/1", "lesen")], "2026-11-01")
    for word in ("new,", "pruned", "total", "Kandidaten", "discovery_candidates"):
        assert word not in msg
    assert not msg.startswith(D.notify.KEIN_HANDGRIFF)


def test_recommendation_nichts_is_left_out():
    msg = D.build_message([_kept("https://github.com/a/b/issues/1", "nichts"),
                           _kept("https://github.com/c/d/issues/2", "lesen")], "2026-11-01")
    assert "a/b/issues/1" not in msg and "c/d/issues/2" in msg


# ── Criterion 5: empty run ──────────────────────────────────────────────────

def test_empty_run_builds_no_message():
    assert D.build_message([], "2026-11-01") is None
    assert D.build_message([_kept("https://github.com/a/b/issues/1", "nichts")], "2026-11-01") is None


# ── describe(): model answer and fallback ───────────────────────────────────

class _Client:
    def __init__(self, text=None, exc=None):
        self._text, self._exc = text, exc
        self.messages = self

    def create(self, **kw):
        if self._exc:
            raise self._exc
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=self._text)])


ITEM = {"repo": "o/r", "number": 7, "title": "Agent identity for tool calls", "url": "https://github.com/o/r/issues/7",
        "labels": [], "comments": 1, "body": "..."}


def test_describe_without_client_falls_back_to_lesen():
    x = D.describe(ITEM, None)
    assert x["empfehlung"] == "lesen" and x["entwurf"] == "" and "o/r" in x["satz"]


def test_describe_model_error_falls_back():
    assert D.describe(ITEM, _Client(exc=RuntimeError("down")))["empfehlung"] == "lesen"


def test_describe_antworten_without_draft_becomes_lesen():
    x = D.describe(ITEM, _Client('{"satz": "Dort geht es um X.", "empfehlung": "antworten", "entwurf": ""}'))
    assert x["empfehlung"] == "lesen"


def test_describe_draft_only_with_antworten():
    x = D.describe(ITEM, _Client('{"satz": "Dort geht es um X.", "empfehlung": "lesen", "entwurf": "text"}'))
    assert x["entwurf"] == ""
    y = D.describe(ITEM, _Client('{"satz": "Dort geht es um X.", "empfehlung": "antworten", "entwurf": "Reply."}'))
    assert (y["empfehlung"], y["entwurf"]) == ("antworten", "Reply.")


def test_describe_unknown_recommendation_falls_back():
    assert D.describe(ITEM, _Client('{"satz": "s", "empfehlung": "posten"}'))["empfehlung"] == "lesen"


# ── Run: criterion 2 and 5 end to end, without network ─────────────────────

@pytest.fixture
def run_env(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "CANDIDATES_FILE", tmp_path / "cands.json")
    monkeypatch.setattr(D, "HEALTH_FILE", tmp_path / "health.json")
    monkeypatch.setattr(D, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(D, "LOG_FILE", tmp_path / "logs" / "discovery.log")
    monkeypatch.setattr(D.gh, "token", lambda: "t")
    monkeypatch.setattr(D, "token_is_live", lambda tok: (True, "HTTP 200"))
    sent = []
    monkeypatch.setattr(D.notify, "send_telegram", lambda text, **kw: sent.append((text, kw)) or True)
    return tmp_path, sent


def _as_found(c):
    return {**c, "url": c["url"], "query": "q", "body": ""}


def test_run_with_the_eleven_sends_nothing_and_keeps_them_in_the_file(run_env, monkeypatch):
    tmp, sent = run_env
    monkeypatch.setattr(D, "discover_threads", lambda tok: [_as_found(c) for c in FIXTURE])
    assert D.main(["--no-model"]) == 0
    assert sent == []
    cands = json.loads((tmp / "cands.json").read_text())["candidates"]
    assert len(cands) == 11
    assert all(c["filter"] == "drop" and c["filter_reason"] for c in cands)
    assert "11 new, 0 kept" in (tmp / "logs" / "discovery.log").read_text()


def test_run_with_one_on_topic_thread_sends_one_message(run_env, monkeypatch):
    tmp, sent = run_env
    found = [_as_found(c) for c in FIXTURE] + [{**ITEM, "query": "q"}]
    monkeypatch.setattr(D, "discover_threads", lambda tok: found)
    assert D.main(["--no-model"]) == 0
    assert len(sent) == 1
    text, kw = sent[0]
    assert kw["channel"] == D.notify.WORKLOG
    assert ITEM["url"] in text and text.count("Empfehlung:") == 1
    for c in FIXTURE:
        assert c["url"] not in text
    for word in ("new,", "pruned", "total"):
        assert word not in text


def test_empty_run_sends_nothing(run_env, monkeypatch):
    tmp, sent = run_env
    monkeypatch.setattr(D, "discover_threads", lambda tok: [])
    assert D.main(["--no-model"]) == 0
    assert sent == []


def test_dry_run_writes_nothing_and_sends_nothing(run_env, monkeypatch, capsys):
    tmp, sent = run_env
    monkeypatch.setattr(D, "discover_threads", lambda tok: [{**ITEM, "query": "q"}])
    assert D.main(["--dry-run", "--no-model"]) == 0
    assert sent == []
    assert not (tmp / "cands.json").exists() and not (tmp / "health.json").exists()
    assert ITEM["url"] in capsys.readouterr().out


def test_recovery_after_failures_is_not_sent(run_env, monkeypatch):
    tmp, sent = run_env
    (tmp / "health.json").write_text(json.dumps({"consecutive_failures": 5, "last_ok": "2026-10-20"}))
    monkeypatch.setattr(D, "discover_threads", lambda tok: [])
    assert D.main(["--no-model"]) == 0
    assert sent == []


def test_third_failure_alerts_with_a_step(run_env, monkeypatch):
    tmp, sent = run_env
    (tmp / "health.json").write_text(json.dumps({"consecutive_failures": 2, "last_ok": "2026-10-28"}))
    monkeypatch.setattr(D, "token_is_live", lambda tok: (False, "credential rejected"))
    assert D.main([]) == 1
    assert len(sent) == 1 and "Handgriff" in sent[0][0]
