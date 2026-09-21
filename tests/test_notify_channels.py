"""Every Telegram message belongs to exactly one channel.

One chat carried stats, alerts, payouts and draft tweets together until
2026-09-21. The split is only worth having if it stays complete, and it stops
being complete the first time someone adds a sender and forgets to say where it
posts. So the assignment is enforced here rather than documented and hoped for:

  * `notify.send_telegram` takes `channel` as a required keyword, so a call
    without one is a TypeError rather than a silent default;
  * every module that defines its own thin `send_telegram` must declare a
    default channel in the signature;
  * no module may resolve `TELEGRAM_CHAT_ID` directly any more — that is the
    undivided chat and using it bypasses the routing.

The scan is over the repository source, so it covers the shell senders and the
standalone scripts that no unit test imports.
"""

import ast
import os
import re

import pytest

from app import notify

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories that hold real senders. `tests` is excluded because a test may
# legitimately talk about the undivided chat while asserting on it.
SOURCE_DIRS = ("app", "agents", "agent", "scripts", "monitor", "workers", "lib")

SKIP_PARTS = ("venv", "node_modules", "__pycache__", ".git", "tests")

# `app/notify.py` is the router itself and `scripts/scrub_telegram_token.sh`
# exists to redact the variable, so both name it by necessity.
DIRECT_CHAT_ID_ALLOWED = {
    "app/notify.py",
    "scripts/scrub_telegram_token.sh",
}


def _source_files(exts):
    for root_name in SOURCE_DIRS:
        root = os.path.join(REPO, root_name)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_PARTS
                # A nested checkout is another repository — its senders follow
                # its rules. The content-scout worker keeps one of moltrust-web
                # here to diff published pages against.
                and not os.path.isdir(os.path.join(dirpath, d, ".git"))
            ]
            for fn in filenames:
                if not fn.endswith(exts) or ".bak" in fn:
                    continue
                full = os.path.join(dirpath, fn)
                yield os.path.relpath(full, REPO), full


# ---------------------------------------------------------------------------
# The router itself
# ---------------------------------------------------------------------------

def test_four_channels_and_no_more():
    assert notify.CHANNELS == ("stats", "alerts", "money", "worklog")


def test_send_telegram_requires_an_explicit_channel():
    with pytest.raises(TypeError):
        notify.send_telegram("no channel given")


def test_unknown_channel_is_rejected_rather_than_guessed():
    with pytest.raises(ValueError, match="unknown telegram channel"):
        notify.chat_id_for("payouts")


def test_channel_chat_id_wins_over_the_undivided_one(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100общий")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_MONEY", "-100money")
    assert notify.chat_id_for(notify.MONEY) == "-100money"
    assert notify.chat_id_for(notify.STATS) == "-100общий"


def test_unconfigured_channel_falls_back_rather_than_dropping(monkeypatch):
    """The routing ships before the chats exist. A message that would have been
    delivered yesterday must not be dropped because its channel has no id."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100single")
    for name in notify.CHANNELS:
        monkeypatch.delenv(f"TELEGRAM_CHAT_ID_{name.upper()}", raising=False)
        assert notify.chat_id_for(name) == "-100single"


# ---------------------------------------------------------------------------
# Every sender in the repository
# ---------------------------------------------------------------------------

def test_every_local_sender_declares_a_default_channel():
    offenders = []
    for rel, full in _source_files((".py",)):
        if rel == "app/notify.py":
            continue
        try:
            tree = ast.parse(open(full, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("send_telegram"):
                continue
            kwonly = [a.arg for a in node.args.kwonlyargs]
            if "channel" not in kwonly:
                offenders.append(f"{rel}:{node.lineno} {node.name}")
    assert not offenders, (
        "these senders do not declare a channel — add "
        "`*, channel: str = notify.<CHANNEL>` to the signature: " + ", ".join(offenders)
    )


_DIRECT = re.compile(r"TELEGRAM_CHAT_ID(?![_A-Z])")


def test_no_sender_reaches_past_the_router_to_the_undivided_chat():
    """Using TELEGRAM_CHAT_ID directly posts everything into one chat again.

    Shell senders are covered too. Two forms are accepted: a line that also
    names a channel variable (the `${TELEGRAM_CHAT_ID_X:-$TELEGRAM_CHAT_ID}`
    shape), and a line carrying the marker `undivided-chat fallback`, which is
    how the shell scripts spell the fallback that a Python caller gets from
    `notify.chat_id_for`. Anything else resolves the undivided chat on purpose
    and is what this test exists to stop.
    """
    marker = "undivided-chat fallback"
    offenders = []
    for rel, full in _source_files((".py", ".sh")):
        if rel in DIRECT_CHAT_ID_ALLOWED:
            continue
        lines = open(full, encoding="utf-8", errors="ignore").read().splitlines()
        for i, line in enumerate(lines, 1):
            if not _DIRECT.search(line):
                continue
            if "TELEGRAM_CHAT_ID_" in line:  # names a channel variable as well
                continue
            if marker in line or (i > 1 and marker in lines[i - 2]):
                continue
            offenders.append(f"{rel}:{i}")
    assert not offenders, (
        "these lines resolve the undivided chat instead of a channel: "
        + ", ".join(offenders)
    )
