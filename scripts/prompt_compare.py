#!/usr/bin/env python3
"""Old prompt against new, over the same fifty candidates the drafter refused.

The drafter refuses about 85 % of what reaches it — 687 of 809 over the
fourteen days to 2026-10-03, against 9 blocked by the gates. So the drafter is
where the branch is decided, and the only honest way to change its instruction
is to run both versions over the same inputs and look.

**What this measures, and what would not count as an improvement.** More drafts
is not the goal: a prompt that produces twelve drafts of which nine hang at
gate 2 has made things worse, because a blocked draft costs a model call and
Lars's attention and reaches nobody. So every new draft goes through gate 1 and
gate 2, and the figure that matters is drafts that *pass*.

**The new rule.** SKIP for advertising, shill, mint promotion and any post
without a verifiable claim. Draft when the post carries a technical claim about
identity, ownership, signature, authorization or provability — **even without
the word "agent"**. The five refusals read by hand on 2026-10-03 split exactly
along that line: three were promotion, two carried a real claim (a handle
minted as an NFT; proving wallet ownership with a short-lived signature) and
were refused anyway.

Target posts come from cdn.syndication.twimg.com, the public embed endpoint,
which costs nothing against the X bill. No post is read through our own
credentials for this.

    python3 scripts/prompt_compare.py --sample 50 --dry-run   # pick, fetch, no model
    python3 scripts/prompt_compare.py --sample 50             # the comparison
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx

from agents import reply_radar as rr
from agents import voice_gate
from app import paths

SKIP_RE = re.compile(r"skip (\d+) \(@(\S+)\) — nothing factual to say")
DRAFT_RE = re.compile(r"draft \d+ for (\d+) \(@(\S+), tier (\d+), (\w+)\): (PASS|BLOCKED)")
LINE_RE = re.compile(r"^\[(\S+)\] \w+: (.*)$")
SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"

# The addition, appended to the shipped prompt rather than replacing it. The
# rest of that instruction — one claim, sourced figures, no pitch — is not what
# went wrong, and rewriting it wholesale would make this comparison measure two
# changes at once.
NEW_RULE = """

**What to refuse, and what to answer — added 2026-10-04 after reading the
refusals by hand.**

Return SKIP for:
- advertising and product promotion, including our own kind of product
- shill posts, mint promotion, price talk, airdrop and allowlist posts
- anything carrying no claim a reader could check

Draft a reply when the post carries a **technical claim about identity,
ownership, signature, authorization or provability** — even if the word "agent"
never appears. A claim about a handle minted on-chain, a wallet proving
ownership with a short-lived signature, an access check that trusts the
caller's own assertion: all of these are the subject, whatever vocabulary the
post uses. On 2026-10-03 two such posts were refused as "nothing factual to
say", and they were the two best candidates of the day.

The test is the claim, not the topic word. A post full of the word "agent" and
empty of claims is promotion; a post about Stellar wallet recovery that states
how ownership is proven is on our subject."""


VARIANT3_RULE = """

**The source rule, added 2026-10-04 — this one decides whether you may write
at all.**

Answer only if **the post you are answering itself states a checkable number or
claim**, or **the citation index below carries one on this subject**. A
remembered paragraph number, a remembered date and a remembered figure are not
sources. "EU AI Act Article 12" from memory is not a citation; the same string
read off a page in the index, with that page's URL, is.

No hit in the index and no figure in the post: **SKIP**. That is the common
case and it is the right answer.

**Product announcements and advertising stay SKIP even when they carry a
number.** A launch post with a version number, a funding figure or a user count
is still promotion, and a reply to it reads as one more voice in somebody's
campaign. The number is not what makes a post answerable; a claim somebody
could check is.
"""


def log_path() -> str:
    return paths.logs("reply_radar.log")


def harvest(days: int = 14) -> tuple[list[dict], list[str]]:
    """(refused candidates, the texts of the drafts that passed the gates).

    The passed drafts become the few-shot examples: sixteen replies that
    cleared both gates and were delivered. Nothing invented, nothing rewritten.
    """
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    refused, examples = [], []
    pending = None
    for line in open(log_path(), errors="replace"):
        m = LINE_RE.match(line)
        if not m:
            continue
        at, msg = m.group(1), m.group(2).strip()
        if at < cut:
            continue
        s = SKIP_RE.search(msg)
        if s:
            refused.append({"id": s.group(1), "author": s.group(2), "at": at})
            pending = None
            continue
        d = DRAFT_RE.match(msg)
        if d:
            pending = d.group(5) if d.group(5) == "PASS" else None
            continue
        if pending == "PASS" and len(msg) > 60 and not msg.startswith("skip"):
            examples.append(msg)
            pending = None
    return refused, examples


def tier_for(author: str, targets: dict) -> int:
    """The radar's own tier, via the radar's own function.

    A second implementation would drift, and the stratification is the whole
    point of the sample.
    """
    entry = targets.get(author.lower()) or targets.get(author)
    if not entry:
        return 4
    return int(entry.get("tier", 4))


def stratified(refused: list[dict], targets: dict, n: int,
               seed: int = 20261004) -> list[dict]:
    """n candidates, keeping each tier's share of the whole.

    Seeded, so the same sample can be drawn again — a comparison nobody can
    repeat is an anecdote.
    """
    for r in refused:
        r["tier"] = tier_for(r["author"], targets)
    by_tier = collections.defaultdict(list)
    for r in refused:
        by_tier[r["tier"]].append(r)
    rng = random.Random(seed)
    total = len(refused)
    out = []
    for tier, rows in sorted(by_tier.items()):
        share = max(1, round(n * len(rows) / total)) if total else 0
        rng.shuffle(rows)
        out.extend(rows[:share])
    rng.shuffle(out)
    return out[:n]


def fetch_target(tid: str) -> dict | None:
    """The post text from the public embed endpoint. Costs nothing."""
    try:
        r = httpx.get(SYNDICATION, params={"id": tid, "lang": "en", "token": "0"},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=25,
                      follow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    return {"text": d.get("text") or "",
            "likes": d.get("favorite_count"),
            "replies": d.get("conversation_count")}


def ask(system: str, user: str, key: str) -> tuple[str, dict]:
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages",
                       headers={"x-api-key": key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                       json={"model": rr.MODEL, "max_tokens": 1500,
                             "system": system,
                             "messages": [{"role": "user", "content": user}]},
                       timeout=120)
    except Exception as e:
        return "", {"error": f"{type(e).__name__}"}
    if r.status_code != 200:
        return "", {"error": f"HTTP {r.status_code}"}
    body = r.json()
    text = "".join(b.get("text", "") for b in body.get("content", [])
                   if b.get("type") == "text").strip()
    return text, {"stop": body.get("stop_reason"),
                  "in": (body.get("usage") or {}).get("input_tokens"),
                  "out": (body.get("usage") or {}).get("output_tokens")}


def parse(raw: str) -> dict | None:
    """The drafter's own acceptance rule, so 'a draft' means the same thing."""
    if not raw or raw.upper().startswith("SKIP"):
        return None
    try:
        a, b = raw.find("{"), raw.rfind("}")
        d = json.loads(raw[a:b + 1])
    except Exception:
        return None
    text = str(d.get("reply", "")).strip().strip('"')
    return {"reply": text, "sources": d.get("sources") or []} if text else None


def gate(draft: dict, target_text: str, tid: str, author: str,
         kb: dict) -> dict:
    """Gates 1 and 2 with the sources the radar actually passes.

    The first version of this harness passed only the target post, and gate (h)
    blocked eleven of twelve new drafts for citing pages that were never in the
    dict. That was my measurement, not the prompt: agents/reply_radar.py builds
    sources from the KB pages the draft cited, plus the URLs it cited that the
    run then fetched, plus the post itself. Replicated here line for line,
    because a comparison whose gate is stricter than production measures the
    harness.
    """
    cited = [u for u in (draft.get("sources") or []) if isinstance(u, str)]
    sources = {u: kb[u] for u in cited if u in kb}
    try:
        sources.update(rr.fetch_sources(
            [u for u in cited if u not in kb] + _links(target_text)))
    except Exception as e:
        log_line(f"    (Quellen-Abruf fehlgeschlagen: {type(e).__name__})")
    sources.update({f"https://x.com/{author}/status/{tid} "
                    f"(the post being answered)": target_text})
    ok, problems, _ = rr.check(draft["reply"], sources)
    return {"pass": ok, "problems": problems, "sources_given": len(sources),
            "cited": cited}


def _links(text: str) -> list[str]:
    return re.findall(r"https?://\S+", text or "")


def log_line(msg: str) -> None:
    print(msg, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    targets = rr.load_targets() if hasattr(rr, "load_targets") else {}
    refused, examples = harvest(a.days)
    print(f"abgelehnte Kandidaten in {a.days} Tagen: {len(refused)}")
    print(f"zugestellte Entwürfe als Few-Shot      : {len(examples)}")
    sample = stratified(refused, targets, a.sample)
    tiers = collections.Counter(r["tier"] for r in sample)
    print(f"Stichprobe: {len(sample)}, nach Tier {dict(sorted(tiers.items()))}")

    fetched, missing = [], 0
    for r in sample:
        t = fetch_target(r["id"])
        if not t or not t["text"]:
            missing += 1
            continue
        fetched.append({**r, **t})
    print(f"Zielposts geholt: {len(fetched)} · nicht mehr erreichbar: {missing}")
    if a.dry_run:
        for f in fetched[:5]:
            print(f"\n  tier {f['tier']} @{f['author']}: {f['text'][:150]}")
        return 0

    key = rr.load_anthropic_key()
    if not key:
        print("kein Anthropic-Key")
        return 2
    kb = rr.load_kb()
    docs = voice_gate.load_voice_docs()
    voice = ("\n\n=== anti-KI-Sprech.md ===\n" + docs["anti_ki_sprech"]
             + "\n\n=== my-voice-en.md ===\n" + docs["my_voice_en"]
             + "\n\n=== our published pages ===\n" + rr.kb_digest(kb))
    shots = "\n\n=== replies of ours that passed both gates ===\n" + "\n\n".join(
        f"- {e}" for e in examples[:16])

    from citation_index import build as build_index, as_prompt as index_block
    idx = build_index()
    index = "\n\n" + index_block(idx)
    print(f"Zitierindex: {idx['counts']['posts']} Posts, "
          f"{idx['counts']['specs']} Spec-Seiten, {idx['counts']['figures']} Zahlen")

    variants = {
        "alt": rr.SYSTEM_PROMPT + voice,
        "neu": rr.SYSTEM_PROMPT + NEW_RULE + voice + shots,
        "v3": rr.SYSTEM_PROMPT + NEW_RULE + VARIANT3_RULE + voice + index + shots,
    }

    results = []
    for i, f in enumerate(fetched, 1):
        user = (f"Post by @{f['author']}:\n\n{f['text']}\n\n"
                "Write the reply, or SKIP.")
        row = {"id": f["id"], "author": f["author"], "tier": f["tier"],
               "text": f["text"][:300]}
        marks = []
        for name, system in variants.items():
            raw, meta = ask(system, user, key)
            d = parse(raw)
            row[name] = {"draft": d["reply"] if d else None,
                         "sources": d.get("sources") if d else None,
                         "meta": meta}
            if d:
                row[name]["gate"] = gate(d, f["text"], f["id"], f["author"], kb)
                marks.append(f"{name} {'pass' if row[name]['gate']['pass'] else 'BLOCK'}")
            else:
                marks.append(f"{name} SKIP")
        results.append(row)
        print(f"  [{i}/{len(fetched)}] tier {f['tier']} @{f['author']}: "
              + " · ".join(marks), flush=True)

    out = a.out or paths.data(f"prompt_compare_"
                              f"{datetime.datetime.now():%Y%m%d_%H%M}.json")
    paths.ensure(out)
    with open(out, "w") as fh:
        json.dump({"at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(), "sample": len(fetched),
            "few_shot": len(examples[:16]),
            "variants": list(variants), "results": results}, fh, indent=1)
    print(summarise(results))
    print(f"\nRohdaten: {out}")
    return 0


def summarise(results: list[dict], names=("alt", "neu", "v3")) -> str:
    def n_draft(k):
        return sum(1 for r in results if r.get(k, {}).get("draft"))

    def n_pass(k):
        return sum(1 for r in results if r.get(k, {}).get("draft")
                   and (r[k].get("gate") or {}).get("pass"))

    L = ["", "=" * 64,
         f"{len(results)} Kandidaten, {len(names)} Prompts, dieselben Eingaben",
         "",
         "                  " + "".join(f"{x:>8}" for x in names),
         "Entwürfe          " + "".join(f"{n_draft(x):>8}" for x in names),
         "davon Gate-pass   " + "".join(f"{n_pass(x):>8}" for x in names),
         "davon blockiert   " + "".join(
             f"{n_draft(x) - n_pass(x):>8}" for x in names), ""]
    rules = collections.Counter()
    for r in results:
        g = (r.get("v3") or {}).get("gate") or {}
        if r.get("v3", {}).get("draft") and not g.get("pass"):
            for p in g.get("problems") or ["(kein Grund)"]:
                rules[p.split(" — ")[0]] += 1
    if rules:
        L.append("v3-Blockgründe: " + ", ".join(f"{k} {v}×"
                                                for k, v in rules.most_common()))
    passes = [r for r in results
              if r.get("v3", {}).get("draft")
              and (r["v3"].get("gate") or {}).get("pass")]
    if passes:
        L += ["", "v3, bestanden:"]
        for r in passes:
            L.append(f"  tier {r['tier']} @{r['author']}")
            L.append(f"    Ziel : {r['text'][:120]}")
            L.append(f"    Reply: {r['v3']['draft'][:140]}")
            L.append(f"    zitiert: {', '.join(r['v3'].get('sources') or []) or '—'}")
    base = n_pass("alt")
    v3 = n_pass("v3")
    L += ["", f"Gate-pass: alt {base} · neu {n_pass('neu')} · v3 {v3}",
          "",
          ("v3 bringt die Zahl NICHT über %d — Befund: der Drafter ist nicht "
           "der Engpass, den ein Prompt löst." % base) if v3 <= base else
          ("v3 liegt über alt (%d → %d)." % (base, v3))]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
