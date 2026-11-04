"""CN5 (Lars, 03.11.2026): the radar drafts, Lars chooses in Telegram.

The drafter refuses for one reason only, that there is nothing checkable to say
about the post. Every other former SKIP code ends in a draft. The voice gate is
unchanged and still checks every text. At most three drafts a day, most views
first; the rest lapses without a message and is counted. The watch message
counts every reason of the last run, one number each.
"""
import datetime
import json
import logging
from pathlib import Path

import pytest

from agents import reply_radar as rr
from agents import voice_gate
from agents import watchdog as wd

UTC = datetime.timezone.utc
RUN_AT = datetime.datetime(2026, 11, 4, 6, 5, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _gate_rules(monkeypatch):
    """The voice gate's rules from the fixtures, as in tests/test_voice_gate.py:
    never from the network or the clone."""
    voice_gate._CACHE.clear()
    monkeypatch.setattr(voice_gate, "DOC_SCAN", FIXTURES / "pre-send-scan.md")
    monkeypatch.setattr(voice_gate, "DOC_ANTI_KI", FIXTURES / "anti-KI-Sprech.md")
    monkeypatch.setattr(voice_gate, "refresh_docs", lambda: None)
    yield
    voice_gate._CACHE.clear()


class _Resp:
    def __init__(self, body):
        self._body, self.status_code, self.text = body, 200, json.dumps(body)

    def json(self):
        return self._body


def _text(t):
    return {"content": [{"type": "text", "text": t}], "stop_reason": "end_turn",
            "usage": {"output_tokens": 40}}


@pytest.fixture
def drafter(monkeypatch, tmp_path):
    """The drafter with the network replaced; answers are served in order."""
    sent = []

    def install(*answers):
        queue = list(answers)

        def post(url, headers=None, json=None, timeout=None):
            sent.append(json)
            return _Resp(_text(queue.pop(0)))
        monkeypatch.setattr(rr.httpx, "post", post)

    monkeypatch.setattr(rr, "drafter_retired", lambda now=None: False)
    monkeypatch.setattr(rr, "load_anthropic_key", lambda: "k")
    monkeypatch.setattr(rr.voice_gate, "load_voice_docs",
                        lambda: {"anti_ki_sprech": "", "my_voice_en": ""})
    monkeypatch.setattr(rr, "citation_block", lambda: "")
    monkeypatch.setattr(rr, "kb_digest", lambda kb: "")
    monkeypatch.setattr(rr, "REFUSALS_FILE", str(tmp_path / "refusals.jsonl"))
    install.sent = sent
    return install


TWEET = {"id": "1", "_author": "a", "_source": "list", "text": "x"}
DRAFT = json.dumps({"reply": "ERC-8004 defines 3 registries.", "sources": []})


# ── criterion 2: one refusal code, every other one becomes a draft ──

def test_only_one_skip_code_is_left():
    assert rr.SKIP_ALLOWED == "no-checkable-claim"
    assert rr.SKIP_DROPPED == {"promotion", "price-talk", "no-source",
                               "off-subject", "repeat-figure", "other"}
    assert "SKIP: no-checkable-claim" in rr.SYSTEM_PROMPT
    # The prompt no longer offers the dropped codes as answers.
    assert "where <code> is one of" not in rr.SYSTEM_PROMPT
    assert "Product announcements and advertising stay SKIP" not in rr.VARIANT3_RULE


@pytest.mark.parametrize("code", sorted(rr.SKIP_DROPPED) + ["uncoded"])
def test_every_dropped_code_ends_in_a_draft(drafter, code):
    first = "SKIP" if code == "uncoded" else f"SKIP: {code} — not worth it"
    drafter(first, DRAFT)
    out = rr.draft_reply(TWEET, {})
    assert out == ("ERC-8004 defines 3 registries.", []), code
    assert rr._LAST_REFUSAL == {}
    # Asked once more, with the rule and the first answer in the user turn.
    assert len(drafter.sent) == 2
    second_user = drafter.sent[1]["messages"][0]["content"]
    assert "SKIP: no-checkable-claim" in second_user and first in second_user
    # Same system block, so the cache carries over.
    assert drafter.sent[0]["system"] == drafter.sent[1]["system"]


def test_nothing_checkable_is_the_one_refusal_and_costs_one_call(drafter):
    drafter("SKIP: no-checkable-claim — no figure in the post or the index")
    assert rr.draft_reply(TWEET, {}) is None
    assert rr._LAST_REFUSAL["kind"] == "no-checkable-claim"
    assert len(drafter.sent) == 1
    assert rr._refusal_is_final("no-checkable-claim: x")


def test_a_reask_may_still_end_in_the_one_allowed_refusal(drafter):
    drafter("SKIP: promotion — launch post", "SKIP: no-checkable-claim — nothing")
    assert rr.draft_reply(TWEET, {}) is None
    assert rr._LAST_REFUSAL["kind"] == "no-checkable-claim"


# ── criterion 3: the voice gate is unchanged and still blocks ──

def test_the_voice_gate_still_rejects_a_text_without_a_figure():
    scan = voice_gate.scan(["no figures here at all, just an opinion"], mode="reply",
                           refresh=False)
    assert scan["violations"], "the gate let a reply without a figure through"
    ok, problems, _ = rr.check("no figures here at all, just an opinion", {})
    assert not ok and problems


# ── criterion 6 and 4: three a day, most views first, the rest counted ──

LONG = "agents need a verifiable credential before they move money for anyone"


def _candidate(i, views):
    return {"id": str(100 + i), "author_id": f"u{i}", "lang": "en", "_source": "list",
            "_author": f"author{i}", "_author_followers": 900,
            "created_at": "2026-11-04T05:00:00Z", "text": f"{LONG} — {i}",
            "public_metrics": {"impression_count": views, "like_count": 0}}


VIEWS = [40, 900, 15, 3000, 220, 75, 5, 1200, 60, 330, 8]   # eleven candidates


@pytest.fixture
def eleven(monkeypatch, tmp_path):
    asked, messages = [], []
    monkeypatch.setattr(rr, "_utcnow", lambda: RUN_AT)
    monkeypatch.setattr(rr, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(rr, "HEARTBEAT_FILE", str(tmp_path / "hb.json"))
    monkeypatch.setattr(rr, "x_auth", lambda: "auth")
    monkeypatch.setattr(rr.x_meter, "reads_paused", lambda *a, **k: None)
    monkeypatch.setattr(rr, "load_targets", lambda: {
        f"author{i}": {"tier": 2, "group": "builders"} for i in range(11)})
    monkeypatch.setattr(rr, "gather", lambda *a, **k: [
        _candidate(i, v) for i, v in enumerate(VIEWS)])
    monkeypatch.setattr(rr, "load_kb", lambda: {})
    texts = ["ERC-8004 keeps its registry on chain.",
             "The A2A agent card is served from .well-known.",
             "A W3C status list answers revocation without a call home."]

    def fake_draft(tweet, kb, avoid=None):
        asked.append(tweet["id"])
        return (texts[len(asked) - 1], [])
    monkeypatch.setattr(rr, "draft_reply", fake_draft)
    monkeypatch.setattr(rr, "fetch_sources", lambda urls: {})
    monkeypatch.setattr(rr.notify, "send_telegram", lambda *a, **k: True)
    monkeypatch.setattr(rr.notify, "chat_id_for", lambda ch: 7)
    monkeypatch.setattr(rr.notify, "send_telegram_message",
                        lambda body, **kw: messages.append(body) or 4242)
    monkeypatch.setattr(rr.reply_decisions, "register", lambda *a, **k: None)
    return asked, messages, tmp_path


def test_eleven_candidates_three_drafts_most_views_first_eight_lapse(eleven, monkeypatch,
                                                                     caplog):
    asked, messages, tmp_path = eleven
    monkeypatch.setattr(rr, "check", lambda t, s: (True, [], {}))
    with caplog.at_level(logging.INFO, logger="reply_radar"):
        rr.run()
    assert rr.DAILY_MAX == 3
    # The three most-viewed: 3000, 1200, 900.
    assert asked == ["103", "107", "101"]
    assert len(messages) == 3, "a lapsed candidate reached Telegram"
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["per_day"]["2026-11-04"] == 3
    assert len(state["seen"]) == 11, "the lapsed candidates must not come back"

    # The watch message counts the eight as their own reason.
    lines = [f"[2026-11-04T06:05:0{min(i, 9)}] INFO: {m}"
             for i, m in enumerate(caplog.messages)]
    s = wd.radar_silence(RUN_AT + datetime.timedelta(minutes=5), lines)
    assert s["reasons"] == {"daily-cap": 8}
    assert s["drafts"] == 3 and s["candidates"] == 11
    assert "daily-cap 8" in s["run_summary"]
    assert any(m == "Reasons: 11 candidates, 3 drafts · daily-cap 8"
               for m in caplog.messages)


def test_a_voice_gate_block_is_still_a_draft_and_its_own_reason(eleven, monkeypatch,
                                                               caplog):
    asked, messages, _ = eleven
    # Real gate on the first text only; the real scan rejects it (no source
    # for its figure), the other two pass by stub.
    real = rr.check
    monkeypatch.setattr(rr, "check", lambda t, s: real(t, s) if t.startswith("ERC")
                        else (True, [], {}))
    with caplog.at_level(logging.INFO, logger="reply_radar"):
        rr.run()
    assert len(messages) == 3
    assert "BLOCKIERT" in messages[0]
    lines = [f"[2026-11-04T06:05:01] INFO: {m}" for m in caplog.messages]
    s = wd.radar_silence(RUN_AT + datetime.timedelta(minutes=5), lines)
    assert s["reasons"] == {"daily-cap": 8, "voice-gate": 1}


# ── criterion 4: the watch message, against the log of a real run ──

# logs/reply_radar.log, run of 2026-10-30 06:05 (server, lines 1976-1990),
# reasons shortened after the code. 11 candidates, 11 refusals, 0 drafts.
REAL_RUN_2026_10_30 = [
    "[2026-10-30T06:05:01] INFO: REPLY RADAR — 2026-10-30 06:05 UTC",
    "[2026-10-30T06:05:01] INFO: Drafted today: 0/2 — room for 2 this run",
    "[2026-10-30T06:05:02] INFO: Candidates after filtering: 11 (by tier: {2: 1, 3: 10})",
    "[2026-10-30T06:05:07] INFO:   skip 2115995079462760456 (@peckshieldalert) — nothing factual to say [off-subject: SKIP: off-subject — Fund-tracing alert]",
    "[2026-10-30T06:05:13] INFO:   skip 2115983936195297639 (@emollick) — nothing factual to say [off-subject: SKIP: off-subject — reply quality]",
    "[2026-10-30T06:05:15] INFO:   skip 2115981377652822479 (@emollick) — nothing factual to say [off-subject: SKIP: off-subject — A demo game post]",
    "[2026-10-30T06:05:17] INFO:   skip 2115996033326969135 (@nousresearch) — nothing factual to say [no-checkable-claim: SKIP: no-checkable-claim — Giveaway promo]",
    "[2026-10-30T06:05:21] INFO:   skip 2115984902562763062 (@emollick) — nothing factual to say [no-checkable-claim: SKIP: no-checkable-claim — platform reach]",
    "[2026-10-30T06:05:24] INFO:   skip 2115987056954417328 (@emollick) — nothing factual to say [no-checkable-claim: SKIP: no-checkable-claim — reply quality]",
    "[2026-10-30T06:05:26] INFO:   skip 2115984280157593977 (@emollick) — nothing factual to say [no-checkable-claim: SKIP: no-checkable-claim — comment quality]",
    "[2026-10-30T06:05:29] INFO:   skip 2115948545035432199 (@e2b) — nothing factual to say [promotion: SKIP: promotion — Product marketing video]",
    "[2026-10-30T06:05:32] INFO:   skip 2116045001570533743 (@letta_ai) — nothing factual to say [off-subject: SKIP: off-subject — memory-benchmark]",
    "[2026-10-30T06:05:35] INFO:   skip 2116045003814580351 (@letta_ai) — nothing factual to say [off-subject: SKIP: off-subject — Benchmark ranking]",
    "[2026-10-30T06:05:37] INFO:   skip 2116045005869686854 (@letta_ai) — nothing factual to say [no-checkable-claim: SKIP: no-checkable-claim — bare link]",
    "[2026-10-30T06:05:37] INFO: Done: 0 drafts this run, 0 through the gates · prompt v3-2026-10-11",
]


def test_the_watch_message_counts_each_reason_of_a_real_run():
    now = datetime.datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    s = wd.radar_silence(now, REAL_RUN_2026_10_30, max_hours=36)
    assert s["reasons"] == {"off-subject": 5, "no-checkable-claim": 5, "promotion": 1}
    assert "alle verworfen — 11 Kandidaten, 11 abgelehnt" in s["reason"]
    assert ("Gründe: no-checkable-claim 5 · off-subject 5 · promotion 1"
            in s["reason"])


def test_the_reasons_reset_with_each_run_header():
    later = REAL_RUN_2026_10_30 + [
        "[2026-10-31T06:05:01] INFO: REPLY RADAR — 2026-10-31 06:05 UTC",
        "[2026-10-31T06:05:02] INFO: Candidates after filtering: 1",
        "[2026-10-31T06:05:05] INFO:   draft 1 for 9 (@x, tier 2, list): PASS",
    ]
    s = wd.radar_silence(datetime.datetime(2026, 10, 31, 7, 0, tzinfo=UTC), later, 36)
    assert s["reasons"] == {} and s["drafts"] == 1
    assert "Gründe: keine" in s["run_summary"]


def test_the_ok_detail_carries_the_counts_even_with_drafts(monkeypatch):
    monkeypatch.setattr(wd, "_radar_tail", lambda *a, **k: [
        "[2026-11-04T06:05:01] INFO: REPLY RADAR — 2026-11-04 06:05 UTC",
        "[2026-11-04T06:05:02] INFO: Candidates after filtering: 5",
        "[2026-11-04T06:05:05] INFO:   draft 1 for 9 (@x, tier 2, list): PASS",
        "[2026-11-04T06:05:06] INFO:   skip 10 (@y) — nothing factual to say [no-checkable-claim: SKIP: x]",
        "[2026-11-04T06:05:07] INFO:   skip 11 — @x already has a draft this run (max 1)",
        "[2026-11-04T06:05:08] INFO:   lapse 12 (@z, 4 views) — daily-cap 3",
    ])
    out = wd.check_radar_deadman(RUN_AT + datetime.timedelta(hours=1),
                                 crontab="5 6 * * * cd x && python agents/reply_radar.py\n")
    assert out["ok"] is True
    assert ("letzter Lauf: 5 Kandidaten, 1 Entwürfe · Gründe: author-cap 1 · "
            "daily-cap 1 · no-checkable-claim 1") in out["detail"]


def test_a_line_that_is_not_a_reason_is_not_counted():
    """The silence probe: the run's own lines must not be read as reasons."""
    for msg in ("REPLY RADAR — 2026-11-04 06:05 UTC",
                "Drafted today: 0/3 — room for 3 this run",
                "  draft 1 for 9 (@x, tier 2, list): PASS",
                "  drafter said SKIP (promotion) — not a reason since CN5, asked once more",
                "Reasons: 11 candidates, 3 drafts · daily-cap 8",
                "  tokens in=1 out=2 cache_read=0 cache_write=0 stop=end_turn"):
        assert wd.radar_reason_of(msg) is None, msg
