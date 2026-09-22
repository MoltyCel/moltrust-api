"""Find out whether this account can create a post longer than 280 characters.

X's own API reference for `POST /2/tweets` does not mention long posts at all:
no `note_tweet` field, no stated limit on `text`, nothing about Premium. Reading
a long post through the API works; creating one is undocumented, and an
undocumented capability is not a capability until it has been observed.

There is no dry run for creating a post. The only decisive test is to create one
and look at the answer, which means this script **publishes**. It therefore:

  * refuses without --i-will-publish, so nobody runs it by reflex,
  * posts a text that is obviously a technical probe,
  * deletes it immediately and reports whether the delete succeeded,
  * prints the post id either way, so a failed delete is recoverable by hand.

Exposure is a few seconds to whoever is looking at that moment. Weigh that
against the alternative, which is guessing at the syndication design.

    python scripts/x_longpost_probe.py --i-will-publish
    python scripts/x_longpost_probe.py --i-will-publish --keep   # do not delete
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from requests_oauthlib import OAuth1

from app import notify

notify.silence_http_request_logs()

TWEETS = "https://api.twitter.com/2/tweets"
PROBE_LENGTH = 700          # comfortably past 280, far short of the 25,000 claim


def auth() -> OAuth1:
    keys = [os.environ[k] for k in
            ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    return OAuth1(*keys)


def probe_text() -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    head = (f"API capability probe {stamp}. Checking whether POST /2/tweets accepts "
            f"a body longer than 280 characters on a Premium account. This post is "
            f"deleted within seconds of being created and carries no content. ")
    filler = ("Padding to exceed the classic limit so the API has to decide. " * 12)
    return (head + filler)[:PROBE_LENGTH]


def main(publish: bool, keep: bool) -> int:
    if not publish:
        print("Refusing: this script publishes. Re-run with --i-will-publish.",
              file=sys.stderr)
        return 2

    a = auth()
    me = requests.get("https://api.twitter.com/2/users/me",
                      params={"user.fields": "subscription_type,verified_type"},
                      auth=a, timeout=30).json().get("data", {})
    print(f"account: @{me.get('username')} subscription={me.get('subscription_type')} "
          f"verified_type={me.get('verified_type')}")

    text = probe_text()
    print(f"posting {len(text)} characters…")
    r = requests.post(TWEETS, json={"text": text}, auth=a, timeout=30)
    print(f"POST /2/tweets -> {r.status_code}")
    print(f"  {r.text[:400]}")

    if r.status_code not in (200, 201):
        print("\nRESULT: long posts are NOT creatable through the API on this "
              "account. Keep the thread shape.")
        return 0

    post_id = r.json()["data"]["id"]
    echoed = r.json()["data"].get("text", "")
    print(f"\ncreated id={post_id}  https://x.com/MolTrust/status/{post_id}")
    print(f"  the creation response echoes {len(echoed)} characters")

    # The creation response truncates its echo, so it cannot answer the
    # question on its own. Read the post back and ask for note_tweet, which is
    # where X keeps the full body of a long post.
    g = requests.get(f"{TWEETS}/{post_id}",
                     params={"tweet.fields": "note_tweet,text"}, auth=a, timeout=30)
    data = g.json().get("data", {}) if g.status_code == 200 else {}
    note = data.get("note_tweet") or {}
    note_text = note.get("text", "")
    print(f"  read back: text={len(data.get('text',''))} chars, "
          f"note_tweet={'yes, ' + str(len(note_text)) + ' chars' if note_text else 'no'}")
    if note_text:
        print(f"\nRESULT: long posts ARE creatable and stored in full "
              f"({len(note_text)} of {len(text)} characters sent).")
    elif len(data.get("text", "")) >= len(text) - 5:
        print(f"\nRESULT: long posts ARE creatable; the full body is in `text`.")
    else:
        print(f"\nRESULT: the call succeeded but only "
              f"{len(data.get('text',''))} characters survived — X truncated it. "
              f"Treat this as NOT supported.")

    if keep:
        print("  --keep given, leaving it up.")
        return 0
    d = requests.delete(f"{TWEETS}/{post_id}", auth=a, timeout=30)
    print(f"  DELETE -> {d.status_code} {d.text[:160]}")
    if d.status_code != 200:
        print(f"  DELETE FAILED — remove it by hand: {post_id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--i-will-publish" in sys.argv, "--keep" in sys.argv))
