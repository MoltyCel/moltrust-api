"""ListWatch: public-aikr, cross-posts and the name filter (ops TW-AIKR, 23.11.2026).

public-aikr is watched next to public-agent-identity, and most of its trust
threads are cross-posted there: the same mail, archived on both lists under the
same Message-ID. Measured 23.11.2026 on the live archives: 9 of the 24
public-aikr messages 0060-0083 of November are also on public-agent-identity
(0045, 0046, 0048-0054), each with an identical Message-ID. Without the
duplicate check every one of them would be reported twice.
"""
import importlib
import json
import os
import sys

import pytest

pytest.importorskip("requests")

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

BASE = "https://lists.w3.org/Archives/Public/"


@pytest.fixture
def lw(tmp_path, monkeypatch):
    # The module parses argv and opens its log under ~/moltstack at import.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["w3c_listwatch"])
    sys.modules.pop("w3c_listwatch", None)
    mod = importlib.import_module("w3c_listwatch")
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(mod, "LISTS", [("public-agent-identity", "w3c"),
                                       ("public-aikr", "w3c")])
    monkeypatch.setattr(mod, "load_secrets_into_env", lambda: None)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    yield mod
    sys.modules.pop("w3c_listwatch", None)


def _page(msgid, body="Plain text.", irt=""):
    return (f'<!-- id="{msgid}" -->\n<!-- inreplyto="{irt}" -->\n'
            f'<pre id="body">{body}</pre>')


def _index(*entries):
    return "".join(f'<li><a id="msg{int(n)}" href="{n}.html">{s}</a> '
                   f'<span class="messages-list-author">{a}</span>'
                   for n, s, a in entries)


def _archive(lw, lists):
    """fetch() over {list: (index entries, {num: page})} in the current month."""
    cur = lw.month_keys("w3c")[1]
    fetched = []

    def fetch(url):
        fetched.append(url)
        for lst, (entries, pages) in lists.items():
            pre = f"{BASE}{lst}/"
            if not url.startswith(pre):
                continue
            rest = url[len(pre):]
            if rest == f"{cur}/":
                return 200, _index(*entries)
            if rest.endswith("/"):
                return 404, ""
            num = rest.rsplit("/", 1)[-1][:-len(".html")]
            return (200, pages[num]) if num in pages else (404, "")
        return 404, ""
    return fetch, fetched


def _run(lw, monkeypatch, lists, state):
    lw.STATE_FILE.write_text(json.dumps(state))
    fetch, fetched = _archive(lw, lists)
    monkeypatch.setattr(lw, "fetch", fetch)
    sent = []

    def fake_send(text, **kw):
        sent.append((kw.get("art"), text))
        lw.notify._LETZTER_GRUND = "ablage" if kw.get("art") == "pipeline-bericht" else ""
        return True
    monkeypatch.setattr(lw.notify, "send_telegram", fake_send)
    rc = lw.main()
    report = next((t for a, t in sent if a == "pipeline-bericht"), "")
    return rc, report, sent, fetched


# --- the list is watched ----------------------------------------------------------

def test_public_aikr_is_watched_after_public_agent_identity():
    import ast
    src = open(os.path.join(_REPO, "scripts", "w3c_listwatch.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    lists = next(ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "LISTS")
    names = [n for n, _ in lists]
    assert ("public-aikr", "w3c") in lists
    assert names.index("public-agent-identity") < names.index("public-aikr")


# --- the first run starts at LIST_START ---------------------------------------------

def test_first_run_reports_from_the_start_on(lw, monkeypatch):
    cur = lw.month_keys("w3c")[1]
    monkeypatch.setattr(lw, "LIST_START", {"public-aikr": (cur, "0060")})
    entries = [("0058", "Old", "A One"), ("0059", "Old 2", "B Two"),
               ("0060", "New", "C Three"), ("0061", "New 2", "D Four")]
    pages = {n: _page(f"{n}@x") for n, _, _ in entries}
    rc, report, _, fetched = _run(lw, monkeypatch, {"public-aikr": (entries, pages)},
                                  {"lists": {"public-agent-identity": {}}})
    assert rc == 0
    assert "[public-aikr] 2 new" in report
    assert "C Three" in report and "D Four" in report
    assert "A One" not in report and "B Two" not in report
    # the messages before the start are not even fetched
    assert not any(u.endswith(("0058.html", "0059.html")) for u in fetched)
    saved = json.loads(lw.STATE_FILE.read_text())["lists"]["public-aikr"][cur]
    assert saved == ["0058", "0059", "0060", "0061"]


def test_the_start_has_no_effect_once_the_list_has_state(lw, monkeypatch):
    # Schweigeprobe der anderen Richtung: a message below the start that is new
    # on a list with state is reported, not swallowed by the baseline.
    cur = lw.month_keys("w3c")[1]
    monkeypatch.setattr(lw, "LIST_START", {"public-aikr": (cur, "0060")})
    entries = [("0058", "Late arrival", "A One"), ("0060", "Seen", "C Three")]
    pages = {"0058": _page("0058@x")}
    rc, report, _, _ = _run(lw, monkeypatch, {"public-aikr": (entries, pages)},
                            {"lists": {"public-aikr": {cur: ["0060"]}}})
    assert "[public-aikr] 1 new" in report and "A One" in report


def test_before_start_orders_months_and_ids(lw, monkeypatch):
    monkeypatch.setattr(lw, "LIST_START", {"public-aikr": ("2026Nov", "0060")})
    assert lw.before_start("public-aikr", "w3c", "2026Oct", "0099")
    assert lw.before_start("public-aikr", "w3c", "2026Nov", "0059")
    assert not lw.before_start("public-aikr", "w3c", "2026Nov", "0060")
    assert not lw.before_start("public-aikr", "w3c", "2026Dec", "0000")
    assert not lw.before_start("public-agent-identity", "w3c", "2026Oct", "0000")


# --- a cross-post appears once -------------------------------------------------------

def test_a_cross_post_new_on_both_lists_is_reported_once(lw, monkeypatch, caplog):
    subj, auth = "AI KR Trust Layer crosswalks, taskforce", "Paola Di Maio"
    rc, report, _, _ = _run(lw, monkeypatch, {
        "public-agent-identity": ([("0048", subj, auth)], {"0048": _page("X1@mail")}),
        "public-aikr": ([("0071", subj, auth), ("0076", "Formal definition", "Milton Ponson")],
                        {"0071": _page("X1@mail"), "0076": _page("Y@mail")}),
    }, {"lists": {"public-agent-identity": {}, "public-aikr": {}}})
    assert rc == 0
    assert report.count(subj) == 1
    assert "[public-agent-identity] 1 new" in report
    assert "[public-aikr] 1 new (1 cross-post(s) reported under another list)" in report
    cur = lw.month_keys("w3c")[1]
    assert f"decision public-agent-identity/{cur}/0048: reported" in caplog.text
    assert (f"decision public-aikr/{cur}/0071: duplicate of "
            f"public-agent-identity/{cur}/0048, not reported") in caplog.text
    assert f"decision public-aikr/{cur}/0076: reported" in caplog.text


def test_a_cross_post_reported_in_an_earlier_run_is_not_reported_again(lw, monkeypatch, caplog):
    # public-agent-identity 0048 was reported before public-aikr was watched;
    # its Message-ID is in no state. The candidate is found by author and
    # subject and confirmed by its Message-ID.
    cur = lw.month_keys("w3c")[1]
    subj, auth = "AI KR Trust Layer crosswalks, taskforce", "Paola Di Maio"
    rc, report, sent, fetched = _run(lw, monkeypatch, {
        "public-agent-identity": ([("0048", subj, auth)], {"0048": _page("X1@mail")}),
        "public-aikr": ([("0071", subj, auth)], {"0071": _page("X1@mail")}),
    }, {"lists": {"public-agent-identity": {cur: ["0048"]}, "public-aikr": {}}})
    assert rc == 0
    assert report == "" and sent == []
    assert (f"decision public-aikr/{cur}/0071: duplicate of "
            f"public-agent-identity/{cur}/0048, not reported") in caplog.text
    assert "nothing new" in caplog.text
    # state advances, so the duplicate is not decided again next run
    assert "0071" in json.loads(lw.STATE_FILE.read_text())["lists"]["public-aikr"][cur]


def test_same_author_and_subject_with_another_message_id_is_reported(lw, monkeypatch):
    # Schweigeprobe: "Re: DNS vs DID in the Trust Layer /" by the same author
    # runs a dozen times in one thread. Only the Message-ID makes a duplicate.
    cur = lw.month_keys("w3c")[1]
    subj, auth = "Re: DNS vs DID in the Trust Layer /", "Larry Lewis"
    rc, report, _, _ = _run(lw, monkeypatch, {
        "public-agent-identity": ([("0027", subj, auth)], {"0027": _page("A@sovrlabs")}),
        "public-aikr": ([("0081", subj, auth)], {"0081": _page("B@sovrlabs")}),
    }, {"lists": {"public-agent-identity": {cur: ["0027"]}, "public-aikr": {}}})
    assert "[public-aikr] 1 new" in report and "cross-post" not in report


def test_a_message_without_id_is_reported(lw, monkeypatch, caplog):
    # A page that cannot be read gives no Message-ID; reporting twice is the
    # cheaper error than dropping.
    cur = lw.month_keys("w3c")[1]
    subj, auth = "Trust KR task force", "Paola Di Maio"
    rc, report, _, _ = _run(lw, monkeypatch, {
        "public-agent-identity": ([("0049", subj, auth)], {"0049": _page("X2@mail")}),
        "public-aikr": ([("0072", subj, auth)], {}),
    }, {"lists": {"public-agent-identity": {cur: ["0049"]}, "public-aikr": {}}})
    assert "[public-aikr] 1 new" in report
    assert f"decision public-aikr/{cur}/0072: reported" in caplog.text


# --- relevance -------------------------------------------------------------------

@pytest.mark.parametrize("text, term", [
    ("Kroehl's envelope draft covers delegated authority.", "kroehl"),
    ("See draft-kroehl-agentic-trust-aae-03.", "draft-kroehl"),
    ("MolTrust publishes the registry.", "moltrust"),
    ("The AAE binds the action.", "aae"),
])
def test_a_message_naming_us_is_upgraded(lw, text, term):
    why = lw.upgrade_reasons("Staford Titus S.", "Re: crosswalks", text, [], set())
    assert len(why) == 1 and term in why[0].removeprefix("nennt ").split(", ")
    if term == "draft-kroehl":
        # the draft name does not count a second time as the bare name
        assert "kroehl" not in why[0].replace("draft-kroehl", "")


@pytest.mark.parametrize("subj, body", [
    # the CG direction dispute, as titled on public-aikr in November
    ("Re: W3 AIKR CG straying from its main objective",
     "The CG should return to knowledge representation proper."),
    ("CG direction , update inventory and non agentic trust",
     "Non agentic trust needs a place in the inventory."),
    # the vocabulary threads
    ("Formal definition of knowledge representation in all academic disciplines",
     "A KR vocabulary must be discipline neutral."),
    ("ATP CG contribution: correction and vocabulary alignment",
     "Vocabulary alignment for the Trust & Identity crosswalk."),
])
def test_direction_dispute_and_vocabulary_are_not_upgraded(lw, subj, body):
    assert lw.upgrade_reasons("Milton Ponson", subj, body, ["other@x"], {"lars@x"}) == []


@pytest.mark.parametrize("line", [
    'Cc: "Lars Kersten Kroehl"<lars@moltrust.ch>',
    "To: Lars Kersten Kroehl",
    "On Fri, 21 Nov 2026, Lars Kersten Kroehl wrote:",
    "Am 21.11.2026 schrieb Lars Kroehl:",
    # as public-aikr 2026Aug/0002 and 2026Sep/0005 carry them, live 23.11.2026:
    "On Tue, Jul 21, 2026 at 5:31=E2=80=AFPM Lars Kersten Kroehl <lars@moltrust.ch<mailto:lars@moltrust.ch>",
    "On Tue, Jul 21, 2026 at 5:31 PM Lars Kersten Kroehl",
    'Next message: Lars Kersten Kroehl: "Re: Knowledge Representation for the Trust Layer"',
])
def test_the_name_in_an_address_or_attribution_line_does_not_count(lw, line):
    assert lw.upgrade_reasons("Owen Ambur", "Re: task force", f"{line}\nAgreed.",
                              [], set()) == []


def test_a_real_address_to_lars_in_the_text_is_upgraded(lw):
    # public-aikr 2026Sep/0011, Paola Di Maio, live 23.11.2026: Lars is named in
    # the text, not in a header. "On" or "Next" in the middle of a line, and a
    # sentence that starts with "On" without a date, still count.
    body = ("@Lars Kersten Kroehl <lars@moltrust.ch>   thanks for facilitating info\n"
            "On trust, Kroehl's draft covers it.")
    assert lw.upgrade_reasons("Paola Di Maio", "Re: KR for the Trust Layer", body,
                              [], set()) == ["nennt kroehl"]
    assert lw.upgrade_reasons("Paola Di Maio", "x", "On delegation, Kroehl is right.",
                              [], set()) == ["nennt kroehl"]


def test_lars_naming_himself_is_not_upgraded(lw):
    assert lw.upgrade_reasons("Lars Kersten Kroehl", "x", "Kroehl here.", [], set()) == []
