"""Charter watch: report a PR or comment that inserts or changes an out-of-scope
section in audit-charter.md of mirjak/audit-bof-preparation, and nothing else
(Lars, 23.10.2026).

Each trigger has a silence case next to it. The silence cases are taken from
the repository as it stood on 23.10.2026: PR #61 rewrites deliverable 2,
mirjak's suggestion on it touches line 50, issue #64 argues about scope in
prose. None of them may report.
"""
import base64
import os
import sys
from datetime import datetime, timezone

import pytest

pytest.importorskip("yaml")
pytest.importorskip("requests")

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
import charter_watch as CW  # noqa: E402

_saved_argv = sys.argv
sys.argv = ["threadwatch"]  # module runs argparse.parse_args() at import
try:
    import threadwatch as TW  # noqa: E402
finally:
    sys.argv = _saved_argv

NOW = datetime(2026, 10, 23, 18, 0, 0, tzinfo=timezone.utc)
API = CW.API
R = CW.REPO

# The charter as on main 7af346a7, cut to the parts that matter here.
CHARTER = """# AUDIT Working Group Charter

To support governance ... but not the auditing of the underlying AI model, training data, or inference mechanisms.

## Scope and Goals

The working group will not define auditing policies or compliance frameworks, but instead provide the technical building blocks needed to support them.

## Deliverables
The AUDIT working group is expected to produce:

1. **Architecture for Autonomous Agent Auditing**
An Informational RFC describing roles.

2. **Audit Data Models and Semantics**
One or more Standards Track RFC(s) that identify and reuse existing data models.
If existing data models are identified the RFC(s) might create profiles.
"""

OOS = """
## Out of Scope

The following are out of scope for the initial work:

* Auditing of model training
* interactive/real-time auditing of agent behavior
"""

CHARTER_OOS = CHARTER.replace("## Deliverables", OOS.strip() + "\n\n## Deliverables")
CHARTER_OOS_2 = CHARTER_OOS.replace(
    "* interactive/real-time auditing of agent behavior",
    "* interactive/real-time auditing of agent behavior\n"
    "* verification of evidence against the action record")
CHARTER_D2 = CHARTER.replace(
    "might create profiles.",
    "might create profiles. The outputs of the VCON WG include a record format.")


# ─── Sections ─────────────────────────────────────────────────────────────────

def test_heading_section_is_found_and_ends_at_the_next_heading():
    blocks = CW.oos_blocks(CHARTER_OOS)
    assert len(blocks) == 1
    assert blocks[0].startswith("## Out of Scope")
    assert "real-time" in blocks[0] and "Deliverables" not in blocks[0]


def test_the_charter_on_main_has_no_section():
    # "Scope and Goals", "will not define", "not the auditing of" — none is one.
    assert CW.oos_blocks(CHARTER) == []


@pytest.mark.parametrize("text", [
    "## Out-of-scope\n\n- a\n",
    "### Non-Goals\n\n- a\n",
    "**Out of scope**\n\n- a\n",
    "Items not in scope:\n\n- a\n- b\n",
    "The following are out of scope:\n* a\n",
])
def test_markers(text):
    assert CW.oos_blocks(text)


@pytest.mark.parametrize("text", [
    "## Scope and Goals\n\n- a\n",
    "Real-time auditing is not out of scope:\n- a\n",
    "I think real-time auditing is out of scope for now.\n",
    "## In scope\n\n- a\n",
])
def test_negation_and_prose_stay_silent(text):
    assert CW.oos_blocks(text) == []


def test_lead_in_takes_its_list_and_stops_at_the_next_paragraph():
    text = "Out of scope:\n\n- a\n- b\n\nNext paragraph.\n"
    assert CW.oos_blocks(text) == ["Out of scope:\n\n- a\n- b"]


# ─── A fake GitHub ────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status):
        self.status_code = status


class _HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = _Resp(status)


def _content(text):
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


class FakeGH:
    """Routes by URL (and ref); a route may be a list of pages."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        key = url
        if "/contents/" in url:
            key = f"{url}@{(params or {}).get('ref')}"
        if "?page=" in url:
            key = url
        if key not in self.routes:
            raise _HTTPError(404)
        val = self.routes[key]
        if isinstance(val, Exception):
            raise val
        if isinstance(val, tuple):
            return val
        return val, {}


def _pr(num, head, base="b0", title="t", user="someone", updated="2026-10-23T12:00:00Z"):
    return {"number": num, "title": title, "user": {"login": user}, "updated_at": updated,
            "html_url": f"https://github.com/{R}/pull/{num}",
            "head": {"sha": head, "repo": {"full_name": R}}, "base": {"sha": base}}


def _routes(pulls=(), files=None, contents=None, review=(), convo=(), issues=(), main="m1"):
    contents = contents or {}
    routes = {
        f"{API}/repos/{R}/issues": list(issues),
        f"{API}/repos/{R}/pulls": list(pulls),
        f"{API}/repos/{R}/pulls/comments": list(review),
        f"{API}/repos/{R}/issues/comments": list(convo),
        f"{API}/repos/{R}/commits/main": {"sha": main, "html_url": "u",
                                          "commit": {"author": {"name": "mirjak"}, "message": "m"}},
        f"{API}/repos/{R}/contents/{CW.CHARTER}@{main}": _content(contents.pop(main, CHARTER)),
    }
    for pr in pulls:
        n = pr["number"]
        routes[f"{API}/repos/{R}/pulls/{n}/files"] = (files or {}).get(n, [{"filename": CW.CHARTER}])
        routes[f"{API}/repos/{R}/compare/{pr['base']['sha']}...{pr['head']['sha']}"] = {
            "merge_base_commit": {"sha": "mb"}}
    routes[f"{API}/repos/{R}/contents/{CW.CHARTER}@mb"] = _content(CHARTER)
    for ref, text in contents.items():
        routes[f"{API}/repos/{R}/contents/{CW.CHARTER}@{ref}"] = _content(text)
    return routes


STATE = {"since": "2026-10-23T06:00:00Z", "seen": [], "main_fp": CW._digest()}


# ─── PRs ──────────────────────────────────────────────────────────────────────

def test_pr_inserting_a_section_reports():
    gh = FakeGH(_routes(pulls=[_pr(70, "h70")], contents={"h70": CHARTER_OOS}))
    hits, nxt, counts = CW.run(gh, STATE, NOW)
    assert [(h["ref"], h["change"]) for h in hits] == [("#70", "inserted")]
    assert "Auditing of model training" in hits[0]["text"]
    assert hits[0]["key"] in nxt["seen"]


def test_pr_changing_a_section_reports():
    routes = _routes(pulls=[_pr(71, "h71")], contents={"h71": CHARTER_OOS_2})
    routes[f"{API}/repos/{R}/contents/{CW.CHARTER}@mb"] = _content(CHARTER_OOS)
    hits, _, _ = CW.run(FakeGH(routes), STATE, NOW)
    assert [(h["ref"], h["change"]) for h in hits] == [("#71", "changed")]


def test_pr_like_61_rewriting_deliverable_2_stays_silent():
    gh = FakeGH(_routes(pulls=[_pr(61, "h61")], contents={"h61": CHARTER_D2}))
    hits, _, counts = CW.run(gh, STATE, NOW)
    assert hits == [] and counts["prs_read"] == 1


def test_pr_on_another_file_is_not_read_further():
    gh = FakeGH(_routes(pulls=[_pr(72, "h72")], files={72: [{"filename": "README.md"}]}))
    hits, _, _ = CW.run(gh, STATE, NOW)
    assert hits == []
    assert not any("/compare/" in u for u, _ in gh.calls)


def test_the_same_change_reports_once_and_a_new_change_again():
    gh = FakeGH(_routes(pulls=[_pr(70, "h70")], contents={"h70": CHARTER_OOS}))
    hits, nxt, _ = CW.run(gh, STATE, NOW)
    assert len(hits) == 1
    again, nxt2, _ = CW.run(gh, nxt, NOW)
    assert again == []
    # A new commit moves updated_at past the window of the previous run.
    gh2 = FakeGH(_routes(pulls=[_pr(70, "h70b", updated="2026-10-23T18:30:00Z")],
                         contents={"h70b": CHARTER_OOS_2}))
    changed, _, _ = CW.run(gh2, nxt2, NOW)
    assert [h["ref"] for h in changed] == ["#70"]


def test_pulls_older_than_the_window_are_not_read():
    old = _pr(23, "h23", updated="2026-07-15T00:00:00Z")
    gh = FakeGH(_routes(pulls=[_pr(70, "h70"), old], contents={"h70": CHARTER}))
    _, _, counts = CW.run(gh, STATE, NOW)
    assert counts["prs_read"] == 1


# ─── Comments ─────────────────────────────────────────────────────────────────

def _review(cid, body, line, commit="c1", path=CW.CHARTER, created="2026-10-23T12:00:00Z"):
    return {"id": cid, "path": path, "body": body, "line": line, "commit_id": commit,
            "created_at": created, "user": {"login": "mirjak"},
            "html_url": f"https://github.com/{R}/pull/61#discussion_r{cid}"}


def _convo(cid, body, created="2026-10-23T12:00:00Z", pull=True):
    kind = "pull" if pull else "issues"
    return {"id": cid, "body": body, "created_at": created, "user": {"login": "x"},
            "html_url": f"https://github.com/{R}/{kind}/64#issuecomment-{cid}"}


SUGGEST_D2 = ("Proposed update based on the list discussion:\n```suggestion\n"
              "The outputs of the VCON WG include a record format.\n```")


def test_review_suggestion_like_mirjaks_on_61_stays_silent():
    gh = FakeGH(_routes(review=[_review(1, SUGGEST_D2, 17)], contents={"c1": CHARTER}))
    hits, _, counts = CW.run(gh, STATE, NOW)
    assert hits == [] and counts["new_pr_comments"] == 1


def test_review_suggestion_inside_the_section_reports():
    line = CHARTER_OOS.splitlines().index("* Auditing of model training") + 1
    body = "```suggestion\n* Auditing of model training data\n```"
    gh = FakeGH(_routes(review=[_review(2, body, line)], contents={"c1": CHARTER_OOS}))
    hits, _, _ = CW.run(gh, STATE, NOW)
    assert [(h["kind"], h["ref"]) for h in hits] == [("review comment", "#61")]


def test_review_suggestion_that_writes_a_section_reports():
    body = "```suggestion\n## Out of Scope\n\n* real-time auditing\n```"
    gh = FakeGH(_routes(review=[_review(3, body, 5)], contents={"c1": CHARTER}))
    hits, _, _ = CW.run(gh, STATE, NOW)
    assert len(hits) == 1


def test_review_remark_without_suggestion_inside_the_section_stays_silent():
    line = CHARTER_OOS.splitlines().index("* Auditing of model training") + 1
    gh = FakeGH(_routes(review=[_review(4, "Why this one?", line)], contents={"c1": CHARTER_OOS}))
    hits, _, _ = CW.run(gh, STATE, NOW)
    assert hits == []


def test_pr_comment_proposing_a_section_reports():
    body = "How about:\n\n```\nOut of scope:\n- real-time auditing\n```\n"
    gh = FakeGH(_routes(convo=[_convo(5, body)]))
    hits, _, _ = CW.run(gh, STATE, NOW)
    assert [h["kind"] for h in hits] == ["PR comment"]


def test_pr_comment_quoting_a_section_reports():
    body = "> ## Non-goals\n> - requirements on the auditing system itself\n\nThoughts?"
    gh = FakeGH(_routes(convo=[_convo(6, body)]))
    hits, _, _ = CW.run(gh, STATE, NOW)
    assert len(hits) == 1


def test_prose_about_scope_stays_silent():
    body = ("I think real-time auditing is out of scope for the initial work, "
            "but verification is not out of scope.")
    gh = FakeGH(_routes(convo=[_convo(7, body)]))
    hits, _, _ = CW.run(gh, STATE, NOW)
    assert hits == []


def test_issue_comments_are_not_pr_comments():
    body = "```\n## Out of Scope\n- a\n```"
    gh = FakeGH(_routes(convo=[_convo(8, body, pull=False)]))
    hits, _, counts = CW.run(gh, STATE, NOW)
    assert hits == [] and counts["new_pr_comments"] == 0


def test_counts_new_issues_and_prs():
    issues = [{"number": 64, "created_at": "2026-10-23T12:53:46Z"},
              {"number": 65, "created_at": "2026-10-23T13:00:00Z", "pull_request": {}},
              {"number": 61, "created_at": "2026-10-02T16:52:35Z", "pull_request": {}}]
    _, _, counts = CW.run(FakeGH(_routes(issues=issues)), STATE, NOW)
    assert (counts["new_issues"], counts["new_prs"]) == (1, 1)


# ─── main ─────────────────────────────────────────────────────────────────────

def test_main_baseline_is_silent_and_a_change_on_main_reports():
    first, nxt, _ = CW.run(FakeGH(_routes(main="m1")), {}, NOW)
    assert first == []
    hits, _, _ = CW.run(FakeGH(_routes(main="m2", contents={"m2": CHARTER_OOS})), nxt, NOW)
    assert [(h["kind"], h["change"]) for h in hits] == [("commit on main", "changed")]


# ─── Reads ────────────────────────────────────────────────────────────────────

def test_a_failed_page_raises():
    routes = _routes()
    routes[f"{API}/repos/{R}/pulls/comments"] = _HTTPError(502)
    with pytest.raises(CW.ReadError):
        CW.run(FakeGH(routes), STATE, NOW)


def test_the_page_cap_raises():
    nxt = {"Link": f'<{API}/repos/{R}/issues?page=2>; rel="next"'}
    routes = _routes()
    routes[f"{API}/repos/{R}/issues"] = ([], nxt)
    routes[f"{API}/repos/{R}/issues?page=2"] = ([], nxt)
    with pytest.raises(CW.ReadError, match="pages"):
        CW.run(FakeGH(routes), STATE, NOW)


def test_input_state_is_not_modified():
    state = {"since": "2026-10-23T06:00:00Z", "seen": ["x"], "main_fp": CW._digest()}
    CW.run(FakeGH(_routes(pulls=[_pr(70, "h70")], contents={"h70": CHARTER_OOS})), state, NOW)
    assert state == {"since": "2026-10-23T06:00:00Z", "seen": ["x"], "main_fp": CW._digest()}


# ─── Report ───────────────────────────────────────────────────────────────────

CFG = {"thresholds": {"max_per_category": 10}}


def _report(**kw):
    return TW.fmt_report({"urgent": [], "active": [], "stale": []}, [], NOW, CFG, **kw)


def test_report_carries_a_hit_and_no_blank_line_inside_pre():
    hit = {"kind": "PR", "ref": "#70", "actor": "a", "title": "t", "change": "inserted",
           "text": "## Out of Scope\n\n* a", "url": "u"}
    text = _report(charter_hits=[hit])
    assert "CHARTER OUT-OF-SCOPE" in text and "Nothing waiting" not in text
    pre = text[text.index("<pre>"):text.index("</pre>")]
    assert "\n\n" not in pre
    assert all(c.count("<pre>") == c.count("</pre>") for c in TW._split_telegram(text, 200))


def test_report_without_hits_says_nothing_about_the_charter():
    text = _report(charter_hits=[])
    assert "CHARTER" not in text and "Nothing waiting" in text


def test_report_names_a_failed_read():
    text = _report(charter_error="pulls/comments: 502")
    assert "CHARTER WATCH" in text and "502" in text
