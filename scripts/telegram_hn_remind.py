"""One-time Telegram reminder to submit the OpenClaw plugin post to Hacker News.

Until 2026-10-09 this file sent at module level, with its own token and no
record of having sent: every execution sent again, including a read-only
probe that only loaded the module (three messages that day). Now it sends
only when run as a program, through app/notify.py, once per post
(app/submit_links.py), with %20 instead of '+' and the title clipped to HN's
80 characters.
"""
import os
import sys

# The repository path, from the file itself. Python puts the script's
# directory on sys.path, not the working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify, submit_links  # noqa: E402

POST_URL = "https://moltrust.ch/blog/openclaw-plugin.html"
TITLE = "Show HN: We built a trust verification plugin for OpenClaw (W3C DID + reputation scoring)"


def main() -> int:
    link = submit_links.hn_submit_link(POST_URL, TITLE)
    text = f"\U0001f99e HN SUBMIT JETZT\n\n{link}"
    result = submit_links.send_once("hn:" + POST_URL, text, channel=notify.WORKLOG)
    print(f"telegram_hn_remind: {result}")
    return 0 if result in ("sent", "already-sent") else 1


if __name__ == "__main__":
    raise SystemExit(main())
