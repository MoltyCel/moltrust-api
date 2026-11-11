"""ListWatch: a list message that concerns us reaches Telegram (ops MELD-LIST).

Since NOTIFY-4 (07.11.2026) the ListWatch report is filed, not sent. A message
is upgraded on facts read from the message itself: it answers one of Lars' own
posts, or it names moltrust, AAE or draft-kroehl outside the quoted text. The
upgraded message goes out as a HANDELN message and stays in the filed report.
The log line says whether the report was sent, filed or not delivered.
"""
import importlib
import json
import os
import sys

import pytest

pytest.importorskip("requests")

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from app import meldungsarten as ma  # noqa: E402

LARS_ID = "CAFJihoo-lars-1@mail.gmail.com"
W3C_IDX = "https://lists.w3.org/Archives/Public/public-agentprotocol/"


@pytest.fixture
def lw(tmp_path, monkeypatch):
    # The module parses argv and opens its log under ~/moltstack at import.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["w3c_listwatch"])
    sys.modules.pop("w3c_listwatch", None)
    mod = importlib.import_module("w3c_listwatch")
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(mod, "LISTS", [("public-agentprotocol", "w3c"), ("audit", "ietf")])
    monkeypatch.setattr(mod, "load_secrets_into_env", lambda: None)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    yield mod
    sys.modules.pop("w3c_listwatch", None)


def _w3c_page(msgid, irt="", body="Plain text."):
    return (f'<!-- id="{msgid}" -->\n<!-- inreplyto="{irt}" -->\n'
            f"<pre id=\"body\">{body}</pre>")


def _ietf_page(msgid, irt="", body="Plain text."):
    head = f"Message-Id: &lt;{msgid}&gt;\n"
    if irt:
        head += f"In-Reply-To: &lt;{irt}&gt;\n"
    return f"<div>{head}Subject: x</div><pre>{body}</pre>"


def _w3c_index(*entries):
    return "".join(f'<li><a id="msg{n}" href="{n}.html">{s}</a> '
                   f'<span class="messages-list-author">{a}</span>'
                   for n, s, a in entries)


def _archive(index_now, pages):
    """fetch() over one W3C list; the IETF list and the old month are empty."""
    def fetch(url):
        if "mailarchive.ietf.org" in url:
            return 200, ""
        if url.startswith(W3C_IDX) and url.endswith("/"):
            return (200, index_now) if url.endswith(("Nov/", "Dec/")) else (404, "")
        name = url.rsplit("/", 1)[-1]
        return (200, pages[name]) if name in pages else (404, "")
    return fetch


# --- the ids, read from both archives -----------------------------------------

def test_message_ids_from_a_w3c_page(lw):
    assert lw.message_ids(_w3c_page("a@x", "b@y"), "w3c") == ("a@x", ["b@y"])
    assert lw.message_ids(_w3c_page("a@x"), "w3c") == ("a@x", [])


def test_message_ids_from_an_ietf_page_as_the_archive_prints_it(lw):
    # The live archive indents each header line inside <p> and ends it with <br />.
    page = ('<p>\n      \n        Message-Id: &lt;FDA4@moltrust.ch&gt;<br />\n      \n'
            '        In-Reply-To: &lt;CAFJ-1@mail.gmail.com&gt;<br />\n</p>'
            '<div class="msg-payload"><pre class="wordwrap">Hi</pre></div>')
    assert lw.message_ids(page, "ietf") == ("FDA4@moltrust.ch", ["CAFJ-1@mail.gmail.com"])


def test_message_ids_from_an_ietf_page(lw):
    assert lw.message_ids(_ietf_page("a@x", "b@y"), "ietf") == ("a@x", ["b@y"])
    assert lw.message_ids(_ietf_page("a@x"), "ietf") == ("a@x", [])


# --- the rule ------------------------------------------------------------------

def test_a_reply_to_lars_is_upgraded(lw):
    assert lw.upgrade_reasons("Dana Reviewer", "Re: scope", "I agree.",
                              [LARS_ID], {LARS_ID}) == ["antwortet auf Lars"]


@pytest.mark.parametrize("text, term", [
    ("We compared it with MolTrust.", "moltrust"),
    ("The AAE envelope binds the action.", "aae"),
    ("See draft-kroehl-agentic-trust-aae-02.", "draft-kroehl"),
])
def test_a_message_that_names_us_is_upgraded(lw, text, term):
    why = lw.upgrade_reasons("Gil Implementer", "Implementation input", text, [], set())
    assert why and term in why[0]


def test_an_ordinary_message_is_not_upgraded(lw):
    # Schweigeprobe: a reply to someone else, on a word that contains the
    # letters but is not the name ("aaes", "moltrusty" do not count), and
    # Lars only on Cc.
    assert lw.upgrade_reasons("Hal Other", "Re: Scope Focus on post-hoc auditing?",
                              "The aaes of moltrusty are out of scope.",
                              ["someone-else@x"], {LARS_ID}) == []


def test_an_address_in_a_cc_line_is_not_a_mention(lw):
    body = 'Cc: "Lars Kersten Kroehl"<lars@moltrust.ch>, "Abe Poster"<a@x.org>\nNew release.'
    assert lw.upgrade_reasons("Abe Poster", "Update", body, [], set()) == []
    assert lw.upgrade_reasons("Abe Poster", "Update", "See https://moltrust.ch/x", [], set())


def test_lars_own_post_is_never_upgraded(lw):
    assert lw.upgrade_reasons("Lars Kersten Kroehl", "Re: scope", "AAE does this.",
                              [LARS_ID], {LARS_ID}) == []


def test_a_mention_in_the_quoted_text_does_not_count(lw):
    body = lw.parse_body("<pre>Thanks.\n&gt; Lars wrote: see the AAE draft\n</pre>")
    assert lw.upgrade_reasons("Rae Quoter", "Re: x", body, [], set()) == []


def test_the_upgrade_kind_is_handeln_and_reaches_telegram():
    ein = ma.einordnen("listwatch-treffer", "Lars muss: entscheiden, ob er antwortet.")
    assert (ein.klasse, ein.zustellung) == (ma.HANDELN, ma.SOFORT)
    # the report itself stays filed; its class is not part of this job
    assert ma.einordnen("scout-bericht", "W3C ListWatch").klasse == ma.ABLAGE


# --- a whole run -----------------------------------------------------------------

def _run(lw, monkeypatch, index_now, pages, state=None, send=None):
    if state is not None:
        lw.STATE_FILE.write_text(json.dumps(state))
    monkeypatch.setattr(lw, "fetch", _archive(index_now, pages))
    calls = []

    def fake_send(text, **kw):
        calls.append((kw.get("art"), text))
        return send(kw.get("art")) if send else _filed_or_sent(lw, kw.get("art"))
    monkeypatch.setattr(lw.notify, "send_telegram", fake_send)
    rc = lw.main()
    return rc, calls


def _filed_or_sent(lw, art):
    lw.notify._LETZTER_GRUND = "ablage" if art == "scout-bericht" else ""
    return True


def test_a_reply_to_lars_is_sent_and_filed(lw, monkeypatch, caplog):
    # Lars' post 0001 is from an earlier run; 0002 answers it.
    idx = _w3c_index(("0001", "Use cases", "Lars Kersten Kroehl"),
                     ("0002", "Re: Use cases", "Toni Usecase"))
    pages = {"0001.html": _w3c_page(LARS_ID), "0002.html": _w3c_page("t@x", LARS_ID)}
    mon = lw.month_keys("w3c")[1]
    state = {"lists": {"public-agentprotocol": {mon: ["0001"]}}}
    rc, calls = _run(lw, monkeypatch, idx, pages, state)
    assert rc == 0
    assert [a for a, _ in calls] == ["listwatch-treffer", "scout-bericht"]
    upgrade, report = calls[0][1], calls[1][1]
    assert upgrade.startswith("Lars muss: entscheiden")
    assert "Toni Usecase (antwortet auf Lars)" in upgrade
    assert ">> HOCHGESTUFT: antwortet auf Lars" in report
    assert "upgrade sent (1 message(s))" in caplog.text
    assert "report filed, not sent (1 new, 0 not fetched)" in caplog.text
    saved = json.loads(lw.STATE_FILE.read_text())
    assert LARS_ID in saved["lars_msgids"]


def test_lars_post_and_its_reply_in_the_same_run(lw, monkeypatch):
    idx = _w3c_index(("0001", "Use cases", "Lars Kersten Kroehl"),
                     ("0002", "Re: Use cases", "Toni Usecase"))
    pages = {"0001.html": _w3c_page(LARS_ID), "0002.html": _w3c_page("t@x", LARS_ID)}
    rc, calls = _run(lw, monkeypatch, idx, pages, {"lists": {}})
    assert [a for a, _ in calls] == ["listwatch-treffer", "scout-bericht"]
    assert "Lars Kersten Kroehl" not in calls[0][1]


def test_nothing_that_concerns_us_files_only(lw, monkeypatch, caplog):
    idx = _w3c_index(("0003", "NTI Ecosystem Update", "Abe Poster"))
    pages = {"0003.html": _w3c_page("n@x", "other@x", "Federation audits.")}
    rc, calls = _run(lw, monkeypatch, idx, pages, {"lists": {}})
    assert rc == 0
    assert [a for a, _ in calls] == ["scout-bericht"]
    assert "HOCHGESTUFT" not in calls[0][1]
    assert "report filed, not sent" in caplog.text
    assert "report sent" not in caplog.text


def test_a_real_send_failure_reads_apart_from_a_filing(lw, monkeypatch, caplog):
    idx = _w3c_index(("0002", "Re: Use cases", "Toni Usecase"))
    pages = {"0002.html": _w3c_page("t@x", LARS_ID)}
    state = {"lists": {}, "lars_msgids": [LARS_ID]}

    def send(art):
        if art == "listwatch-treffer":
            lw.notify._LETZTER_GRUND = "kein token/chat"
            return False
        lw.notify._LETZTER_GRUND = "ablage"
        return True
    rc, calls = _run(lw, monkeypatch, idx, pages, state, send=send)
    assert rc == 1
    assert "upgrade not delivered: kein token/chat" in caplog.text
    assert "report filed, not sent" in caplog.text
    assert "state not advanced" in caplog.text
    assert "0002" not in json.dumps(json.loads(lw.STATE_FILE.read_text())["lists"])


def test_a_report_that_was_sent_says_sent(lw, monkeypatch, caplog):
    idx = _w3c_index(("0003", "Update", "Abe Poster"))
    pages = {"0003.html": _w3c_page("n@x")}

    def send(art):
        lw.notify._LETZTER_GRUND = ""
        return True
    _run(lw, monkeypatch, idx, pages, {"lists": {}}, send=send)
    assert "report sent (1 new, 0 not fetched)" in caplog.text


def test_the_skip_guard_counts_a_filed_report_as_a_closing_line():
    import listwatch_skip_check as G
    from datetime import datetime, timezone
    lines = ["[2026-11-08T11:00:01Z] INFO: W3C LISTWATCH — 7 list(s)\n",
             "[2026-11-08T11:00:02Z] WARNING: audit 2026-11: HTTP 429, skipping this month\n",
             "[2026-11-08T11:00:04Z] INFO: report filed, not sent (0 new, 1 not fetched)\n"]
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    assert G.silent_skips(lines, now, 48) == (0, 1)


# --- the reading list in the 08:00 bundle (Weg 2) -------------------------------

def _bundle(posted_texts):
    import datetime as dt
    from app import entscheidungen
    now = dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.timezone.utc)
    ok = entscheidungen.senden(now, send=lambda text, **kw: posted_texts.append(text) or True)
    return ok, entscheidungen


def test_a_run_queues_its_messages_for_the_bundle_upgraded_first(lw, monkeypatch, caplog):
    idx = _w3c_index(("0003", "NTI Ecosystem Update", "Abe Poster"),
                     ("0002", "Re: Use cases", "Toni Usecase"))
    pages = {"0003.html": _w3c_page("n@x", "other@x", "Federation audits."),
             "0002.html": _w3c_page("t@x", LARS_ID)}
    rc, _ = _run(lw, monkeypatch, idx, pages, {"lists": {}, "lars_msgids": [LARS_ID]})
    assert rc == 0
    assert "reading list queued for the 08:00 bundle (2 message(s))" in caplog.text
    sent = []
    ok, entscheidungen = _bundle(sent)
    assert ok is True and len(sent) == 1
    lines = sent[0].splitlines()
    assert lines[1].startswith("1. W3C/IETF-Listen: 2 neue Beiträge, 1 davon hochgestuft.")
    assert "Empfehlung: B" in lines[1]
    assert lines[2] == ("   ↑ [public-agentprotocol] Toni Usecase: Re: Use cases "
                        "(antwortet auf Lars)")
    assert lines[3] == "   · [public-agentprotocol] Abe Poster: NTI Ecosystem Update"
    assert entscheidungen.lesen() == []


def test_two_runs_reach_lars_as_one_point(lw, monkeypatch):
    idx1 = _w3c_index(("0003", "Update", "Abe Poster"))
    _run(lw, monkeypatch, idx1, {"0003.html": _w3c_page("n@x")}, {"lists": {}})
    idx2 = idx1 + _w3c_index(("0004", "On AAE envelopes", "Gil Implementer"))
    _run(lw, monkeypatch, idx2, {"0003.html": _w3c_page("n@x"),
                                 "0004.html": _w3c_page("g@x", "", "AAE binds it.")})
    sent = []
    _bundle(sent)
    lines = sent[0].splitlines()
    assert lines[0].startswith("Entscheidungen vom 09.11.: 1 Punkt.")
    assert lines[1].startswith("1. W3C/IETF-Listen: 2 neue Beiträge, 1 davon hochgestuft.")
    assert lines[2].startswith("   ↑ [public-agentprotocol] Gil Implementer")
    assert lines[3].startswith("   · [public-agentprotocol] Abe Poster")


def test_a_long_list_is_cut_and_says_where_the_rest_is():
    from app import entscheidungen as E
    liste = [{"zeile": f"[audit] Poster {i}: Subject {i}", "hoch": i == 30} for i in range(31)]
    assert E.leseliste_vormerken("listwatch-leseliste", "W3C/IETF-Listen", liste)
    text = E.buendel_text(E.zusammenfassen(E.lesen()), __import__("datetime").date(2026, 11, 9))
    lines = text.splitlines()
    assert lines[2].startswith("   ↑ [audit] Poster 30")
    assert len(lines) == 2 + E.LISTE_MAX + 1
    assert lines[-1] == "   … und 6 weitere, alle in der Ablage (Volltexte)."


def test_a_queue_that_cannot_be_written_holds_the_state(lw, monkeypatch, caplog):
    monkeypatch.setattr(lw.entscheidungen, "leseliste_vormerken", lambda *a, **k: False)
    idx = _w3c_index(("0003", "Update", "Abe Poster"))
    rc, _ = _run(lw, monkeypatch, idx, {"0003.html": _w3c_page("n@x")}, {"lists": {}})
    assert rc == 1
    assert "reading list not queued" in caplog.text
    assert "state not advanced" in caplog.text
