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

    # Every comment the relevance path admits is carried through. Whether the
    # probe rule (direct question only, expires 2026-09-30) would also have let
    # it through is recorded per comment, so one run yields both funnels.
    results = []
    after_probe = comment_gate.DIRECT_QUESTION_UNTIL + datetime.timedelta(days=1)

    OLD = re.compile(
        r"\b(agent[- ]?(identity|identities|authorization|authorisation|trust|credential)"
        r"|erc[- ]?8004|x402|did:|verifiable credential|agent registry|attestation"
        r"|know your agent|agent passport|mandate|delegation|provenance)\b",
        re.I)

    for i, row in enumerate(rows, 1):
        text = row["content"]
        probe_ok = comment_gate.worth_answering(text)[0]
        rec = {"id": row["id"], "author": row["author"], "outcome": "",
               "detail": "", "probe_ok": probe_ok,
               "old_filter_ok": bool(OLD.search(text))}

        if ambassador.LOW_EFFORT_PATTERNS.match(text.strip()):
            rec["outcome"] = "low-effort"
            results.append(rec)
            continue

        on_topic, reason = comment_gate.worth_answering(text, now=after_probe)
        rec["relevance"] = reason
        if not on_topic:
            rec.update(outcome="off-topic")
            results.append(rec)
            continue

        require_number = comment_gate.needs_number(text)
        rec["require_number"] = require_number
        stage = ambassador.get_stage({"agent_replies": {}}, row["author"], text)
        rec["stage"] = stage

        draft = ambassador.generate_reply(
            row.get("post_title", ""), bodies.get(row["post_id"], ""),
            row["author"], text, stage,
            session_id=f"dryrun_{row['id']}")
        if not draft:
            rec.update(outcome="no draft")
            results.append(rec)
            continue
        rec["first_words"] = len(draft.split())

        dup = ambassador.check_reply_dedup(row["author"], draft)
        if dup:
            rec.update(outcome="dedup", detail=dup)
            results.append(rec)
            continue

        passed, problems = comment_gate.check_reply(
            draft, kb, require_number=require_number, comment_text=text)
        rec["first_problems"] = "; ".join(problems)[:300]
        redrafted = False
        if not passed:
            note = comment_gate.redraft_note(draft)
            if note:
                second = ambassador.generate_reply(
                    row.get("post_title", ""), bodies.get(row["post_id"], ""),
                    row["author"], text, stage,
                    session_id=f"dryrun_{row['id']}", redraft_note=note)
                redrafted = True
                if second:
                    draft = second
                    passed, problems = comment_gate.check_reply(
                        draft, kb, require_number=require_number, comment_text=text)

        rec.update(draft=draft, redrafted=redrafted, words=len(draft.split()))
        if not passed:
            rec.update(outcome="gate", detail="; ".join(problems)[:300])
        else:
            # The last rule before the network, the one `post_reply` applies.
            broken = content_violations("", draft)
            if broken:
                rec.update(outcome="content rule", detail=", ".join(broken))
            else:
                rec.update(outcome="would post",
                           score=suitability(row, draft, reason), comment=text)
        results.append(rec)
        print(f"  {i}/{len(rows)} {row['author'][:20]:22} "
              f"{'Q' if probe_ok else '-'} {rec['outcome']}", file=sys.stderr)

    print(json.dumps({"total": len(rows), "results": results},
                     ensure_ascii=False, indent=1),
          file=open(args.out, "w") if args.out else sys.stdout)


if __name__ == "__main__":
    main()
