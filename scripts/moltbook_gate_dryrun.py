"""What the Moltbook comment gate would do, without writing anything.

Reads a corpus of incoming comments, walks each one through the same filters
`ambassador.cmd_run` applies, and reports how many survive each. It calls
`generate_reply` and `check_reply` — so it costs model calls — and nothing else:
no `post_reply`, no state file, no MEMORY.md, no daily log. The three functions
that write are not imported.

Usage:
    python scripts/moltbook_gate_dryrun.py <corpus.jsonl> [--limit N] [--out FILE]

The corpus is one JSON object per line with `id`, `author`, `content`,
`post_id`, `post_title`, newest first.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx  # noqa: E402

import ambassador  # noqa: E402
from agents import comment_gate, reply_radar  # noqa: E402
from moltbook_poster import content_violations  # noqa: E402

BASE = comment_gate.MOLTBOOK_BASE


def post_bodies(client, post_ids, key):
    """Post content, so a draft sees what the comment was replying to."""
    out = {}
    for pid in post_ids:
        try:
            r = client.get(f"{BASE}/posts/{pid}",
                           headers={"Authorization": f"Bearer {key}"}, timeout=30)
            if r.status_code == 200:
                d = r.json()
                post = d.get("post") if isinstance(d, dict) else None
                out[pid] = (post or d or {}).get("content", "") or ""
        except Exception:
            out[pid] = ""
    return out


def suitability(row, draft, reason):
    """How well this one would serve as the first comment written after release.

    A strong relevance term beats three weak ones, a question earns an answer,
    a long comment carries something to answer, and a draft with a figure in it
    is the kind the account was refused for not writing.
    """
    score = 0
    if reason.startswith("on topic (") and "," not in reason:
        score += 2          # a strong term carried it on its own
    if "?" in row["content"]:
        score += 2
    words = len(row["content"].split())
    score += 2 if words >= 80 else 1 if words >= 40 else 0
    if any(c.isdigit() for c in draft):
        score += 1
    return score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    ambassador.init_keys()
    if not (ambassador.MOLTBOOK_KEY and ambassador.ANTHROPIC_KEY):
        sys.exit("missing keys")

    rows = [json.loads(l) for l in open(args.corpus) if l.strip()][:args.limit]
    print(f"corpus: {len(rows)} comments", file=sys.stderr)

    try:
        kb = reply_radar.load_kb()
    except Exception as e:
        sys.exit(f"KB unavailable, rule (h) would block everything: {e}")
    print(f"KB: {len(kb)} pages", file=sys.stderr)

    with httpx.Client() as client:
        bodies = post_bodies(client, sorted({r["post_id"] for r in rows}),
                             ambassador.MOLTBOOK_KEY)

    tally = {"total": len(rows), "low_effort": 0, "off_topic": 0, "off_topic_old": 0,
             "relevant": 0, "no_draft": 0, "dedup": 0, "gate_blocked": 0,
             "redrafted": 0, "redraft_saved": 0, "passed": 0, "needs_number": 0,
             # The probe rule (direct question only) expires on its own, so the
             # relevance path is counted separately to stay comparable with the
             # run before it existed.
             "relevance_only": 0, "held_by_probe_rule": 0, "content_rule": 0}
    results = []
    after_probe = comment_gate.DIRECT_QUESTION_UNTIL + datetime.timedelta(days=1)

    OLD = re.compile(
        r"\b(agent[- ]?(identity|identities|authorization|authorisation|trust|credential)"
        r"|erc[- ]?8004|x402|did:|verifiable credential|agent registry|attestation"
        r"|know your agent|agent passport|mandate|delegation|provenance)\b",
        re.I)

    for i, row in enumerate(rows, 1):
        text = row["content"]
        rec = {"id": row["id"], "author": row["author"], "outcome": "", "detail": ""}

        if not OLD.search(text):
            tally["off_topic_old"] += 1

        if ambassador.LOW_EFFORT_PATTERNS.match(text.strip()):
            tally["low_effort"] += 1
            rec["outcome"] = "low-effort"
            results.append(rec)
            continue

        if comment_gate.worth_answering(text, now=after_probe)[0]:
            tally["relevance_only"] += 1

        on_topic, reason = comment_gate.worth_answering(text)
        if not on_topic:
            tally["off_topic"] += 1
            if "no direct question" in reason:
                tally["held_by_probe_rule"] += 1
            rec.update(outcome="off-topic", detail=reason)
            results.append(rec)
            continue
        tally["relevant"] += 1

        require_number = comment_gate.needs_number(text)
        if require_number:
            tally["needs_number"] += 1

        stage = ambassador.get_stage({"agent_replies": {}}, row["author"], text)
        draft = ambassador.generate_reply(
            row.get("post_title", ""), bodies.get(row["post_id"], ""),
            row["author"], text, stage,
            session_id=f"dryrun_{row['id']}")
        if not draft:
            tally["no_draft"] += 1
            rec.update(outcome="no draft")
            results.append(rec)
            continue

        dup = ambassador.check_reply_dedup(row["author"], draft)
        if dup:
            tally["dedup"] += 1
            rec.update(outcome="dedup", detail=dup)
            results.append(rec)
            continue

        passed, problems = comment_gate.check_reply(draft, kb,
                                                   require_number=require_number)
        redrafted = False
        if not passed:
            banned = comment_gate.banned_hits(draft)
            if banned:
                second = ambassador.generate_reply(
                    row.get("post_title", ""), bodies.get(row["post_id"], ""),
                    row["author"], text, stage,
                    session_id=f"dryrun_{row['id']}", avoid_words=banned)
                tally["redrafted"] += 1
                redrafted = True
                if second:
                    draft = second
                    passed, problems = comment_gate.check_reply(
                        draft, kb, require_number=require_number)
                    if passed:
                        tally["redraft_saved"] += 1

        rec.update(draft=draft, stage=stage, redrafted=redrafted, words=len(draft.split()),
                   require_number=require_number, relevance=reason)
        if not passed:
            tally["gate_blocked"] += 1
            rec.update(outcome="gate", detail="; ".join(problems)[:300])
            results.append(rec)
            print(f"  {i}/{len(rows)} {row['author'][:20]:22} {rec['outcome']}",
                  file=sys.stderr)
            continue

        # The last rule before the network, the one `post_reply` applies. Run 1
        # counted eight drafts as postable without it; every one of them carried
        # emphasis markup and would have been withheld here.
        broken = content_violations("", draft)
        if broken:
            tally["content_rule"] += 1
            rec.update(outcome="content rule", detail=", ".join(broken))
        else:
            tally["passed"] += 1
            rec.update(outcome="would post", score=suitability(row, draft, reason),
                       comment=text)
        results.append(rec)
        print(f"  {i}/{len(rows)} {row['author'][:20]:22} {rec['outcome']}",
              file=sys.stderr)

    print(json.dumps({"tally": tally, "results": results}, ensure_ascii=False,
                     indent=1),
          file=open(args.out, "w") if args.out else sys.stdout)


if __name__ == "__main__":
    main()
