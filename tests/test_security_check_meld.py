"""ops SEC-MELD: the security report reaches Telegram only when Lars has to act.

Rule MELD-1 (Lars, 27.10.2026): Telegram only when Lars has to decide or do
something; at once only for money, a security incident, the outage of a live
service or a root one-liner. Everything else stays in the log and the mail.

The run of 01.11.2026 03:00Z had six alerts, one pip-audit line (dnspython,
msgpack, pyasn1, pypdf, each with a fixed release) and five high-error
endpoints from a foreign crawler. None needed Lars, and all six went to
Telegram, because scripts/security_check.sh sent as soon as ALERT_COUNT > 0.

The functions are sourced with SECURITY_CHECK_FUNCTIONS_ONLY=1, so no check
runs; notify_send is replaced by a stub that writes to a file. Nothing is sent.
"""
import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "security_check.sh"
HTTP_ERRORS = ROOT / "scripts" / "security_http_errors.py"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash missing")

# The class of every alert call, keyed by the start of its message. A new call
# without an entry here, or a changed class, turns the ratchet test red.
CLASSES = {
    '"pip-audit produced no output"': "ABLAGE",
    '"npm audit:': "ABLAGE",
    '"Could not retrieve SSL certificate': "AUSNAHME",
    '"SSL cert expires': "AUSNAHME",
    '"Open ports could not be read': "ABLAGE",
    '"Unexpected ports reachable from outside': "AUSNAHME",
    '"Disk $MOUNT': "AUSNAHME",
    '"High failed SSH login count': "ABLAGE",
    '"API health check failed': "AUSNAHME",
    '"Latest backup is': "ABLAGE",
    '"No SQL backups found': "ABLAGE",
    '"Backup directory': "ABLAGE",
    '"discovery endpoint': "AUSNAHME",
    '"request_log could not be read': "ABLAGE",
    '"${line#ALERT }"': "ABLAGE",                       # pip_audit_split.py
    '"high-error endpoint: ${line#ALERT }"': "ABLAGE",  # security_http_errors.py
}

# The pip-audit line of 01.11.2026, as pip_audit_split.py prints it.
PIP_AUDIT_0111 = (
    "ALERT pip-audit: production dependencies with a fixed release: "
    "dnspython 2.6.1 -> 2.7.0 (1); msgpack 1.0.8 -> 1.1.0 (1); "
    "pyasn1 0.6.0 -> 0.6.1 (1); pypdf 4.2.0 -> 5.1.0 (1)\n"
    "INFO pip-audit: tooling in the venv, not reached from requirements.txt: pip 24.0 (1)\n"
)

# The five high-error endpoints of 01.11.2026, as request_log rows from one
# foreign /24 (documentation range here); security_http_errors.py turns them
# into the ALERT lines, as on the box.
ROWS_0111 = "\n".join([
    "/x402/verify|405|203.0.113.0|66",
    "/join|405|203.0.113.0|48",
    "/auth/signup-did|422|203.0.113.0|41",
    "/identity/nonce|405|203.0.113.0|41",
    "/credentials/verify|422|203.0.113.0|41",
]) + "\n"


def run(body: str, tmp_path: pathlib.Path) -> tuple[subprocess.CompletedProcess, list[str], str]:
    """Source the script's functions, stub the sender, run body, then send."""
    sent = tmp_path / "sent.txt"
    script = f'''
set -eo pipefail
source "{SCRIPT}"
TIMESTAMP="2026-11-01 03:00:00 UTC"
notify_send() {{ cat >> "{sent}"; printf '\\n---\\n' >> "{sent}"; }}
{body}
send_telegram
printf '%s' "$REPORT" > "{tmp_path / 'report.txt'}"
'''
    env = {"PATH": os.environ["PATH"], "SECURITY_CHECK_FUNCTIONS_ONLY": "1"}
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env,
                       timeout=60)
    messages = []
    if sent.exists():
        messages = [m for m in sent.read_text().split("\n---\n") if m.strip()]
    report = (tmp_path / "report.txt").read_text() if (tmp_path / "report.txt").exists() else ""
    return p, messages, report


def http_error_lines() -> str:
    p = subprocess.run(["python3", "-I", str(HTTP_ERRORS)], input=ROWS_0111,
                       capture_output=True, text=True, check=True)
    return p.stdout


def test_the_five_rows_of_0111_are_alerts_of_the_error_check():
    lines = [l for l in http_error_lines().splitlines() if l.startswith("ALERT ")]
    assert len(lines) == 5


def test_the_six_alerts_of_0111_send_nothing(tmp_path):
    pip_file = tmp_path / "pip.txt"
    pip_file.write_text(PIP_AUDIT_0111)
    err_file = tmp_path / "err.txt"
    err_file.write_text(http_error_lines())
    p, messages, report = run(f'feed_pip_audit < "{pip_file}"\n'
                              f'feed_http_errors < "{err_file}"\n'
                              'echo "count=${#ALERTS[@]} err=$ERR_ALERTS"', tmp_path)
    assert p.returncode == 0, p.stderr
    assert "count=6 err=5" in p.stdout
    assert messages == []
    assert "Telegram alert sent" not in p.stdout
    assert "Telegram: nothing to send (6 alert(s)" in p.stdout
    # ABLAGE stays in the report that goes to the log and the mail.
    assert report.count("[ALERT] [ABLAGE]") == 6
    assert "dnspython 2.6.1 -> 2.7.0" in report
    assert "high-error endpoint: /x402/verify 66x HTTP 405" in report


def test_one_outage_among_the_six_sends_exactly_one_message(tmp_path):
    pip_file = tmp_path / "pip.txt"
    pip_file.write_text(PIP_AUDIT_0111)
    err_file = tmp_path / "err.txt"
    err_file.write_text(http_error_lines())
    p, messages, report = run(f'feed_pip_audit < "{pip_file}"\n'
                              f'feed_http_errors < "{err_file}"\n'
                              'alert AUSNAHME "API health check failed (HTTP 000)"', tmp_path)
    assert p.returncode == 0, p.stderr
    assert p.stdout.count("Telegram alert sent") == 1
    assert len(messages) == 1
    msg = messages[0]
    assert msg.startswith("MolTrust Security Alert — 1 issue(s)")
    assert "[AUSNAHME] API health check failed (HTTP 000)" in msg
    # The ABLAGE lines stay out of the message and in the report.
    assert "pip-audit" not in msg and "high-error endpoint" not in msg
    assert report.count("[ALERT] [ABLAGE]") == 6


def test_a_decision_for_lars_is_sent(tmp_path):
    p, messages, _ = run('alert HANDLUNG "decide X"\nalert ABLAGE "upkeep Y"', tmp_path)
    assert p.returncode == 0, p.stderr
    assert len(messages) == 1
    assert "[HANDLUNG] decide X" in messages[0] and "upkeep Y" not in messages[0]


def test_no_alert_sends_nothing(tmp_path):
    p, messages, _ = run(':', tmp_path)
    assert p.returncode == 0, p.stderr
    assert messages == []


def test_an_alert_without_class_stops_the_script(tmp_path):
    p, messages, _ = run('alert "something new"', tmp_path)
    assert p.returncode != 0
    assert "alert without class" in p.stderr
    assert messages == []


def test_every_alert_call_carries_its_recorded_class():
    """Ratchet: a new alert call without an entry in CLASSES turns this red."""
    found = {}
    for line in SCRIPT.read_text().splitlines():
        code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        m = re.search(r'\balert (HANDLUNG|AUSNAHME|ABLAGE|"\S*) ?(.*)$', code)
        if not m or "alert()" in code:
            continue
        assert m.group(1) in ("HANDLUNG", "AUSNAHME", "ABLAGE"), f"alert without class: {line}"
        msg = m.group(2)
        keys = [k for k in CLASSES if msg.startswith(k)]
        assert len(keys) == 1, f"alert call not in CLASSES: {line.strip()}"
        found[keys[0]] = m.group(1)
    assert found == CLASSES


def test_telegram_is_reached_only_through_send_telegram():
    src = SCRIPT.read_text()
    calls = [l for l in src.splitlines()
             if re.search(r"\b(notify_send|send_telegram)\b", l) and not l.lstrip().startswith("#")]
    # definition of notify_send, its one use, definition of send_telegram, its one use
    assert len(calls) == 4, calls
    assert src.count("-m app.notify") == 1
