"""Telegram only when Lars has to act (ops NOTIFY-4, MELD-1).

Origin: Lars, 27./28.10.2026. The 28.10. digest opened with "Lars muss: nichts"
and carried about fifty lines after it. Over seven days (31.10.-07.11.2026)
the send log held 187 lines; the USDC poller alone delivered 19 messages on
06./07.11. without anyone having anything to do.

Three things are held here:

  * the ratchet (MELD-1 criterion 4): every send call in the repository names
    its kind with `art=`, and the kind is in app/meldungsarten.py. A new call
    without one is red;
  * the table (NOTIFY-4 criterion 2) is complete and the doc file equals it;
  * the behaviour (NOTIFY-4 criteria 3, 4, 6; MELD-1 criterion 3): ABLAGE makes
    no Telegram call and one section in the filing; HANDELN goes out with at
    most four lines; a decision goes into the one message of the day.

Every check that finds something has a counter-check that must stay quiet.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import pathlib
import re

import pytest

from app import ablage, entscheidungen, meldungsarten as MA, notify

REPO = pathlib.Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("app", "agents", "agent", "scripts", "monitor", "workers", "lib", "ops")
SKIP_PARTS = {"venv", "node_modules", "__pycache__", ".git", "tests"}
NOTIFY_FUNCS = {"send_telegram", "send_telegram_message", "send_befunde"}

# Files that call Telegram without app/notify.py and are listed, with a reason,
# in tests/test_telegram_eine_sendestelle.py (AUSGENOMMEN). They send no
# message through notify, so they carry no art.
OHNE_NOTIFY = {"agents/herald.py"}


# --------------------------------------------------------------------- table

def test_every_kind_has_one_class_and_a_reason():
    for name, a in MA.ARTEN.items():
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name), name
        assert a.klasse in MA.KLASSEN, name
        assert len(a.grund.strip()) > 20, f"{name}: reason missing"
        if a.klasse == MA.HANDELN:
            assert a.zustellung in (MA.SOFORT, MA.BUENDEL), name
        else:
            assert not a.zustellung and not a.ausnahme, name
        assert a.ausnahme in ("",) + MA.AUSNAHMEN, name


def test_the_kinds_named_in_criterion_2_are_filed():
    """Digest, selftest state, deploys, nginx, reply radar, DSGVO, measurements:
    ABLAGE unless they carry a step (then the first line says so)."""
    for name in ("selftest-befund", "deploy", "dsgvo-retention", "messbericht",
                 "watchdog-befund", "auto-repair", "usdc-eingang", "pipeline-bericht"):
        assert MA.ARTEN[name].klasse == MA.ABLAGE, name
    for name in ("selftest-sammelmeldung", "tagesbericht"):
        assert MA.einordnen(name, notify.KEIN_HANDGRIFF + "\nx").klasse == MA.ABLAGE
        assert MA.einordnen(name, "Lars muss: Token erneuern\nx").klasse == MA.HANDELN


def test_no_step_line_matches_notify():
    assert MA._KEIN_HANDGRIFF == notify.KEIN_HANDGRIFF


def test_doc_file_equals_the_table():
    doc = (REPO / "docs/ops/meldungsarten.md").read_text(encoding="utf-8")
    assert doc == MA.markdown(), (
        "docs/ops/meldungsarten.md is stale: python -m app.meldungsarten > docs/ops/meldungsarten.md")


def test_unknown_kind_is_delivered_and_marked():
    e = MA.einordnen(None, "something nobody classified")
    assert (e.art, e.klasse) == (MA.UNBEKANNT, MA.HANDELN)
    e = MA.einordnen("no-such-kind", "x")
    assert e.art == MA.UNBEKANNT


def test_patterns_catch_the_senders_outside_the_repo():
    assert MA.einordnen(None, "Claude ist gerade nicht erreichbar. Das Gateway ...").art \
        == "llm-gateway-ausfall"
    assert MA.einordnen(None, "MolTrust deploy FAILED — moltrust-api").klasse == MA.ABLAGE
    # Counter-check: "Lars muss: nichts" is not a step.
    assert MA.einordnen(None, "Lars muss: nichts\nFoo").klasse == MA.ABLAGE


# ------------------------------------------------------------------- ratchet

def _files(ext):
    for d in SOURCE_DIRS:
        root = REPO / d
        if not root.is_dir():
            continue
        for p in root.rglob(f"*{ext}"):
            rel = p.relative_to(REPO)
            if set(rel.parts) & SKIP_PARTS or ".bak" in p.name:
                continue
            if any((REPO / pathlib.Path(*rel.parts[:i]) / ".git").exists()
                   for i in range(1, len(rel.parts))):
                continue
            yield str(rel), p


def _is_notify(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr in NOTIFY_FUNCS
            and isinstance(node.value, ast.Name) and node.value.id == "notify")


def _art_ok(value, params) -> bool:
    if isinstance(value, ast.Constant):
        return value.value in MA.ARTEN
    if isinstance(value, ast.IfExp):
        return _art_ok(value.body, params) and _art_ok(value.orelse, params)
    if isinstance(value, ast.Name):
        return value.id in params
    return False


def _own_nodes(f):
    """The nodes of a function without those of functions nested in it."""
    todo = list(ast.iter_child_nodes(f))
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield n
        todo.extend(ast.iter_child_nodes(n))


def _wrappers(tree) -> set:
    """Functions that hand their own `art` parameter to notify."""
    out = set()
    for f in ast.walk(tree):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for c in _own_nodes(f):
            if isinstance(c, ast.Call) and (_is_notify(c.func) or (
                    c.args and _is_notify(c.args[0]))):
                if any(k.arg == "art" and isinstance(k.value, ast.Name)
                       and k.value.id == "art" for k in c.keywords):
                    out.add(f.name)
    return out


def _all_wrappers() -> dict:
    out = {}
    for rel, p in _files(".py"):
        try:
            out[rel] = _wrappers(ast.parse(p.read_text(encoding="utf-8")))
        except SyntaxError:
            continue
    return out


def _module_file(mod: str, here: str) -> str:
    if mod.startswith("."):
        base = pathlib.Path(here).parent
        mod = mod.lstrip(".")
        return str(base / (mod.replace(".", "/") + ".py")) if mod else str(base)
    return mod.replace(".", "/") + ".py"


def _send_calls(rel: str, tree, wrappers: dict):
    """(call, params of the enclosing function) for every call that can send."""
    local = set(wrappers.get(rel, set()))
    attr = {}  # alias -> wrapper names of that module
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            src = _module_file(("." * n.level) + (n.module or ""), rel)
            for a in n.names:
                if a.name in wrappers.get(src, set()):
                    local.add(a.asname or a.name)
                sub = (src.rstrip("/") + "/" + a.name + ".py") if n.module is None or n.level else \
                    _module_file(f"{n.module}.{a.name}", rel)
                if wrappers.get(sub):
                    attr[a.asname or a.name] = wrappers[sub]
    funcs = [f for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for c in ast.walk(tree):
        if not isinstance(c, ast.Call):
            continue
        fn = c.func
        hit = (_is_notify(fn)
               or (c.args and _is_notify(c.args[0]))
               or (isinstance(fn, ast.Name) and fn.id in local)
               or (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                   and fn.attr in attr.get(fn.value.id, set()))
               or any(k.arg == "channel" for k in c.keywords))
        if not hit:
            continue
        enc = [f for f in funcs if f.lineno <= c.lineno <= (f.end_lineno or f.lineno)]
        params = set()
        if enc:
            a = enc[-1].args
            params = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
        yield c, params


def ratchet_offenders() -> list:
    wrappers = _all_wrappers()
    out = []
    for rel, p in _files(".py"):
        if rel == "app/notify.py" or rel in OHNE_NOTIFY:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for c, params in _send_calls(rel, tree, wrappers):
            art = [k.value for k in c.keywords if k.arg == "art"]
            if not art or not _art_ok(art[0], params):
                out.append(f"{rel}:{c.lineno}")
    return out


def test_every_send_call_names_a_known_kind():
    """MELD-1 criterion 4: a send call without a classification is red."""
    off = ratchet_offenders()
    assert off == [], (
        "send call without art= or with an unknown art: " + ", ".join(off)
        + " — name the kind (app/meldungsarten.py) at the call site")


def test_ratchet_catches_a_new_call(tmp_path, monkeypatch):
    """Counter-check for the ratchet: a call without art is found, one with art is not."""
    bad = ast.parse("from app import notify\nnotify.send_telegram('x', channel=notify.STATS)\n")
    good = ast.parse("from app import notify\n"
                     "notify.send_telegram('x', channel=notify.STATS, art='deploy')\n")
    wrong = ast.parse("from app import notify\n"
                      "notify.send_telegram('x', channel=notify.STATS, art='erfunden')\n")
    wrap = ast.parse("from app import notify\n"
                     "def tg(t, *, art):\n    notify.send_telegram(t, channel=notify.STATS, art=art)\n"
                     "tg('a')\ntg('b', art='deploy')\n")

    def count(tree, rel="x.py"):
        w = {rel: _wrappers(tree)}
        return sum(1 for c, params in _send_calls(rel, tree, w)
                   if not ([k for k in c.keywords if k.arg == "art"]
                           and _art_ok([k.value for k in c.keywords if k.arg == "art"][0], params)))

    assert count(bad) == 1
    assert count(good) == 0
    assert count(wrong) == 1
    assert count(wrap) == 1          # tg('a') only


def test_every_shell_sender_names_a_known_kind():
    off = []
    for rel, p in list(_files(".sh")):
        text = p.read_text(encoding="utf-8", errors="replace").replace("\\\n", " ")
        for m in re.finditer(r"-m app\.notify\b[^\n]*", text):
            a = re.search(r"--art\s+([a-z0-9-]+)", m.group(0))
            if not a or a.group(1) not in MA.ARTEN:
                off.append(rel)
    assert off == [], f"shell sender without a known --art: {off}"


# ------------------------------------------------------------------ behaviour

@pytest.fixture
def posted(monkeypatch, tmp_path):
    """notify with the gate open, a token, and requests.post recorded."""
    monkeypatch.setenv("MOLTRUST_NOTIFY", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100test")
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MOLTRUST_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(notify, "SENT_LOG", str(tmp_path / "state" / "sent.jsonl"))
    monkeypatch.setattr(notify, "FINGERPRINTS", str(tmp_path / "state" / "fp.json"))
    notify._ENV_CACHE.clear()
    calls = []

    class R:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"result": {"message_id": 41}}

    def post(url, data=None, timeout=None):
        calls.append(data)
        return R()

    monkeypatch.setattr(notify.requests, "post", post)
    return calls


def _filing(tmp_path) -> str:
    files = list((tmp_path / "reports").glob("*.md"))
    return files[0].read_text(encoding="utf-8") if files else ""


def _log(tmp_path) -> list:
    p = tmp_path / "state" / "sent.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_ablage_makes_no_telegram_call_and_one_filing_line(posted, tmp_path):
    """Criterion 6, first half."""
    assert notify.send_telegram("Auto-Repair Candidates\n/a -> /b", channel=notify.ALERTS,
                                art="auto-repair") is True
    assert posted == []
    text = _filing(tmp_path)
    assert text.count("· auto-repair · alerts") == 1
    assert "/a -> /b" in text
    line = _log(tmp_path)[-1]
    assert (line["grund"], line["art"], line["klasse"], line["erfolg"]) == \
        ("ablage", "auto-repair", "ABLAGE", False)


def test_handeln_goes_out_with_at_most_four_lines(posted, tmp_path):
    """Criterion 6, second half; criterion 4."""
    assert notify.send_telegram("Erneuern\nwarum\nSchritt: https://x\nbis 08.11.",
                                channel=notify.STATS, art="zugang-abgelaufen") is True
    assert len(posted) == 1
    assert len(notify.handeln_zeilen(posted[0]["text"])) <= 4
    assert _filing(tmp_path) == ""
    line = _log(tmp_path)[-1]
    assert (line["art"], line["klasse"], line["erfolg"]) == ("zugang-abgelaufen", "HANDELN", True)


def test_a_long_handeln_message_is_cut_and_filed_in_full(posted, tmp_path):
    long = "\n".join(f"Zeile {i}" for i in range(1, 12))
    notify.send_telegram(long, channel=notify.ALERTS, art="endpunkt-ausfall")
    sent = posted[0]["text"]
    assert len(notify.handeln_zeilen(sent)) == 4
    assert sent.startswith("Zeile 1\nZeile 2\nZeile 3\n")
    assert "Tagesablage" in sent
    assert "Zeile 11" in _filing(tmp_path)


def test_a_copy_block_counts_as_one_line():
    text = "Was\n<pre>eins\nzwei\ndrei\nvier</pre>\nSchritt\nFrist"
    assert len(notify.handeln_zeilen(text)) == 4
    assert notify.kurz_fassen(text) == (text, None)


def test_no_step_digest_is_filed_not_sent(posted, tmp_path):
    """Criterion 3: the digest is dropped from Telegram when it says "nichts"."""
    ok = notify.send_telegram(notify.KEIN_HANDGRIFF + "\nSelbsttest 07.11.", channel=notify.STATS,
                              drosseln=False, art="selftest-sammelmeldung")
    assert ok is True and posted == []
    assert "Selbsttest 07.11." in _filing(tmp_path)


def test_filing_happens_with_the_gate_closed(posted, tmp_path, monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY", "0")
    notify._ENV_CACHE.clear()
    assert notify.send_telegram("x", channel=notify.STATS, art="messbericht") is True
    assert "x" in _filing(tmp_path)


def test_a_filing_that_cannot_be_written_is_not_handled(posted, tmp_path, monkeypatch):
    monkeypatch.setenv("MOLTRUST_REPORTS_DIR", "/nonexistent/forbidden")
    assert notify.send_telegram("x", channel=notify.STATS, art="messbericht") is False
    assert posted == []


def test_a_standing_finding_is_filed_once(posted, tmp_path):
    for _ in range(3):
        notify.send_telegram("[WARN] c-x — same", channel=notify.ALERTS, art="selftest-befund")
    assert _filing(tmp_path).count("[WARN] c-x") == 1
    assert [x["grund"] for x in _log(tmp_path)] == ["ablage", "gedrosselt", "gedrosselt"]


def test_send_befunde_files_selftest_findings(posted, tmp_path):
    r = notify.send_befunde([("c-a", "[FAIL] c-a — weg")], channel=notify.ALERTS,
                            kopf="MolTrust Selftest —", art="selftest-befund")
    assert r["gesendet"] == ["c-a"] and r["erfolg"] is True
    assert posted == []
    assert "[FAIL] c-a" in _filing(tmp_path)


def test_cli_files_with_art(posted, tmp_path, capsys):
    assert notify._cli(["--channel", "alerts", "--text", "MolTrust deploy ok", "--art", "deploy"]) == 0
    assert posted == []


# --------------------------------------------------------- decisions (MELD-1 c3)

def test_a_decision_is_queued_not_sent(posted, tmp_path):
    notify.send_telegram("Reseller-Antrag von X. Firma Y. Bitte pruefen.", channel=notify.ALERTS,
                         art="reseller-antrag")
    assert posted == []
    q = entscheidungen.lesen()
    assert len(q) == 1 and q[0]["art"] == "reseller-antrag"
    assert q[0]["punkt"] == "Reseller-Antrag von X. Firma Y."


def test_the_bundle_is_one_message_with_format_and_way(posted, tmp_path):
    entscheidungen.vormerken("discovery-thread", "Thread a: Satz eins. Satz zwei. Satz drei.")
    entscheidungen.vormerken("content-scout-lead", "Lead 7 in b. Punkt.", empfehlung="B",
                             optionen={"A": "kommentieren", "B": "verwerfen"})
    now = dt.datetime(2026, 11, 8, 8, 0, tzinfo=dt.timezone.utc)
    assert entscheidungen.senden(now) is True
    assert len(posted) == 1
    text = posted[0]["text"]
    lines = text.splitlines()
    assert lines[0].startswith("Entscheidungen vom 08.11.: 2 Punkte.")
    assert "Antwort im Console-Tab" in lines[0] and "„1A 2A“" in lines[0]
    assert lines[1] == "1. Thread a: Satz eins. Satz zwei. Empfehlung: A. " \
                       "(A ein Console-Tab übernimmt, B liegen lassen)"
    assert lines[2].endswith("Empfehlung: B. (A kommentieren, B verwerfen)")
    assert entscheidungen.lesen() == []
    assert "Satz drei" in _filing(tmp_path)        # full text filed


def test_no_bundle_without_decisions(posted):
    assert entscheidungen.senden() is None
    assert posted == []


def test_an_undelivered_bundle_keeps_its_points(posted, tmp_path):
    entscheidungen.vormerken("discovery-thread", "Thread a: x.")
    assert entscheidungen.senden(send=lambda *a, **k: False) is False
    assert len(entscheidungen.lesen()) == 1
