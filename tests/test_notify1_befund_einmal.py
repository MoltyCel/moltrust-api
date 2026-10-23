"""A finding goes out once, and again only when it changes (ops NOTIFY-1).

Criterion 2, one case each: first occurrence, unchanged, changed, gone and
back. Replayed against the case that started the job: the web-root finding on
blog/index.html, every five minutes, same file, same hash — on 21.10.2026 it
reached the channel at 09:00, 11:00, 13:00 and 15:00Z.
"""
import ast
import datetime as dt
import pathlib

from app import notify as n

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
ROOT = pathlib.Path(__file__).resolve().parents[1]

WEBROOT = ("Web-Root: 1 Schreibvorgang/-vorgaenge ausserhalb deploy.sh in /var/www/html\n"
           "  blog/index.html weicht von der Generatorausgabe ab (erwartet sha256 "
           "{erwartet}…/48211 B, geliefert {geliefert}…/48190 B)")


def _text(geliefert="a1b2c3d4e5f6", erwartet="0f9e8d7c6b5a"):
    return WEBROOT.format(geliefert=geliefert, erwartet=erwartet)


def _runs(path, texts, start=T0, every=dt.timedelta(minutes=5)):
    """texts: one entry per run, None for a run without the finding."""
    sent = []
    for i, text in enumerate(texts):
        if text is not None and n.throttle(text, start + every * i, path)[0]:
            sent.append(i)
    return sent


def test_first_occurrence_is_delivered(tmp_path):
    assert n.throttle(_text(), T0, str(tmp_path / "fp.json"))[0] is True


def test_unchanged_is_not_delivered_again_however_often(tmp_path):
    """The 21.10. case: 24 hours at five minutes, 288 runs, one delivery."""
    assert _runs(str(tmp_path / "fp.json"), [_text()] * 288) == [0]


def test_a_changed_hash_is_a_new_finding(tmp_path):
    """The hash stays in the fingerprint. Before NOTIFY-1 the "Git-Hash"
    pattern removed the twelve characters, and a changed file looked like the
    old one."""
    assert n.fingerprint(_text("a1b2c3d4e5f6")) != n.fingerprint(_text("ffffffffffff"))
    runs = [_text("a1b2c3d4e5f6")] * 5 + [_text("ffffffffffff")] * 5
    assert _runs(str(tmp_path / "fp.json"), runs) == [0, 5]


def test_a_changed_path_or_check_is_a_new_finding():
    assert n.fingerprint(_text()) != n.fingerprint(_text().replace("blog/index.html",
                                                                  "index.html"))
    assert n.fingerprint("[FAIL] a-track-record-burst — 1") != \
        n.fingerprint("[FAIL] c-checkout-clean — 1")


def test_gone_and_back_is_new(tmp_path):
    """Three runs present, four runs absent (20 minutes against a five-minute
    cadence), then back: delivered again."""
    runs = [_text()] * 3 + [None] * 4 + [_text()] * 3
    assert _runs(str(tmp_path / "fp.json"), runs) == [0, 7]


def test_one_missed_run_does_not_count_as_gone(tmp_path):
    """Silence check: a watch that skips a single run has not lost the finding."""
    runs = [_text()] * 3 + [None] + [_text()] * 3
    assert _runs(str(tmp_path / "fp.json"), runs) == [0]


def test_an_hourly_finding_is_gone_after_three_hours(tmp_path):
    hourly = dt.timedelta(hours=1)
    p = str(tmp_path / "fp.json")
    assert _runs(p, ["[FAIL] x — 1"] * 3 + [None] * 2 + ["[FAIL] x — 1"], every=hourly) == [0]
    p2 = str(tmp_path / "fp2.json")
    assert _runs(p2, ["[FAIL] x — 1"] * 3 + [None] * 3 + ["[FAIL] x — 1"], every=hourly) == [0, 6]


# ── criterion 6: the daily report is the one sender outside the throttle ─────

def test_drosseln_false_delivers_the_same_text_again(monkeypatch, tmp_path):
    monkeypatch.setattr(n, "FINGERPRINTS", str(tmp_path / "fp.json"))
    monkeypatch.setattr(n, "SENT_LOG", str(tmp_path / "sent.jsonl"))
    monkeypatch.setattr(n, "telegram_allowed", lambda *a, **k: True)
    monkeypatch.setattr(n, "_resolve", lambda name: "x")
    monkeypatch.setattr(n, "chat_id_for", lambda channel: "1")
    posted = []

    class _R:
        status_code = 200
        content = b""

        def json(self):
            return {"result": {"message_id": 1}}

    monkeypatch.setattr(n.requests, "post", lambda *a, **k: posted.append(1) or _R())
    assert n.send_telegram("Tagesbericht 0 neu", channel=n.STATS) is True
    assert n.send_telegram("Tagesbericht 0 neu", channel=n.STATS) is False
    assert n.send_telegram("Tagesbericht 0 neu", channel=n.STATS, drosseln=False) is True
    assert len(posted) == 2


def test_only_the_daily_report_may_bypass_the_throttle():
    """Ratchet: `drosseln=False` is allowed in scripts/selftest.py only (the
    daily report). Today no file uses it; the selftest call follows once
    moltrust-api#894 has released the file."""
    allowed = {"scripts/selftest.py"}
    found = set()
    for f in ROOT.rglob("*.py"):
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "venv/", ".venv/")) or "/node_modules/" in rel:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "drosseln" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is False:
                        found.add(rel)
    assert found <= allowed, sorted(found - allowed)
