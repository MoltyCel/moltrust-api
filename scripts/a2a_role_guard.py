#!/usr/bin/env python3
"""Live guard for ops job A2A: /a2a declares capabilities and executes no tasks.

Against production, without credentials:

  1. a valid SendMessage to https://api.moltrust.ch/a2a  -> JSON-RPC error -32004
     (UnsupportedOperationError) whose message points to REST, and no result
  2. the public agent card                               -> served, its signature
     verifies against the published registry key, and it says that tasks are
     not executed

Direction 1 alone would call healthy a card that still promises A2A tasks.
Direction 2 alone would call healthy an endpoint that answers every task with a
success again, which is the defect this job removed (21.10.2026). Refusing a
message writes nothing.

Prints one line per check and a closing line "a2a-role=ok" or
"a2a-role=FAIL <check> ...". A source that cannot be read prints "UNREADABLE ...".
Exit 0 ok, 1 red, 2 unreadable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

A2A_URL = "https://api.moltrust.ch/a2a"
CARD_URL = "https://api.moltrust.ch/.well-known/agent-card.json"
REGISTRY_KEY_URL = "https://api.moltrust.ch/.well-known/registry-key.json"
UA = "moltrust-invariant"
SEND = {"jsonrpc": "2.0", "id": "a2a-role-guard", "method": "SendMessage",
        "params": {"message": {"messageId": "a2a-role-guard", "role": "ROLE_USER",
                               "parts": [{"text": "What is the trust score of did:moltrust:0000000000000000?"}]}}}
CARD_STATEMENT = "executes no tasks"


class Unreadable(Exception):
    pass


def _fetch(url: str, body: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 - literal https hosts only
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise Unreadable(f"{url}: {type(e).__name__} {getattr(e, 'code', '')}".rstrip()) from e


def judge_task(answer: dict) -> str | None:
    """None when the task message was refused as unsupported, else why not."""
    if "result" in answer:
        return "SendMessage bekam ein result statt einer Ablehnung"
    err = answer.get("error") or {}
    if err.get("code") != -32004:
        return f"SendMessage code={err.get('code')} statt -32004"
    if "REST" not in str(err.get("message", "")):
        return "Ablehnung nennt den Weg ueber REST nicht"
    return None


def judge_card(card_bytes: bytes, jwk: dict) -> str | None:
    """None when the served card verifies and says it executes no tasks."""
    from lib.agent_card_verify import CardVerificationError, verify_agent_card
    try:
        card = json.loads(card_bytes)
    except ValueError as e:
        return f"keine JSON-Karte ({e.__class__.__name__})"
    try:
        verify_agent_card(card, jwk)
    except CardVerificationError as e:
        return f"signatur {e}"
    if CARD_STATEMENT not in str(card.get("description", "")):
        return f"Card sagt nicht '{CARD_STATEMENT}'"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a2a-url", default=A2A_URL, help="fault probe: another endpoint")
    a = ap.parse_args(argv)
    try:
        raw = _fetch(a.a2a_url, json.dumps(SEND).encode(),
                     {"Content-Type": "application/json", "A2A-Version": "1.0"})
        answer = json.loads(raw)
        card_bytes = _fetch(CARD_URL)
        jwk = json.loads(_fetch(REGISTRY_KEY_URL))
    except (Unreadable, ValueError) as e:
        print(f"UNREADABLE {e}")
        return 2
    fails = []
    task = judge_task(answer)
    print(f"task {a.a2a_url}: " + (task or "abgelehnt -32004 mit Verweis auf REST"))
    if task:
        fails.append(f"task {task}")
    card = judge_card(card_bytes, jwk)
    print(f"card {CARD_URL}: " + (card or f"erreichbar, signatur gueltig, sagt '{CARD_STATEMENT}'"))
    if card:
        fails.append(f"card {card}")
    if fails:
        print("a2a-role=FAIL " + "; ".join(fails))
        return 1
    print("a2a-role=ok task abgelehnt, card signiert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
