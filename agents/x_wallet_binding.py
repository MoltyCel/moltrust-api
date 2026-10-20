#!/usr/bin/env python3
"""Post wallet binding announcement tweet. One-shot."""
import requests, os, sys
from requests_oauthlib import OAuth1

# Wrapped into main() on 2026-10-09 (WORKFLOW 18): until then this file
# acted when merely loaded — it posted a public tweet on X. A module body defines; it does not act.


def main():
    auth = OAuth1(
        os.environ["X_CONSUMER_KEY"],
        os.environ["X_CONSUMER_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_SECRET"]
    )

    text = """Something just went live on @MolTrust.

    Agent DIDs now support wallet binding.

    Register a DID. Bind your wallet. Any x402 payer can resolve your payment address directly from your identity.

    DID → PaymentService → USDC on Base.

    30 seconds to set up. Free.

    api.moltrust.ch/x402/verify?did=did:moltrust:d34ed796a4dc4698

    #AIAgents #x402 #Base #A2A"""

    resp = requests.post("https://api.twitter.com/2/tweets", json={"text": text}, auth=auth, timeout=15)
    data = resp.json()
    if resp.status_code in (200, 201):
        print(f"Tweet posted: {data['data']['id']}")
    else:
        print(f"FAILED: {resp.status_code} {data}")
        sys.exit(1)


if __name__ == "__main__":
    main()
