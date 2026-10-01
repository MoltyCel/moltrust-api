#!/usr/bin/env python3
"""What the widened read surface would do, without writing anything.

Walks the real `read_surface` and applies cmd_run's filters in cmd_run's order.
The funnel is counted over every comment; drafting stops where the per-run
allowance stops, because that is what the agent does. No post_reply, no state
write, no MEMORY.md, no daily log.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx  # noqa: E402

import ambassador as A  # noqa: E402
from agents import comment_gate as cg, reply_radar  # noqa: E402
from moltbook_poster import content_violations  # noqa: E402

import os
ROOM = int(os.environ.get("DRYRUN_DRAFT_N", cg.MAX_PER_RUN))


def main() -> None:
    A.init_keys()
    if not (A.MOLTBOOK_KEY and A.ANTHROPIC_KEY):
        sys.exit("missing keys")
    state = json.load(open(A.STATE_FILE)) if A.STATE_FILE.exists() else {"seen_comments": {}}
    seen_map = state.get("seen_comments", {})
    kb = reply_radar.load_kb()
    print(f"KB: {len(kb)} pages", file=sys.stderr)

    t0 = time.time()
    with httpx.Client() as client:
        surface = A.read_surface(client)
        own = sum(1 for p in surface if p.get("source") == "own")
        print(f"read_surface: {len(surface)} threads ({own} own, {len(surface)-own} commented)",
              file=sys.stderr)

        f = dict(threads=len(surface), comments=0, ours=0, empty=0, seen=0,
                 low_effort=0, off_topic=0, relevant=0, sender_dup=0,
                 rate_limited=0, drafted=0, dedup=0, gate=0, content_rule=0,
                 would_post=0, deferred_no_room=0)
        reasons: dict[str, int] = {}
        candidates = []          # survive every cheap filter, in run order
        answered: set[str] = set()

        for post in surface:
            pid = post["id"]
            seen = set(seen_map.get(pid, []))
            tree = A.get_comments(client, pid)
            flat = []
            def walk(lst):
                for c in lst:
                    flat.append(c)
                    if isinstance(c.get("replies"), list):
                        walk(c["replies"])
            walk(tree)
            f["comments"] += len(flat)
            for c in flat:
                name = (c.get("author") or {}).get("name", "unknown")
                text = c.get("content", "") or ""
                if A.is_our_account(name, c.get("author_id", "")):
                    f["ours"] += 1; continue
                if c["id"] in seen:
                    f["seen"] += 1; continue
                if not text.strip():
                    f["empty"] += 1; continue
                if A.LOW_EFFORT_PATTERNS.match(text.strip()):
                    f["low_effort"] += 1; continue
                ok, why = cg.worth_answering(text)
                if not ok:
                    f["off_topic"] += 1
                    reasons[why.split(" —")[0]] = reasons.get(why.split(" —")[0], 0) + 1
                    continue
                f["relevant"] += 1
                if name in answered:
                    f["sender_dup"] += 1; continue
                if A.check_agent_rate_limit(name):
                    f["rate_limited"] += 1; continue
                answered.add(name)
                candidates.append({"post": post, "comment": c, "author": name,
                                   "text": text, "relevance": why})

        read_secs = time.time() - t0
        print(f"Lesen: {read_secs:.1f}s fuer {f['threads']} Threads", file=sys.stderr)

        results = []
        for i, cand in enumerate(candidates):
            if i >= ROOM:
                f["deferred_no_room"] += 1
                continue
            post, c, name, text = cand["post"], cand["comment"], cand["author"], cand["text"]
            if not post.get("title"):
                fetched = A.get_post(client, post["id"])
                post["title"] = fetched.get("title") or "(untitled)"
                post["content"] = fetched.get("content") or ""
            stage = A.get_stage({"agent_replies": {}}, name, text)
            need_num = cg.needs_number(text)
            draft = A.generate_reply(post["title"], post.get("content", ""), name,
                                     text, stage, session_id=f"scope_{c['id']}")
            f["drafted"] += 1
            if not draft:
                results.append({"author": name, "outcome": "no draft"}); continue
            first_words = len(draft.split())
            if A.check_reply_dedup(name, draft):
                f["dedup"] += 1
                results.append({"author": name, "outcome": "dedup"}); continue
            passed, problems = cg.check_reply(draft, kb, require_number=need_num,
                                              comment_text=text)
            redrafted = False
            if not passed:
                note = cg.redraft_note(draft)
                if note:
                    second = A.generate_reply(post["title"], post.get("content", ""),
                                              name, text, stage,
                                              session_id=f"scope_{c['id']}",
                                              redraft_note=note)
                    redrafted = True
                    if second:
                        draft = second
                        passed, problems = cg.check_reply(draft, kb,
                                                          require_number=need_num,
                                                          comment_text=text)
            rec = {"author": name, "post": post["id"], "source": post.get("source"),
                   "title": post.get("title"), "relevance": cand["relevance"],
                   "stage": stage, "require_number": need_num,
                   "first_words": first_words, "words": len(draft.split()),
                   "redrafted": redrafted, "comment": text, "draft": draft}
            if not passed:
                f["gate"] += 1; rec["outcome"] = "gate"
                rec["detail"] = "; ".join(problems)[:300]
            else:
                broken = content_violations("", draft)
                if broken:
                    f["content_rule"] += 1; rec["outcome"] = "content rule"
                    rec["detail"] = ", ".join(broken)
                else:
                    f["would_post"] += 1; rec["outcome"] = "would post"
            results.append(rec)

    out = {"funnel": f, "off_topic_reasons": reasons, "room": ROOM,
           "candidates_total": len(candidates), "read_seconds": round(read_secs, 1),
           "results": results}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
