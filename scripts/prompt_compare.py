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


def gate(draft: dict, target_text: str, tid: str, author: str) -> dict:
    """Gates 1 and 2, exactly as the radar runs them."""
    sources = {f"https://x.com/{author}/status/{tid} (the post being answered)":
               target_text}
    ok, problems, _ = rr.check(draft["reply"], sources)
    return {"pass": ok, "problems": problems}


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
    base = (rr.SYSTEM_PROMPT
            + "\n\n=== anti-KI-Sprech.md ===\n" + docs["anti_ki_sprech"]
            + "\n\n=== my-voice-en.md ===\n" + docs["my_voice_en"]
            + "\n\n=== our published pages ===\n" + rr.kb_digest(kb))
    shots = "\n\n=== replies of ours that passed both gates ===\n" + "\n\n".join(
        f"- {e}" for e in examples[:16])
    old_system = base
    new_system = (rr.SYSTEM_PROMPT + NEW_RULE
                  + "\n\n=== anti-KI-Sprech.md ===\n" + docs["anti_ki_sprech"]
                  + "\n\n=== my-voice-en.md ===\n" + docs["my_voice_en"]
                  + "\n\n=== our published pages ===\n" + rr.kb_digest(kb)
                  + shots)

    results = []
    for i, f in enumerate(fetched, 1):
        user = (f"Post by @{f['author']}:\n\n{f['text']}\n\n"
                "Write the reply, or SKIP.")
        raw_old, m_old = ask(old_system, user, key)
        raw_new, m_new = ask(new_system, user, key)
        d_old, d_new = parse(raw_old), parse(raw_new)
        row = {"id": f["id"], "author": f["author"], "tier": f["tier"],
               "text": f["text"][:300],
               "old": {"draft": d_old["reply"] if d_old else None,
                       "meta": m_old},
               "new": {"draft": d_new["reply"] if d_new else None,
                       "meta": m_new}}
        if d_new:
            row["new"]["gate"] = gate(d_new, f["text"], f["id"], f["author"])
        if d_old:
            row["old"]["gate"] = gate(d_old, f["text"], f["id"], f["author"])
        results.append(row)
        print(f"  [{i}/{len(fetched)}] tier {f['tier']} @{f['author']}: "
              f"alt {'Entwurf' if d_old else 'SKIP'} · "
              f"neu {'Entwurf' if d_new else 'SKIP'}"
              + ("" if not d_new else
                 (" (Gate " + ("pass" if row["new"]["gate"]["pass"]
                               else "BLOCKED") + ")")))

    out = a.out or paths.data(f"prompt_compare_"
                              f"{datetime.datetime.now():%Y%m%d_%H%M}.json")
    paths.ensure(out)
    with open(out, "w") as fh:
        json.dump({"at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(), "sample": len(fetched),
            "few_shot": len(examples[:16]), "results": results}, fh, indent=1)
    print(summarise(results))
    print(f"\nRohdaten: {out}")
    return 0


def summarise(results: list[dict]) -> str:
    def passed(side):
        return sum(1 for r in results if r[side]["draft"]
                   and r[side].get("gate", {}).get("pass"))

    def drafted(side):
        return sum(1 for r in results if r[side]["draft"])

    def blocked(side):
        return sum(1 for r in results if r[side]["draft"]
                   and not r[side].get("gate", {}).get("pass"))

    n = len(results)
    L = ["", "=" * 62,
         f"{n} Kandidaten, beide Prompts, dieselben Eingaben", "",
         f"{'':18} {'alt':>8} {'neu':>8}",
         f"{'Entwürfe':18} {drafted('old'):>8} {drafted('new'):>8}",
         f"{'davon Gate-pass':18} {passed('old'):>8} {passed('new'):>8}",
         f"{'davon blockiert':18} {blocked('old'):>8} {blocked('new'):>8}", ""]
    both = sum(1 for r in results if r["old"]["draft"] and r["new"]["draft"])
    only_new = [r for r in results if r["new"]["draft"] and not r["old"]["draft"]]
    only_old = [r for r in results if r["old"]["draft"] and not r["new"]["draft"]]
    neither = sum(1 for r in results
                  if not r["old"]["draft"] and not r["new"]["draft"])
    L += [f"beide entworfen : {both}",
          f"nur neu         : {len(only_new)}",
          f"nur alt         : {len(only_old)}",
          f"beide SKIP      : {neither}", ""]
    if only_new:
        L.append("Wo der neue Prompt entwirft und der alte nicht:")
        for r in only_new[:6]:
            g = r["new"].get("gate", {})
            L.append(f"  tier {r['tier']} @{r['author']} "
                     f"[{'pass' if g.get('pass') else 'BLOCKED: ' + '; '.join(g.get('problems', []))[:60]}]")
            L.append(f"    Ziel : {r['text'][:110]}")
            L.append(f"    Reply: {r['new']['draft'][:110]}")
    if only_old:
        L.append("")
        L.append("Wo der alte entwirft und der neue nicht (Regression prüfen):")
        for r in only_old[:4]:
            L.append(f"  tier {r['tier']} @{r['author']}: {r['text'][:100]}")
    gain = passed("new") - passed("old")
    L += ["", f"Zugestellte Entwürfe: {passed('old')} → {passed('new')} "
          f"({gain:+d})",
          "Mehr Entwürfe, die am Gate hängen, sind keine Verbesserung — "
          "deshalb entscheidet die Gate-pass-Zeile, nicht die Entwurfszeile."]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
