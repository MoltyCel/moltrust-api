"""Two ways a Telegram send goes wrong quietly, and the guards against them.

Both were observed in production, not imagined: watchdog.log carried the bot
token 220 times, and auto_repair lost 23 messages to a parse error it reported
only as a failure to send.
"""
import logging
import re

import pytest

from app import notify


# ── Token redaction ──

@pytest.fixture
def captured():
    """A logger whose output we can read back."""
    stream = logging.StreamHandler()
    import io
    buf = io.StringIO()
    stream.stream = buf
    stream.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger(f"probe.{id(buf)}")
    log.handlers = [stream]
    log.setLevel(logging.INFO)
    log.propagate = False
    return log, buf


FAKE_TOKEN = "8309114520:AAF2tRprE5gGFVYDbRjJHCcT5F9bTdW0dU"


def test_token_in_a_url_is_redacted_and_the_line_survives(captured):
    log, buf = captured
    log.info('HTTP Request: POST https://api.telegram.org/bot%s/sendMessage "200 OK"',
             FAKE_TOKEN)
    out = buf.getvalue()
    assert "AAF2tRprE5g" not in out
    assert "<redacted>" in out
    # The request is still legible — redaction, not suppression.
    assert "api.telegram.org" in out and "sendMessage" in out


def test_token_is_redacted_from_the_message_and_from_the_args(captured):
    log, buf = captured
    log.info(f"inline bot{FAKE_TOKEN} here")
    log.info("as an arg: %s", f"bot{FAKE_TOKEN}")
    log.info("in a dict: %(t)s", {"t": FAKE_TOKEN})
    out = buf.getvalue()
    assert "AAF2tRprE5g" not in out
    assert out.count("<redacted>") == 3


def test_the_bot_id_stays_so_the_line_is_still_identifiable(captured):
    log, buf = captured
    log.info("POST /bot%s/sendMessage", FAKE_TOKEN)
    assert "8309114520" in buf.getvalue()


def test_ordinary_colons_are_left_alone(captured):
    log, buf = captured
    log.info("took 1:23 · ratio 4:5 · did:moltrust:97caa5d172314d80 · 12:00 UTC")
    out = buf.getvalue()
    assert "<redacted>" not in out
    assert "did:moltrust:97caa5d172314d80" in out


def test_installing_twice_does_not_stack_factories():
    notify.install_token_redaction()
    first = logging.getLogRecordFactory()
    notify.install_token_redaction()
    assert logging.getLogRecordFactory() is first


# ── HTML escaping ──

def test_escape_html_covers_exactly_what_telegram_needs():
    assert notify.escape_html("<i>x</i> & y > z") == "&lt;i&gt;x&lt;/i&gt; &amp; y &gt; z"
    # Quotes are not special in Telegram's HTML mode and must survive as typed.
    assert notify.escape_html('a "quoted" value') == 'a "quoted" value'


def test_escape_html_defuses_the_tag_that_cost_auto_repair_23_messages():
    """Telegram answered 400 "Can't find end tag corresponding to start tag i"."""
    hostile = "GET /path?a=<i>value"
    assert "<i>" not in notify.escape_html(hostile)


def test_escape_html_takes_non_strings():
    assert notify.escape_html(42) == "42"
    assert notify.escape_html(None) == "None"


# ── No sender is left on Markdown ──

def test_no_module_still_sends_markdown():
    """Markdown has no escaping rule that covers every interpolated value, so a
    lone underscore in a user agent or a file name loses the whole message."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        if any(part in path.parts for part in (".webdocs", "venv", "tests")):
            continue
        for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if re.search(r"""parse_mode["']?\s*[:=]\s*["']Markdown""", line):
                offenders.append(f"{path.relative_to(root)}:{i}")
    assert not offenders, "Markdown parse_mode still in use: " + ", ".join(offenders)
