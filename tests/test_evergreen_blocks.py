"""Evergreen after 2026-10-15: a post the gate blocks does not hold up the path.

From 2026-10-06 to 2026-10-15 every scheduled run drafted the same post, and
every hook failed g2f. Each criterion below has a test that fires and a test
that stays quiet.
"""
import datetime
import json
import logging

import pytest

from agents import syndicate as sy
from scripts import evergreen_verify as ev


A = "https://moltrust.ch/blog/a.html"
B = "https://moltrust.ch/blog/b.html"
C = "https://moltrust.ch/blog/c.html"
NOW = datetime.datetime(2026, 10, 15, 9, 0, tzinfo=datetime.timezone.utc)

BLOCK_REPORT = ("Pre-send scan [thread]: PASS\n"
                "  Gate 2 (post): 7/7 pass\n\n"
                "--- hook, rule (h) ---\n"
                "Pre-send scan [reply]: BLOCKED\n"
                "  Gate 2 (post): 7/8 pass — failing: g2f\n"
                "  ! g2f Substanz-Boden — no concrete number anywhere\n")


def item(link):
    return {"link": link, "title": link.rsplit("/", 1)[-1], "guid": link,
            "category": "Analysis", "description": "d",
            "pub_date": "Sun, 13 Sep 2026 00:00:00 GMT"}


FEED = [item(C), item(B), item(A)]          # newest first; A is the oldest


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sy, "SECRETS_FILE", str(tmp_path / "no-secrets"))
    monkeypatch.setenv("SYNDICATE_ARMED", "1")
    state = tmp_path / "syndicate_state.json"
    state.write_text(json.dumps({"seen": [i["guid"] for i in FEED]}))
    monkeypatch.setattr(sy, "STATE_FILE", str(state))
    monkeypatch.setattr(sy, "COUNTER_FILE", str(tmp_path / "syndicate_counter.json"))
    monkeypatch.setattr(sy, "REGISTER_FILE", str(tmp_path / "syndicated.json"))
    monkeypatch.setattr(sy, "HEARTBEAT_FILE", str(tmp_path / "hb.json"))
    # raising=False so that, run against the code before 2026-10-15, the tests
    # fail on what they check and not on a missing name in the fixture.
    monkeypatch.setattr(sy, "EVERGREEN_HEARTBEAT_FILE", str(tmp_path / "hb_evergreen.json"),
                        raising=False)
    monkeypatch.setattr(sy, "PRIORITY_FILE", str(tmp_path / "no-priority.json"))
    monkeypatch.setattr(sy.x_meter, "reads_paused", lambda now=None: None)
    monkeypatch.setattr(sy, "_RUN_POSTS", 0)
    monkeypatch.setattr(sy, "fetch_feed", lambda: [dict(i) for i in FEED])
    monkeypatch.setattr(sy, "fetch_article_text", lambda u, limit=6000: "42 things")
    monkeypatch.setattr(sy, "bluesky_mirror", lambda parts: [])
    monkeypatch.setattr(sy, "deliver_linkedin", lambda *a, **k: True)
    monkeypatch.setattr(sy, "record_linkedin_draft", lambda *a, **k: None)


@pytest.fixture
def world(monkeypatch):
    """Which links the gate blocks, what was drafted, posted and sent."""
    w = {"blocked": set(), "drafted": [], "posted": [], "sent": []}
    monkeypatch.setattr(sy, "draft_teaser", lambda i: w["drafted"].append(i["link"]) or
                        {"parts": [f"hook {i['link']}", f"link {i['link']}"], "linkedin": ""})
    monkeypatch.setattr(sy, "check_teaser", lambda parts, i:
                        (False, BLOCK_REPORT) if i["link"] in w["blocked"] else (True, "PASS"))
    monkeypatch.setattr(sy.x_post, "post_thread", lambda parts, kind=None:
                        w["posted"].append(parts[1].split()[-1]) or ["11"])
    monkeypatch.setattr(sy, "send_telegram", lambda m, **k: w["sent"].append(m) or True)
    return w


def register():
    try:
        return json.load(open(sy.REGISTER_FILE))
    except FileNotFoundError:
        return {}


# ── CA1: g2f is unchanged and still runs on the hook alone ──
#
# Against the real gate, which reads its rules from the web docs mirror on the
# server and refuses to run without them. CI has no mirror; there the shape of
# the two scans is held by test_evergreen.py::test_both_scans_have_to_pass.

needs_rules = pytest.mark.skipif(not sy.voice_gate.DOC_SCAN.exists(),
                                 reason="gate rules not mirrored on this machine")


@needs_rules
def test_ca1_fires_a_hook_without_a_digit_is_blocked_by_g2f():
    art = "RSAC 2026 said Gap 1 was unsolved across all vendors."
    hook = "An agent tried to write to /etc/ and the kernel saw it happen."
    ok, report = sy.check_teaser([hook, "Read it: https://moltrust.ch/blog/x.html"],
                                 {"link": "https://moltrust.ch/blog/x.html",
                                  "article_text": art})
    assert not ok
    assert "hook: g2f" in sy.gate_reasons(report)


@needs_rules
def test_ca1_quiet_a_hook_with_a_sourced_number_passes_g2f():
    art = "RSAC 2026 said Gap 1 was unsolved across all vendors."
    hook = "RSAC 2026 said Gap 1 was unsolved across all vendors."
    _, report = sy.check_teaser([hook, "Read it: https://moltrust.ch/blog/x.html"],
                                {"link": "https://moltrust.ch/blog/x.html",
                                 "article_text": art})
    assert "g2f" not in sy.gate_reasons(report)


# ── CA2: a blocked post does not hold up the path ──

def test_ca2_fires_the_run_moves_on_to_the_next_due_post(world):
    world["blocked"] = {A}
    assert sy.run_evergreen() == 0
    assert world["drafted"] == [A, B]
    assert world["posted"] == [B]
    assert register()[A]["evergreen_blocked"]["in_a_row"] == 1


def test_ca2_quiet_a_post_that_passes_is_the_only_draft(world):
    assert sy.run_evergreen() == 0
    assert world["drafted"] == [A] and world["posted"] == [A]
    assert "evergreen_blocked" not in register()[A]


def test_ca2_fires_three_blocks_in_a_row_take_the_post_out_and_say_so(world):
    world["blocked"] = {A}
    for _ in range(3):
        sy.run_evergreen()
    assert register()[A]["evergreen_blocked"]["in_a_row"] == 3
    assert A not in [i["link"] for i in sy.evergreen_candidates(FEED, register(), NOW)]
    assert sum("aus der Warteschlange" in m for m in world["sent"]) == 1


def test_ca2_quiet_two_blocks_keep_the_post_in_the_queue(world):
    world["blocked"] = {A}
    for _ in range(2):
        sy.run_evergreen()
    assert A in [i["link"] for i in sy.evergreen_candidates(FEED, register(), NOW)]
    assert not any("aus der Warteschlange" in m for m in world["sent"])


def test_ca2_quiet_a_pass_ends_the_run_of_blocks(world):
    world["blocked"] = {A}
    sy.run_evergreen()
    sy.run_evergreen()
    world["blocked"] = set()
    reg = register()
    del reg[B]                                   # B was posted; make A due again
    json.dump(reg, open(sy.REGISTER_FILE, "w"))
    sy.run_evergreen()
    assert "evergreen_blocked" not in register()[A]


def test_ca2_fires_a_run_drafts_at_most_two(world):
    world["blocked"] = {A, B, C}
    assert sy.run_evergreen() == 1
    assert world["drafted"] == [A, B]
    assert world["posted"] == []


def test_ca2_quiet_an_explicit_url_ignores_the_drop(world):
    json.dump({A: {"evergreen_blocked": {"in_a_row": 3}}}, open(sy.REGISTER_FILE, "w"))
    assert sy.run_evergreen(url=A) == 0
    assert world["posted"] == [A]


# ── CA3: the block is in the log with its reason, and in the send record ──

def test_ca3_fires_a_block_writes_a_log_line_with_the_rule(world, caplog):
    world["blocked"] = {A}
    with caplog.at_level(logging.WARNING, logger="syndicate"):
        sy.run_evergreen()
    lines = [r.message for r in caplog.records if r.message.startswith("GATE BLOCKED")]
    assert lines == ["GATE BLOCKED (1/3 in a row): a.html — "
                     "hook: g2f Substanz-Boden — no concrete number anywhere"]


def test_ca3_quiet_a_pass_writes_no_block_line(world, caplog):
    with caplog.at_level(logging.WARNING, logger="syndicate"):
        sy.run_evergreen()
    assert not any("GATE BLOCKED" in r.message for r in caplog.records)


def test_ca3_fires_send_telegram_goes_through_notify(monkeypatch):
    calls = []
    monkeypatch.setattr(sy.notify, "send_telegram",
                        lambda text, **k: calls.append((text, k)) or True)
    assert sy.send_telegram("x" * 5000, channel=sy.notify.STATS) is True
    assert calls == [("x" * 4000, {"channel": sy.notify.STATS, "parse_mode": "HTML"})]


def test_ca3_quiet_a_refused_send_is_reported_as_refused(monkeypatch):
    monkeypatch.setattr(sy.notify, "send_telegram", lambda text, **k: False)
    assert sy.send_telegram("gedrosselt", channel=sy.notify.STATS) is False


def test_ca3_the_block_message_names_the_reason(world):
    world["blocked"] = {A}
    sy.run_evergreen()
    block = [m for m in world["sent"] if "Evergreen blockiert" in m]
    assert len(block) == 1 and "(1/3 in Folge)" in block[0]
    assert "Grund: hook: g2f" in block[0]


# ── CA4: evergreen_verify names a gate block as one ──

LOG_BLOCKED = """\
[2026-10-15T09:00:07] INFO: EVERGREEN — 2026-10-15 09:00 UTC
[2026-10-15T09:00:07] INFO: 72 of 74 posts are due (never syndicated, or older than 90 days)
[2026-10-15T09:00:58] WARNING: GATE BLOCKED (1/3 in a row): We Enforced — hook: g2f Substanz-Boden — no concrete number anywhere
[2026-10-15T09:01:40] WARNING: GATE BLOCKED (1/3 in a row): Internal-only — hook: g2f Substanz-Boden — no concrete number anywhere
"""
LOG_QUIET = """\
[2026-10-15T09:00:07] INFO: EVERGREEN — 2026-10-15 09:00 UTC
[2026-10-15T09:00:07] INFO: 72 of 74 posts are due (never syndicated, or older than 90 days)
"""


def _verify(monkeypatch, tmp_path, text):
    log = tmp_path / "syndicate.log"
    log.write_text(text)
    monkeypatch.setattr(ev, "LOG", str(log))
    monkeypatch.setattr(ev.syndicate, "load_register", lambda: {})
    monkeypatch.setattr(ev.syndicate, "fetch_feed", lambda: [])
    return ev.format_report(ev.check("2026-10-15"))


def test_ca4_fires_a_blocked_run_is_reported_as_a_gate_block(monkeypatch, tmp_path):
    out = _verify(monkeypatch, tmp_path, LOG_BLOCKED)
    assert "Gate-Blockade</b> (2)" in out
    assert "no concrete number anywhere" in out
    assert "lief nichts" not in out
    assert "Läufe im Log: 1" in out, "a block line must not count as a run"


def test_ca4_quiet_without_a_block_the_old_finding_stands(monkeypatch, tmp_path):
    out = _verify(monkeypatch, tmp_path, LOG_QUIET)
    assert "Gate-Blockade" not in out
    assert "Kein Eintrag mit heutigem Datum im Register" in out


# ── CA5: the half-hourly run does not overwrite the evergreen heartbeat ──

def _heartbeat(path):
    return json.load(open(path))


def test_ca5_fires_the_evergreen_error_survives_the_next_regular_run(world):
    world["blocked"] = {A, B}
    sy.run_evergreen()
    assert sy.run() == 0                         # "No new posts", as at 09:30
    assert _heartbeat(sy.EVERGREEN_HEARTBEAT_FILE)["status"] == "error"
    assert "gate blocked" in _heartbeat(sy.EVERGREEN_HEARTBEAT_FILE)["detail"]
    assert _heartbeat(sy.HEARTBEAT_FILE)["status"] == "ok"


def test_ca5_quiet_a_good_evergreen_run_writes_ok_to_its_own_file(world, tmp_path):
    sy.run_evergreen()
    assert _heartbeat(sy.EVERGREEN_HEARTBEAT_FILE)["status"] == "ok"
    assert not (tmp_path / "hb.json").exists(), "evergreen wrote the regular heartbeat"


# ── the guard: c-evergreen-drops-a-blocked-post ──

def _guard(tmp_path, log_text):
    import pathlib
    import subprocess
    import sys
    import textwrap

    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = yaml.safe_load(open(root / "docs/invariants/c-evergreen-drops-a-blocked-post.yaml"))
    body = spec["abfrage"]["wert"].split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0]
    log = tmp_path / "syndicate.log"
    log.write_text(log_text)
    body = textwrap.dedent(body).replace('"logs/syndicate.log"', repr(str(log)))
    return subprocess.run([sys.executable, "-c", body], cwd=root, capture_output=True,
                          text=True, timeout=60).stdout.strip()


def _runs(*links, day="2026-10-20"):
    out = []
    for n, link in enumerate(links):
        out.append(f"[{day}T09:{n:02d}:00] INFO: EVERGREEN — {day} 09:00 UTC")
        out.append(f"[{day}T09:{n:02d}:01] INFO: 9 of 9 posts are due (never syndicated, or older than 90 days)")
        out.append(f"[{day}T09:{n:02d}:01] INFO: Evergreen: T  ({link})")
        out.append(f"[{day}T09:{n:02d}:30] INFO: Evergreen: second draft  (https://moltrust.ch/blog/z.html)")
    return "\n".join(out) + "\n"


def test_guard_fires_four_runs_on_the_same_post(tmp_path):
    out = _guard(tmp_path, _runs(A, A, A, A))
    assert "EVERGREEN OK" not in out and "a.html ist 4 Laeufe in Folge" in out


def test_guard_quiet_three_runs_then_another_post(tmp_path):
    out = _guard(tmp_path, _runs(A, A, A, B, A))
    assert out.startswith("EVERGREEN OK, 5 Laeufe")


def test_guard_quiet_the_runs_before_the_fix_do_not_count(tmp_path):
    out = _guard(tmp_path, _runs(A, A, A, A, day="2026-10-15"))
    assert out.startswith("EVERGREEN OK, 0 Laeufe")
