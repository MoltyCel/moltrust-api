#!/usr/bin/env python3
"""Guard for ops WC3: the agent card is one document, kept in two repos and served twice.

The card is signed here, in moltrust-api, and served from moltrust-web's copy on
moltrust.ch and api.moltrust.ch. From 12 to 18 October 2026 the served copy lagged
the source and nothing turned red. The watchdog's check_agent_card_signature did
not catch it: the stale card still carried a valid signature, over stale bytes.

This guard compares four byte strings:

  1. .well-known/agent-card.json in moltrust-api at main (GitHub API)
  2. .well-known/agent-card.json in moltrust-web at main (GitHub API)
  3. https://moltrust.ch/.well-known/agent-card.json
  4. https://api.moltrust.ch/.well-known/agent-card.json

and requires all four to be identical, and the served card's signature to verify
against the published registry key. Equality alone would pass a hand edit made in
both repos; a valid signature alone would pass a stale copy. Only both together say
the served card is the signed source.

Prints one line per source and a closing line "agent-card-guard=ok ..." or
"agent-card-guard=FAIL <what> ...". A source that cannot be read prints
"UNREADABLE ...", which the selftest runner treats as red. Exit 0 ok, 1 red,
2 unreadable.

--api-ref / --web-ref read a repo at another commit instead of main. That is the
fault probe: an older ref holds an older card with a valid signature, and the run
must turn red.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CARD_PATH = ".well-known/agent-card.json"
REPOS = (("moltrust-api", "MoltyCel/moltrust-api"), ("moltrust-web", "MoltyCel/moltrust-web"))
HOSTS = (("moltrust.ch", "https://moltrust.ch/.well-known/agent-card.json"),
         ("api.moltrust.ch", "https://api.moltrust.ch/.well-known/agent-card.json"),
         # The alias under the A2A base path, served by the API from the same
         # file (ops AR-1027). It has to stay byte-identical.
         ("api.moltrust.ch/a2a", "https://api.moltrust.ch/a2a/.well-known/agent-card.json"))
REGISTRY_KEY_URL = "https://api.moltrust.ch/.well-known/registry-key.json"
UA = "moltrust-invariant"


class Unreadable(Exception):
    pass


def git_blob(data: bytes) -> str:
    # The git object id, for comparing with `git hash-object`; not a security hash.
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _get(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 - literal https hosts only
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise Unreadable(f"{url}: {type(e).__name__} {getattr(e, 'code', '')}".rstrip()) from e


def repo_card(repo: str, ref: str, token: str) -> bytes:
    url = f"https://api.github.com/repos/{repo}/contents/{CARD_PATH}?ref={ref}"
    headers = {"Accept": "application/vnd.github.raw"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _get(url, headers)


def judge(copies: dict[str, bytes], served_signature_error: str | None) -> tuple[bool, list[str]]:
    """copies: source name -> bytes, in report order. served_signature_error: None
    when the served card verifies, else the verifier's reason."""
    lines = [f"{name} blob={git_blob(b)[:12]} sha256={hashlib.sha256(b).hexdigest()[:12]} bytes={len(b)}"
             for name, b in copies.items()]
    reference = next(iter(copies.values()))
    differing = [name for name, b in copies.items() if b != reference]
    fails = []
    if differing:
        first = next(iter(copies))
        fails.append(f"ungleich {','.join(differing)} != {first}")
    if served_signature_error is not None:
        fails.append(f"signatur {served_signature_error}")
    if fails:
        lines.append("agent-card-guard=FAIL " + "; ".join(fails))
        return False, lines
    lines.append(f"agent-card-guard=ok {len(copies)} gleich blob={git_blob(reference)[:12]} signatur gueltig")
    return True, lines


def served_signature(card_bytes: bytes, jwk: dict) -> str | None:
    from lib.agent_card_verify import CardVerificationError, verify_agent_card
    try:
        card = json.loads(card_bytes)
    except ValueError as e:
        return f"keine JSON-Karte ({e.__class__.__name__})"
    try:
        verify_agent_card(card, jwk)
    except CardVerificationError as e:
        return str(e)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-ref", default="main")
    ap.add_argument("--web-ref", default="main")
    a = ap.parse_args(argv)
    from app import gh
    token = gh.token()
    refs = {"moltrust-api": a.api_ref, "moltrust-web": a.web_ref}
    try:
        copies = {f"{name}@{refs[name]}": repo_card(repo, refs[name], token) for name, repo in REPOS}
        for host, url in HOSTS:
            copies[host] = _get(url, {})
        jwk = json.loads(_get(REGISTRY_KEY_URL, {}))
    except (Unreadable, ValueError) as e:
        print(f"UNREADABLE {e}")
        return 2
    if a.api_ref != "main" or a.web_ref != "main":
        print(f"fehlerprobe api-ref={a.api_ref} web-ref={a.web_ref}")
    # Both served copies are verified, each error names its host.
    errors = [f"{host}: {err}" for host, _ in HOSTS
              if (err := served_signature(copies[host], jwk)) is not None]
    ok, lines = judge(copies, "; ".join(errors) or None)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
