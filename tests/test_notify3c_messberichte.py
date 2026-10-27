"""ops NOTIFY-3c: measurement reports go into the daily report, not to Telegram.

Lars, 23.10.2026: measurement reports do not go to Telegram, not even as a
"report". Each sender that used the stats channel was classified on
27.10.2026, one sentence each:

- MESSBERICHT: numbers or a confirmation without an action. The call now files
  the text in app/tagesbericht_eingang; the selftest digest (08:00/20:00 UTC)
  carries it and empties the inbox once Telegram accepted the digest.
- BLEIBT: a message with an action, a deadline, money or an outage. It stays
  on Telegram unchanged (NOTIFY-3 criterion 5).

The ratchet works in three directions:

1. A file classified as pure measurement (NUR_MESSBERICHT) makes no Telegram
   call at all. Adding one turns the test red (NOTIFY-3 criterion 3).
2. Every remaining send on the stats channel sits in BLEIBT, with the number
   of send sites per function. A new stats send anywhere, or a second one in a
   function that has one, is red until somebody classifies it.
3. Every MESSBERICHT function files into the inbox. A function that stops
   doing so is red, so the table cannot claim a filing that no longer exists.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent

# The two reports themselves. They are the receivers, not senders to classify.
BERICHTE = {
    ("scripts/selftest.py", "send_digest"):
        "The selftest digest that carries the inbox (NOTIFY-1 criterion 6).",
    ("scripts/daily_report.py", "main"):
        "The daily conformance report of TB-1, plain words, one per day.",
}

# Measurement reports that now file into the inbox: (file, function) -> reason.
MESSBERICHT = {
    ("agents/board_digest.py", "run"):
        "Confirms the board post with an entry count; nothing to act on.",
    ("agents/proof_post.py", "run"):
        "Confirms that the weekly proof post went out; nothing to act on.",
    ("agents/reply_radar.py", "maybe_report_decisions"):
        "Counts of draft decisions at the first sample size.",
    ("agents/reply_radar.py", "filter_report"):
        "Tally of the radar filter with a sample of dropped posts.",
    ("agents/reply_radar.py", "report_counts"):
        "Counts of draft decisions on request.",
    ("agents/syndicate.py", "post_evergreen"):
        "Confirms an evergreen post with its links; nothing to act on.",
    ("agents/traffic_monitor.py", "file_report"):
        "New and recurring API callers, twice a day.",
    ("ops/cron/moltbook_stats_meldung.py", "main"):
        "Weekly Moltbook numbers.",
    ("scripts/digest_metrics.py", "write_reply_rows"):
        "Impressions and likes of tracked replies.",
    ("scripts/digest_metrics.py", "write_video_rows"):
        "Metrics of the video series.",
    ("scripts/digest_metrics.py", "compare_video_digest"):
        "Weekly comparison of video and digest reach.",
    ("scripts/digest_metrics.py", "write_digest_row"):
        "Metrics of the daily digest post.",
    ("scripts/evergreen_verify.py", "main"):
        "The check after an evergreen run when it found nothing; a broken "
        "cooldown still goes to Telegram.",
    ("scripts/finance_watch.py", "main"):
        "The monthly stats line; thresholds still go to the alerts channel.",
    ("scripts/funnel_diff.py", "telegram"):
        "Funnel comparison against a baseline, run by hand.",
    ("scripts/linkedin_auth_check.py", "main"):
        "Token status when it is healthy; a token that needs a hand still "
        "goes to Telegram.",
    ("scripts/milestone_trigger.py", "telegram"):
        "T-1 is a number nobody has to act on; T-2 and T-3 stay alerts.",
    ("scripts/onboarding_funnel_report.py", "main"):
        "Onboarding funnel of the past week.",
    ("scripts/post_video.py", "main"):
        "Confirms the Bluesky half of clip 1; the full clip post with its X "
        "cost stays on Telegram.",
    ("scripts/r2_settlement_watch.py", "main"):
        "The --always reading without a change; a change stays an alert.",
    ("scripts/radar_bottleneck.py", "main"):
        "Where the radar is bound, over 14 days.",
    ("scripts/reply_impact.py", "main"):
        "Effect of replies on followers and profile clicks.",
    ("scripts/revoke_inactive.py", "main"):
        "Dry-run count of revocation candidates; it changes nothing.",
    ("scripts/selfheal.py", "announce"):
        "A repair that worked; a failed one stays an alert.",
    ("scripts/sm_kpis.py", "run"):
        "Weekly social media KPIs.",
    ("scripts/supervision_report.py", "main"):
        "A supervision week without a red and without missing runs; such a "
        "week still goes to Telegram.",
    ("scripts/task_watch.py", "main"):
        "A changed count of qualified submissions; readiness and expiry stay "
        "alerts.",
    ("scripts/x_spend_after_radar.py", "report"):
        "X spend per week before and after the radar change.",
    ("scripts/x_spend_report.py", "report"):
        "X spend per day against the monthly target.",
    ("scripts/x_value_log.py", "main"):
        "Cost per follower and per registration, as quotients.",
    ("scripts/daily_stats.sh", "<shell>"):
        "Registrations and counts, twice a day.",
    ("scripts/weekly_traffic.sh", "<shell>"):
        "Weekly traffic from the nginx log.",
    ("scripts/bazaar_index_check.sh", "<shell>"):
        "Whether MolTrust is in the Bazaar index; a one-shot reading.",
}

# Files with no other Telegram message left. Any Telegram call here is red.
NUR_MESSBERICHT = {
    "agents/traffic_monitor.py",
    "ops/cron/moltbook_stats_meldung.py",
    "scripts/digest_metrics.py",
    "scripts/funnel_diff.py",
    "scripts/onboarding_funnel_report.py",
    "scripts/radar_bottleneck.py",
    "scripts/reply_impact.py",
    "scripts/sm_kpis.py",
    "scripts/x_spend_after_radar.py",
    "scripts/x_spend_report.py",
    "scripts/x_value_log.py",
    "scripts/daily_stats.sh",
    "scripts/weekly_traffic.sh",
    "scripts/bazaar_index_check.sh",
}

# Sends on the stats channel that stay: (file, function) -> (count, reason).
BLEIBT = {
    ("agent/ambassador.py", "check_milestones"): (
        1, "Asks Lars whether to post a manual X draft for the milestone."),
    ("agents/moltbook_drafts.py", "offer"): (
        1, "A reply draft with Send and Drop buttons; Lars decides."),
    ("agents/reply_radar.py", "report_reads_paused"): (
        1, "Outage: the source breaker is closed and the radar reads nothing."),
    ("agents/reply_radar.py", "send_draft"): (
        1, "A reply draft with decision buttons; Lars decides."),
    ("agents/reply_radar.py", "reading_list_run"): (
        1, "The reading list Lars replies from by hand."),
    ("agents/syndicate.py", "hold_unarmed"): (
        1, "A draft that was not posted and is handed to Lars to post."),
    ("agents/syndicate.py", "note_block"): (
        1, "A post dropped from the evergreen queue; it only returns by hand."),
    ("agents/syndicate.py", "deliver_linkedin"): (
        2, "A LinkedIn draft with a Post button, or one the gate blocked."),
    ("agents/syndicate.py", "post_evergreen"): (
        1, "An evergreen post the gate blocked."),
    ("ops/cron/moltbook_writers_wache.py", "main"): (
        2, "An unexplained Moltbook writer, or the check could not run."),
    ("scripts/discovery_snapshot.py", "telegram_alert"): (
        1, "Only failures: the upsert failed or the snapshot is not ok."),
    ("scripts/evergreen_verify.py", "main"): (
        1, "Only when the cooldown is broken or more than one post went out."),
    ("scripts/linkedin_auth_check.py", "main"): (
        3, "Re-authorisation by hand, a failed renewal, or an unhealthy token."),
    ("scripts/linkedin_metrics.py", "main"): (
        1, "Asks Lars to enter the LinkedIn numbers from the panel."),
    ("scripts/post_video.py", "post_to_bluesky"): (
        1, "A posted video that is not in the feed; Lars decides."),
    ("scripts/post_video.py", "main"): (
        1, "Clip posted, with its X cost and the LinkedIn step by hand."),
    ("scripts/runden_auswertung.py", "main"): (
        1, "Deadline: warning 24 hours before a round expires."),
    ("scripts/supervision_report.py", "main"): (
        1, "Only a week with a red or with missing runs."),
}

AUSGESCHLOSSEN = ("venv", "node_modules", "site-packages", "build", "tests")


def _dateien(suffix: str):
    for p in WURZEL.rglob(f"*{suffix}"):
        rel = p.relative_to(WURZEL)
        if any(t.startswith(".") for t in rel.parts):
            continue
        if any(t in rel.parts for t in AUSGESCHLOSSEN):
            continue
        yield p, str(rel)


def _aufrufe(baum: ast.AST):
    """Every call with the name of the function it sits in."""
    out = []

    def besuche(knoten, name):
        for kind in ast.iter_child_nodes(knoten):
            if isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef)):
                besuche(kind, kind.name)
                continue
            if isinstance(kind, ast.Call):
                out.append((name, kind))
            besuche(kind, name)

    besuche(baum, "<modul>")
    return out


def _name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _ist_stats(ausdruck: str) -> bool:
    return "STATS" in ausdruck or re.search(r"""['"]stats['"]""", ausdruck) is not None


def stats_sendungen(quelltext: str) -> list:
    """(function, line) of every send on the stats channel in one source.

    A send is any call with a `channel=` keyword that names the stats channel,
    or a call to a local wrapper whose `channel` default is the stats channel
    and that is called without one.
    """
    baum = ast.parse(quelltext)
    vorgaben = {}
    for k in ast.walk(baum):
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg, d in zip(k.args.kwonlyargs, k.args.kw_defaults):
                if arg.arg == "channel" and d is not None:
                    vorgaben[k.name] = ast.unparse(d)
    treffer = []
    for funktion, call in _aufrufe(baum):
        kanal = [ast.unparse(kw.value) for kw in call.keywords if kw.arg == "channel"]
        if not kanal and isinstance(call.func, ast.Name) and _name(call) in vorgaben:
            kanal = [vorgaben[_name(call)]]
        if kanal and _ist_stats(kanal[0]):
            treffer.append((funktion, call.lineno))
    return treffer


TELEGRAM = {"send_telegram", "send_telegram_message", "send_befunde",
            "send_telegram_alert"}


def telegram_aufrufe(quelltext: str) -> list:
    """Every call that can reach Telegram: a send function or a channel keyword."""
    out = []
    for funktion, call in _aufrufe(ast.parse(quelltext)):
        if _name(call) in TELEGRAM or any(kw.arg == "channel" for kw in call.keywords):
            out.append((funktion, call.lineno))
    return out


def _shell_stats(text: str) -> bool:
    return re.search(r"--channel\s+[\"']?stats|melden\s+stats", text) is not None


def _alle_stats_sendungen() -> dict:
    zaehler: dict = {}
    for p, rel in _dateien(".py"):
        try:
            text = p.read_text(encoding="utf-8")
            treffer = stats_sendungen(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for funktion, _ in treffer:
            zaehler[(rel, funktion)] = zaehler.get((rel, funktion), 0) + 1
    for p, rel in _dateien(".sh"):
        if _shell_stats(p.read_text(encoding="utf-8", errors="replace")):
            zaehler[(rel, "<shell>")] = zaehler.get((rel, "<shell>"), 0) + 1
    return zaehler


# --- the ratchet --------------------------------------------------------------

def test_every_stats_send_is_classified():
    """Direction 2: a stats send outside BLEIBT, or one more than counted, is red."""
    ist = _alle_stats_sendungen()
    erlaubt = {k: n for k, (n, _) in BLEIBT.items()}
    erlaubt.update({k: 1 for k in BERICHTE})
    zu_viel = {k: n for k, n in ist.items() if n > erlaubt.get(k, 0)}
    assert zu_viel == {}, (
        "send on the stats channel that is not classified: "
        + ", ".join(f"{f} {fn} ({n}x)" for (f, fn), n in sorted(zu_viel.items()))
        + " — a measurement goes to app.tagesbericht_eingang.ablegen; a message "
          "with an action, a deadline, money or an outage goes into BLEIBT with a reason")


def test_bleibt_lists_only_what_still_sends():
    """A classified send that disappeared is struck from the list."""
    ist = _alle_stats_sendungen()
    weg = {k: n for k, (n, _) in BLEIBT.items() if ist.get(k, 0) != n}
    assert weg == {}, f"BLEIBT no longer matches the code: {weg} (found {ist})"


@pytest.mark.parametrize("datei", sorted(NUR_MESSBERICHT))
def test_a_measurement_file_makes_no_telegram_call(datei):
    """Direction 1, NOTIFY-3 criterion 3."""
    text = (WURZEL / datei).read_text(encoding="utf-8")
    if datei.endswith(".sh"):
        assert "app.notify" not in text and "api.telegram.org" not in text, \
            f"{datei} is classified as a measurement and calls Telegram"
        return
    assert telegram_aufrufe(text) == [], \
        f"{datei} is classified as a measurement and calls Telegram"


@pytest.mark.parametrize("datei,funktion", sorted(MESSBERICHT))
def test_a_measurement_function_files_into_the_inbox(datei, funktion):
    """Direction 3: the table names a filing that the code really does."""
    text = (WURZEL / datei).read_text(encoding="utf-8")
    if funktion == "<shell>":
        assert "app.tagesbericht_eingang" in text
        assert not _shell_stats(text)
        return
    ablagen = [fn for fn, call in _aufrufe(ast.parse(text)) if _name(call) == "ablegen"]
    assert funktion in ablagen, f"{datei} {funktion} does not file into the inbox"


def test_every_classification_has_a_sentence():
    for k, satz in [*MESSBERICHT.items(), *BERICHTE.items(),
                    *((k, s) for k, (_, s) in BLEIBT.items())]:
        assert satz.strip().endswith(".") and len(satz) > 20, k
        assert (WURZEL / k[0]).exists(), k
    assert not set(MESSBERICHT) & set(BLEIBT) - {
        # Mixed functions: the measurement branch files, the finding branch sends.
        ("agents/syndicate.py", "post_evergreen"),
        ("scripts/evergreen_verify.py", "main"),
        ("scripts/linkedin_auth_check.py", "main"),
        ("scripts/post_video.py", "main"),
        ("scripts/supervision_report.py", "main"),
    }
    for datei in NUR_MESSBERICHT:
        assert any(d == datei for d, _ in MESSBERICHT), datei


# The ratchet itself, on a source that does what it must not.

def test_the_ratchet_sees_a_telegram_call_in_a_measurement_file():
    quelle = ("from app import notify\n"
              "def run():\n"
              "    notify.send_telegram('42 rows', channel=notify.STATS)\n")
    assert telegram_aufrufe(quelle) == [("run", 3)]
    assert stats_sendungen(quelle) == [("run", 3)]


def test_the_ratchet_sees_a_wrapper_with_a_stats_default():
    quelle = ("from app import notify\n"
              "def send(text, *, channel=notify.STATS):\n"
              "    return notify.send_telegram(text, channel=channel)\n"
              "def main():\n"
              "    send('report')\n")
    assert stats_sendungen(quelle) == [("main", 5)]


def test_the_ratchet_stays_quiet_on_a_filing():
    quelle = ("from app import tagesbericht_eingang\n"
              "def run():\n"
              "    tagesbericht_eingang.ablegen('x', '42 rows')\n")
    assert telegram_aufrufe(quelle) == []
    assert stats_sendungen(quelle) == []


# --- the mixed senders: measurement files, finding sends ---------------------

@pytest.fixture()
def eingang(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(tmp_path / "selftest"))
    from app import tagesbericht_eingang
    return tagesbericht_eingang


@pytest.fixture()
def telegram(monkeypatch):
    from app import notify
    gesendet = []
    monkeypatch.setattr(notify, "send_telegram",
                        lambda text, **kw: gesendet.append((kw.get("channel"), text)) or True)
    return gesendet


def _laden(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, WURZEL / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_milestone_t1_files_and_t2_alerts(eingang, telegram):
    mt = _laden("scripts/milestone_trigger.py", "mt_notify3c")
    mt.telegram("T-1: 90 organic", alerts=False)
    assert telegram == []
    assert [e["quelle"] for e in eingang.lesen()] == ["milestone_trigger"]
    mt.telegram("T-2: 100 organic", alerts=True)
    assert telegram == [("alerts", "T-2: 100 organic")]


def test_selfheal_files_a_repair_that_worked_and_alerts_a_failed_one(eingang, telegram):
    sh = _laden("scripts/selfheal.py", "sh_notify3c")
    sh.announce([{"ok": True, "action": "restart", "check": "service/x",
                  "detail": "inactive", "result": "active &lt;ok&gt;"}], dry=False)
    assert telegram == []
    [e] = eingang.lesen()
    assert e["quelle"] == "selfheal" and "<b>" not in e["text"]
    assert "Ergebnis: active <ok>" in e["text"]  # entities resolved, text kept
    sh.announce([{"ok": False, "action": "restart", "check": "service/x",
                  "detail": "inactive", "result": "failed"}], dry=False)
    assert [c for c, _ in telegram] == ["alerts"]


@pytest.mark.parametrize("unlocked,befund", [(False, False), (True, True)])
def test_evergreen_verify_sends_only_a_finding(eingang, telegram, monkeypatch,
                                               unlocked, befund):
    ev = _laden("scripts/evergreen_verify.py", "ev_notify3c")
    monkeypatch.setattr(ev, "check", lambda day=None: {"unlocked": unlocked,
                                                       "posted_today": ["a"]})
    monkeypatch.setattr(ev, "format_report", lambda k: "<b>Evergreen</b> report")
    assert ev.main(["--send"]) == (1 if befund else 0)
    if befund:
        assert [c for c, _ in telegram] == ["stats"]
        assert eingang.lesen() == []
    else:
        assert telegram == []
        assert [e["text"] for e in eingang.lesen()] == ["Evergreen report"]


@pytest.mark.parametrize("red,befund", [([], False), (["x"], True)])
def test_supervision_report_sends_only_a_week_with_a_finding(eingang, telegram,
                                                             monkeypatch, red, befund):
    sr = _laden("scripts/supervision_report.py", "sr_notify3c")
    monkeypatch.setattr(sr, "collect", lambda days: {
        "red": red, "broken": [], "runs": 100, "expected_runs": 100})
    monkeypatch.setattr(sr, "format_report", lambda k: "week")
    assert sr.main(["--send"]) == (1 if befund else 0)
    assert bool(telegram) is befund
    assert bool(eingang.lesen()) is not befund
