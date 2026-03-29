#!/usr/bin/env python3
"""Post sprint wrap-up tweet. One-shot cron for March 30."""
import requests, os, sys
from requests_oauthlib import OAuth1

auth = OAuth1(
    os.environ["X_CONSUMER_KEY"],
    os.environ["X_CONSUMER_SECRET"],
    os.environ["X_ACCESS_TOKEN"],
    os.environ["X_ACCESS_SECRET"]
)

text = """MolTrust sprint wrap-up — three things that close the loop:

1. Output Provenance (IPR) — agents can now prove what they said, before the outcome
2. moltrust-api is open source — github.com/MoltyCel/moltrust-api
3. @moltrust/verify v1.1.0 — full offline VC verification, no API needed

Protocol WP v0.6.1 + blog:
moltrust.ch/blog/sprint-march-2026.html

#AIAgents #W3C #DID #Base #OpenSource"""

resp = requests.post("https://api.twitter.com/2/tweets", json={"text": text}, auth=auth)
data = resp.json()
if resp.status_code in (200, 201):
    print(f"Tweet posted: {data['data']['id']}")
else:
    print(f"FAILED: {resp.status_code} {data}")
    sys.exit(1)
