#!/usr/bin/env python3
"""Guard for ops AR-1024: every skill in the served agent-skills index loads, and
its bytes match the digest the index states.

The index went live on 24.10.2026 and passed every test: the field table was
valid and each digest was right. The served copy still pointed the moltrust skill
at a 404, because the content filter had replaced the 40-character commit in its
URL with `[REDACTED]` on the way out. A check of the index alone stays green
against that; only fetching what the index links says whether it works.

For each entry of https://api.moltrust.ch/.well-known/agent-skills/index.json the
guard resolves `url` against the index URL (Agent Skills Discovery v0.2.0, the
README at :227), fetches it and compares SHA-256 of the raw bytes with `digest`.

Prints one line per skill and a closing line "agent-skills-guard=ok ..." or
"agent-skills-guard=FAIL ...". A source that cannot be read prints
"UNREADABLE ...", which the selftest runner treats as red. Exit 0 ok, 1 red,
2 unreadable.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

INDEX_URL = "https://api.moltrust.ch/.well-known/agent-skills/index.json"
UA = "moltrust-invariant"


class Unreadable(Exception):
    pass


def _get(url: str) -> bytes:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise Unreadable(f"{url}: not https")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 - https only, checked above
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise Unreadable(f"{url}: {type(e).__name__} {getattr(e, 'code', '')}".rstrip()) from e


def judge(index: dict, fetched: dict[str, bytes | str]) -> tuple[bool, list[str]]:
    """fetched: skill name -> the bytes behind its url, or the reason it could not
    be read. Every skill in the index must be present and match its digest."""
    lines, fails = [], []
    skills = index.get("skills") or []
    if not skills:
        fails.append("index lists no skills")
    for s in skills:
        name = s.get("name", "?")
        got = fetched.get(name)
        if isinstance(got, (bytes, bytearray)):
            digest = "sha256:" + hashlib.sha256(got).hexdigest()
            same = digest == s.get("digest")
            lines.append(f"{name} bytes={len(got)} {digest[:19]} {'=' if same else '!='} index")
            if not same:
                fails.append(f"{name} digest")
        else:
            lines.append(f"{name} {got or 'not fetched'}")
            fails.append(f"{name} unreadable")
    if fails:
        lines.append("agent-skills-guard=FAIL " + "; ".join(fails))
        return False, lines
    lines.append(f"agent-skills-guard=ok {len(skills)} skills load and match their digest")
    return True, lines


def main(argv=None) -> int:
    try:
        index = json.loads(_get(INDEX_URL))
    except (Unreadable, ValueError) as e:
        print(f"UNREADABLE {e}")
        return 2
    fetched: dict[str, bytes | str] = {}
    for s in index.get("skills") or []:
        url = urllib.parse.urljoin(INDEX_URL, s.get("url", ""))
        try:
            fetched[s.get("name", "?")] = _get(url)
        except Unreadable as e:
            # A linked skill that does not load is the finding itself, not an
            # unreadable guard: the index was read.
            fetched[s.get("name", "?")] = f"UNLOADABLE {e}"
    ok, lines = judge(index, fetched)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
