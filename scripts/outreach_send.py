#!/usr/bin/env python3
"""Send wave 1 of the Bazaar outreach. One mail per recipient, no follow-up.

The texts are the ones approved on 23.09 with the two anti-KI-Sprech violations
corrected, and they were re-run through agents/voice_gate before this file was
written. Nothing here rewrites them.

Rules the shape enforces:

  * one message per recipient, never a CC or BCC list. A seller who can read
    which competitors we also wrote to has learned something we had no right
    to tell them.
  * plain text, no HTML, no tracking pixel, no link shortener. The reader can
    see where every link goes before clicking it.
  * every send is logged with its Message-ID before the next one starts, so a
    crash halfway through leaves a record of what did go out.
  * no retry. A bounce is an answer.

    python3 scripts/outreach_send.py --dry-run
    python3 scripts/outreach_send.py --send
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = os.path.join(HERE, "outreach", "welle-1.json")
LOG = os.path.expanduser("~/outreach-welle-1.log.jsonl")

SUBJECT = "A cheaper rate for callers who can prove who they are"

BODY = """You sell through the x402 Bazaar, which means you take USDC from agents you have no way to tell apart. We built an offline check for that shape: the caller presents a signed attestation and a signature made with its own key, your server verifies both against a cached key set, and nothing on our side is contacted during the request. We run it on our own paid endpoints - a caller with a MolTrust identity and a trust score of 50 or better pays 20 % less, so a 0.05 USDC call costs them 0.04, and everyone else pays list price and is never turned away. It is three headers and about forty lines: npm i @moltrust/x402, then requireMolTrust({ minScore: 50, jwks }) around the route you already have. If it is useful, take it; if the idea is wrong, I would rather hear why than keep building on it.

- Lars Kroehl, MolTrust
https://moltrust.ch
"""


def load_secrets() -> dict:
    out = {}
    with open(os.path.expanduser("~/.moltrust_secrets")) as f:
        for line in f:
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"')
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--send", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(TARGETS))
    mails = [t for t in cfg["targets"] if t["route"] == "mail"]
    s = load_secrets()
    sender = s.get("SMTP_USER", "")
    if not sender or not s.get("SMTP_PASS"):
        raise SystemExit("SMTP nicht konfiguriert; nichts gesendet.")

    # Already sent? The log is the authority, not memory. Re-running must not
    # write to anyone twice.
    seen = set()
    if os.path.exists(LOG):
        for line in open(LOG):
            try:
                seen.add(json.loads(line)["to"])
            except Exception:
                pass

    print(f"Absender: MolTrust <{sender}>")
    print(f"Betreff:  {SUBJECT}")
    print(f"Empfaenger: {len(mails)}, davon schon geschrieben: "
          f"{len([m for m in mails if m['to'] in seen])}")
    print()

    if args.dry_run:
        for m in mails:
            mark = "  BEREITS GESENDET" if m["to"] in seen else ""
            print(f"  -> {m['to']:34} ({m['host']}){mark}")
        print(f"\n--- Text, {len(BODY)} Zeichen ---\n{BODY}")
        print("(Probelauf, nichts gesendet)")
        return 0

    ctx = ssl.create_default_context()
    sent = 0
    with smtplib.SMTP(s["SMTP_HOST"], int(s.get("SMTP_PORT", 587)), timeout=45) as srv:
        srv.starttls(context=ctx)
        srv.login(sender, s["SMTP_PASS"])
        for m in mails:
            if m["to"] in seen:
                print(f"  uebersprungen (schon gesendet): {m['to']}")
                continue
            msg = EmailMessage()
            msg["From"] = f"MolTrust <{sender}>"
            msg["To"] = m["to"]
            msg["Reply-To"] = sender
            msg["Subject"] = SUBJECT
            msg["Date"] = formatdate(localtime=True)
            mid = make_msgid(domain="moltrust.ch")
            msg["Message-ID"] = mid
            msg.set_content(BODY)
            srv.send_message(msg)
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "to": m["to"], "host": m["host"], "subject": SUBJECT,
                   "message_id": mid, "wave": 1}
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"  gesendet: {m['to']:34} {mid}")
            sent += 1
            time.sleep(2)
    print(f"\n{sent} gesendet. Protokoll: {LOG}. Kein Nachfassen vorgesehen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
