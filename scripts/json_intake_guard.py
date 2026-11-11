#!/usr/bin/env python3
"""Live guard for ops JI: the JSON intake of /vc/aae/evaluate and /vc/aae/submit.

Two directions per endpoint, against production, without credentials:

  1. a body with a member name that occurs twice  -> 422 naming duplicate-member
  2. the same body with the name once             -> passes intake, reaches auth: 401

Direction 1 alone would call a deploy healthy that refuses every body at intake.
Direction 2 alone would call a deploy healthy that parses last-wins again. The
intake runs before authentication, so neither request is authenticated and
neither writes anything.

Prints one line per endpoint and direction and a closing line "json-intake=ok" or
"json-intake=FAIL <endpoint> <direction> ...". Exit 0 ok, 1 a direction failed.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

API = "https://api.moltrust.ch"
PATHS = ("/vc/aae/evaluate", "/vc/aae/submit")
REF = "sha256:" + "0" * 64
VALID = ('{"aae_ref": "%s", "action_context": {"nonce": "json-intake-guard", '
         '"domain": "api.moltrust.ch"}}' % REF).encode()
DUPLICATE = ('{"aae_ref": "%s", "action_context": {"nonce": "json-intake-guard", '
             '"domain": "evil.example.com", "domain": "api.moltrust.ch"}}' % REF).encode()


def post(url: str, body: bytes) -> tuple[int, str]:
    """(status, detail); (0, "") when no HTTP answer came back."""
    if not url.startswith("https://"):
        raise ValueError("the guard talks https only")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "moltrust-json-intake-guard/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 - https only, checked above
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, OSError):
        return 0, ""
    try:
        detail = json.loads(raw).get("detail")
    except Exception:
        detail = None
    return status, detail if isinstance(detail, str) else ""


def judge(results: dict) -> tuple[bool, list[str]]:
    """results: path -> {"duplicate": (status, detail), "valid": (status, detail)}."""
    lines, failed = [], []
    for path, r in results.items():
        s1, d1 = r["duplicate"]
        ok1 = s1 == 422 and "duplicate-member" in d1
        s2, d2 = r["valid"]
        ok2 = s2 == 401 and "JSON intake" not in d2
        lines.append(f"{path} richtung1 doppelt status={s1} erwartet=422 duplicate-member "
                     f"{'ok' if ok1 else 'FAIL'}")
        lines.append(f"{path} richtung2 gueltig status={s2} erwartet=401 (Eingang passiert) "
                     f"{'ok' if ok2 else 'FAIL'}")
        if not ok1:
            failed.append(f"{path} richtung1 (doppelter Feldname ergab {s1 or 'keine Antwort'} "
                          f"{d1[:80]!r}, erwartet 422 duplicate-member)")
        if not ok2:
            failed.append(f"{path} richtung2 (gueltiges Dokument ergab {s2 or 'keine Antwort'} "
                          f"{d2[:80]!r}, erwartet 401 hinter dem Eingang)")
    lines.append("json-intake=ok" if not failed else "json-intake=FAIL " + "; ".join(failed))
    return not failed, lines


def run(base: str, *, send=post, duplicate: bytes = DUPLICATE) -> tuple[bool, list[str]]:
    base = base.rstrip("/")
    return judge({p: {"duplicate": send(base + p, duplicate), "valid": send(base + p, VALID)}
                  for p in PATHS})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=API)
    ap.add_argument("--fault-valid-as-duplicate", action="store_true",
                    help="Fehlerprobe: in Richtung 1 das gueltige Dokument senden. Der Lauf muss rot werden.")
    a = ap.parse_args(argv)
    if a.fault_valid_as_duplicate:
        print("fehlerprobe richtung1 sendet das gueltige Dokument")
    ok, lines = run(a.base, duplicate=VALID if a.fault_valid_as_duplicate else DUPLICATE)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
