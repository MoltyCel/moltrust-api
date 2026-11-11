"""Evergreen after 2026-10-27 (ops EG): no post leaves the list on its own.

On 2026-10-27 the kernel post left the queue after its third g2f block, and the
second draft of the same run failed g1x_fragment_coda on a closing "With
code.". Each criterion has a test that fires and one that stays quiet.
"""
import datetime
import json

import pytest

from agents import syndicate as sy

# The real gate, on the fixture copy of its rules (conftest).
pytestmark = pytest.mark.usefixtures("voice_gate_fixture_rules")

A = "https://moltrust.ch/blog/a.html"
B = "https://moltrust.ch/blog/b.html"
C = "https://moltrust.ch/blog/c.html"
NOW = datetime.datetime(2026, 10, 27, 9, 0, tzinfo=datetime.timezone.utc)

WITH_FIGURES = "From an initial report of 386 malicious skills the count passed 1,184."
# The kernel post's digits, all of them inside a name or a year.
NO_FIGURES = "The AAE is Ed25519-signed and anchored on Base L2, as RSAC 2026 heard."

# The second draft of 2026-10-27 09:02, word for word, and the article lines it
# draws on. The hook passed; the link post failed on its last two words.
SKILLS = "https://moltrust.ch/blog/malicious-ai-skills-verifiable-identity.html"
SKILLS_ARTICLE = (
    "In February 2026, security researchers discovered that the ClawHub "
    "Marketplace had a problem. From an initial report of 386 malicious skills, "
    "the count tripled within weeks to over 1,184. A coordinated campaign dubbed "
    "\"ClawHavoc\" was responsible for 335 of those, distributing Atomic Stealer "
    "malware through seemingly legitimate skill packages. Security researchers "
    "disclosed CVE-2026-25253, a one-click remote code execution vulnerability "
    "that affected every OpenClaw instance running the default configuration. "
    "Every skill publisher gets a W3C DID that is cryptographically signed and "
    "anchored to a public blockchain.")
SKILLS_HOOK = (
    "386 malicious skills in the first report on the ClawHub Marketplace. Within "
    "weeks the count passed 1,184. One campaign, ClawHavoc, accounted for 335 of "
    "them, shipping Atomic Stealer through packages named like popular ones. "
    "Anyone could publish without a verified identity.")
SKILLS_LINK = (
    "Write-up: CVE-2026-25253, the one-click RCE in every default OpenClaw "
    "install, and the checks a registry can run before a skill executes: "
    "publisher DID, trust score, blockchain anchor. With code.\n" + SKILLS)


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
    monkeypatch.setattr(sy, "EVERGREEN_HEARTBEAT_FILE", str(tmp_path / "hb_evergreen.json"))
    monkeypatch.setattr(sy, "PRIORITY_FILE", str(tmp_path / "no-priority.json"))
    monkeypatch.setattr(sy.x_meter, "reads_paused", lambda now=None: None)
    monkeypatch.setattr(sy, "_RUN_POSTS", 0)
    monkeypatch.setattr(sy, "fetch_feed", lambda: [dict(i) for i in FEED])
    monkeypatch.setattr(sy, "bluesky_mirror", lambda parts: [])
    monkeypatch.setattr(sy, "deliver_linkedin", lambda *a, **k: True)
    monkeypatch.setattr(sy, "record_linkedin_draft", lambda *a, **k: None)
    monkeypatch.setattr(sy.tagesbericht_eingang, "ablegen", lambda *a, **k: None)


@pytest.fixture
def world(monkeypatch):
    """Article text per link, what the gate blocks, what was drafted, posted and sent."""
    w = {"text": {}, "blocked": set(), "drafted": [], "posted": [], "sent": [],
         "draft": None}

    def draft(i):
        w["drafted"].append(i["link"])
        if w["draft"]:
            return w["draft"](i)
        return {"parts": [f"386 hook {i['link']}", f"link {i['link']}"], "linkedin": ""}

    monkeypatch.setattr(sy, "fetch_article_text",
                        lambda u, limit=6000: w["text"].get(u, WITH_FIGURES))
    monkeypatch.setattr(sy, "draft_teaser", draft)
    monkeypatch.setattr(sy.x_post, "post_thread", lambda parts, kind=None:
                        w["posted"].append(parts) or ["11"])
    monkeypatch.setattr(sy, "send_telegram", lambda m, **k: w["sent"].append(m) or True)
    return w


def gate_stub(monkeypatch, w):
    monkeypatch.setattr(sy, "check_teaser", lambda parts, i:
                        (False, "  ! g2f Substanz-Boden — no concrete number anywhere")
                        if i["link"] in w["blocked"] else (True, "PASS"))


def register():
    try:
        return json.load(open(sy.REGISTER_FILE))
    except FileNotFoundError:
        return {}


def save(reg):
    json.dump(reg, open(sy.REGISTER_FILE, "w"))


def links(reg=None):
    return [i["link"] for i in sy.evergreen_candidates(FEED, register() if reg is None
                                                       else reg, NOW)]


# ── EG1: a post the old code dropped is in the list again ──

def test_eg1_fires_the_kernel_post_as_the_old_code_left_it_is_a_candidate():
    save({A: {"evergreen_blocked": {"in_a_row": 3, "reason": "hook: g2f"}}})
    assert A in links()


def test_eg1_quiet_a_post_posted_last_week_stays_out():
    save({A: {"last": "2026-10-20T09:00:00+00:00"}})
    assert A not in links()


# ── EG2: the figure for the hook comes from the article ──

def test_eg2_fires_the_figures_are_read_out_of_the_article():
    assert sy.article_figures(SKILLS_ARTICLE) == ["386", "1,184", "335"]
    assert sy.article_figures("Carries 71% of them, Gap 1 and Layer 2.") == ["71%", "1", "2"]


def test_eg2_quiet_digits_in_a_name_or_a_year_are_not_a_figure():
    assert sy.article_figures(NO_FIGURES) == []
    assert sy.article_figures("CVE-2026-25253 in February 2026, on /24 and v1.2.3") == []


def test_eg2_quiet_the_byline_and_stray_css_are_not_a_figure():
    # The kernel post as fetch_article_text returned it on 2026-10-27.
    page = ("Trust April 4, 2026 5 min read Security We Enforced It. This was Gap 1 "
            "from RSAC 2026. Layer 2 — API. "
            'class="cta" style="border-top:1px solid var(--slate-200);padding:40px 0;'
            'margin-top:48px"> BUILD')
    assert sy.article_figures(page) == ["1", "2"]


def test_eg2_fires_the_instruction_names_the_figures():
    text = sy._teaser_instructions({**item(A), "figures": ["386", "1,184"]})
    assert "exactly as written there: 386, 1,184." in text


def test_eg2_fires_no_figure_no_draft_and_one_report(world):
    world["text"] = {A: NO_FIGURES, B: NO_FIGURES, C: NO_FIGURES}
    assert sy.run_evergreen() == 0
    assert sy.run_evergreen() == 0
    assert world["drafted"] == [] and world["posted"] == []
    hand = [m for m in world["sent"] if "Aufhänger von Hand" in m]
    assert len(hand) == 3, "each post once, not once per run"
    assert links() == [A, B, C], "a post without a figure stays in the list"


def test_eg2_quiet_a_post_with_a_figure_is_drafted_and_not_reported(world, monkeypatch):
    gate_stub(monkeypatch, world)
    assert sy.run_evergreen() == 0
    assert world["drafted"] == [A]
    assert not any("Aufhänger von Hand" in m for m in world["sent"])


def test_eg2_quiet_a_figure_added_later_clears_the_report(world, monkeypatch):
    gate_stub(monkeypatch, world)
    save({A: {"evergreen_manual": {"since": "2026-10-27"}}})
    sy.run_evergreen()
    assert "evergreen_manual" not in register()[A]


def test_eg2_a_post_without_a_figure_costs_no_draft(world, monkeypatch):
    world["text"] = {A: NO_FIGURES}
    world["blocked"] = {B}
    gate_stub(monkeypatch, world)
    assert sy.run_evergreen() == 0
    assert world["drafted"] == [B, C]
    assert [p[1] for p in world["posted"]] == [f"link {C}"]


def test_eg2_fires_a_hook_without_one_of_the_figures_is_not_posted(world, monkeypatch):
    # A digit is enough for g2f; it has to be one the article carries.
    gate_stub(monkeypatch, world)
    world["draft"] = lambda i: {"parts": ["7 hooks", "link " + i["link"]], "linkedin": ""}
    sy.run_evergreen()
    assert world["posted"] == []
    assert register()[A]["evergreen_blocked"]["reason"] == (
        "hook: the hook carries none of the article's figures: 386, 1,184")


def test_eg2_quiet_a_figure_inside_a_longer_number_does_not_count():
    assert not sy.carries_figure("13860 skills", ["386"])
    assert sy.carries_figure("386 skills, then 1,184.", ["1,184"])


# ── EG3: three refusals defer a post, they do not remove it ──

def test_eg3_fires_three_blocks_defer_and_report_once(world, monkeypatch):
    world["blocked"] = {A}
    gate_stub(monkeypatch, world)
    for _ in range(3):
        sy.run_evergreen()
    assert links()[-1] == A
    assert sum("zurückgestellt" in m for m in world["sent"]) == 1
    # Drafted again later and blocked again: counted, not reported again.
    reg = register()
    reg[B] = {"last": NOW.isoformat()}
    reg[C] = {"last": NOW.isoformat()}
    save(reg)
    sy.run_evergreen()
    assert register()[A]["evergreen_blocked"]["in_a_row"] == 4
    assert sum("zurückgestellt" in m for m in world["sent"]) == 1


def test_eg3_quiet_two_blocks_keep_the_post_in_its_place(world, monkeypatch):
    world["blocked"] = {A}
    gate_stub(monkeypatch, world)
    for _ in range(2):
        sy.run_evergreen()
    assert links()[0] == A
    assert not any("zurückgestellt" in m for m in world["sent"])


def test_eg3_only_a_recorded_decision_takes_a_post_out():
    save({A: {"evergreen_removed": {"by": "Lars", "on": "2026-10-27"}},
          B: {"evergreen_blocked": {"in_a_row": 9}}})
    assert links() == [C, B]


# ── EG4: no closing sentence without a verb ──

def test_eg4_fires_the_instruction_forbids_a_verbless_closing_tag():
    text = sy._teaser_instructions({**item(A), "figures": ["386"]})
    assert "Every sentence has a finite verb" in text and "'With code.'" in text


def test_eg4_fires_with_code_is_struck_and_the_post_goes_out(world):
    # Against the code before ops EG this post is blocked under
    # g1x_fragment_coda and nothing goes out.
    world["text"] = {A: SKILLS_ARTICLE}
    world["draft"] = lambda i: {"parts": [SKILLS_HOOK, SKILLS_LINK], "linkedin": ""}
    assert sy.run_evergreen() == 0
    assert len(world["posted"]) == 1
    hook, link_post = world["posted"][0]
    assert hook == SKILLS_HOOK
    assert "With code." not in link_post
    assert link_post == SKILLS_LINK.replace(" With code.", "")
    assert "evergreen_blocked" not in register().get(A, {})


def test_eg4_quiet_a_post_without_a_coda_is_left_as_it_is(world):
    world["text"] = {A: SKILLS_ARTICLE}
    clean = SKILLS_LINK.replace(" With code.", "")
    world["draft"] = lambda i: {"parts": [SKILLS_HOOK, clean], "linkedin": ""}
    assert sy.run_evergreen() == 0
    assert world["posted"] == [[SKILLS_HOOK, clean]]


def test_eg4_quiet_a_block_for_another_rule_strikes_nothing():
    report = "  ! g2f Substanz-Boden — no concrete number anywhere"
    assert sy.strike_codas([SKILLS_HOOK, SKILLS_LINK], report) is None


def test_eg4_quiet_a_part_that_is_only_the_coda_is_not_emptied():
    report = ("  ! g1x_fragment_coda Fragment-Coda (Nachschlag ohne Verb) — tweet 2 "
              "coda: short coda with no finite verb: “With code.”")
    assert sy.strike_codas([SKILLS_HOOK, "With code. " + SKILLS], report) is None


# ── EG5: the gates are the gates ──

def test_eg5_the_gate_still_refuses_the_draft_with_its_coda():
    ok, report = sy.check_teaser([SKILLS_HOOK, SKILLS_LINK],
                                 {"link": SKILLS, "article_text": SKILLS_ARTICLE})
    assert not ok and "g1x_fragment_coda" in report
