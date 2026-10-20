"""Which bound is actually binding on the reply radar: supply, drafter, or cap.

Written because the three are easy to confuse and the answer decides where the
money goes. On 03.10.2026 `LIST_PAGE` was halved on a cost argument while the
list was delivering drafts at $0.22 each and the search at $2.02 — the cheap
saving and the productive leg were the same leg.

Three bounds, and exactly one of them binds on any given day:

    supply    few candidates reached the drafter at all
    drafter   candidates were plentiful and the model refused nearly all
    cap       the run cap (3) or the daily cap (8) was reached

The distinction is not cosmetic. A supply-bound radar gets better by reading
more, which costs money. A drafter-bound one gets better by changing the
prompt, which costs nothing. A cap-bound one is working as designed and should
be left alone.

    python3 scripts/radar_bottleneck.py            # last 7 days
    python3 scripts/radar_bottleneck.py --days 14
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.expanduser("~/moltstack")
LOG = os.path.join(BASE, "logs", "reply_radar.log")
LEDGER = os.path.join(BASE, "data", "x_meter.jsonl")

LINE = re.compile(r"^\[(\S+)\] \w+: (.*)$")
DRAFT = re.compile(r"draft \d+ for (\d+) \(@(\S+), tier (\d+), (\w+)\): (PASS|BLOCKED)")
RULE = re.compile(r"^(g\d[a-z_]*)\b")

USD_PER_POST = 0.005
USD_PER_USER = 0.010
# Three a run, eight a day — agents/reply_radar.py. Named here so "cap-bound"
# is measured against the real numbers rather than a remembered pair.
PER_RUN = 3


def collect(days: int = 7) -> dict:
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    runs: list[dict] = []
    per_source = collections.Counter()
    delivered = collections.Counter()
    gate_rules = collections.Counter()
    skips = caps = 0
    cur_draft = None
    try:
        lines = open(LOG, errors="replace").read().splitlines()
    except OSError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    for line in lines:
        m = LINE.match(line)
        if not m:
            continue
        at, t = m.group(1), m.group(2).strip()
        if at < cut:
            continue
        if t.startswith("REPLY RADAR"):
            runs.append({"at": at, "cand": 0, "drafts": 0})
        elif t.startswith("Candidates after filtering:") and runs:
            runs[-1]["cand"] = int(re.search(r"filtering: (\d+)", t).group(1))
        elif DRAFT.match(t):
            g = DRAFT.match(t)
            cur_draft = g
            per_source[g.group(4)] += 1
            if runs:
                runs[-1]["drafts"] += 1
            if g.group(5) == "PASS":
                delivered[g.group(4)] += 1
        elif cur_draft is not None and RULE.match(t):
            gate_rules[RULE.match(t).group(1)] += 1
        elif t.startswith("skip "):
            if "nothing factual to say" in t:
                skips += 1
            elif "already has a draft" in t:
                caps += 1

    cand = sum(r["cand"] for r in runs)
    drafts = sum(r["drafts"] for r in runs)
    capped_runs = sum(1 for r in runs if r["drafts"] >= PER_RUN)
    cost = leg_cost(cut)
    return {"days": days, "runs": len(runs), "candidates": cand,
            "drafter_skips": skips, "author_caps": caps, "drafts": drafts,
            "delivered": dict(delivered), "by_source": dict(per_source),
            "gate_rules": dict(gate_rules), "capped_runs": capped_runs,
            "cost": cost, "bottleneck": judge(cand, skips, drafts, capped_runs,
                                              len(runs))}


def leg_cost(cut: str) -> dict:
    """Per-leg cost with X's per-UTC-day deduplication, so the two legs are
    comparable rather than merely both large."""
    per_day: dict[tuple, tuple[set, set]] = {}
    try:
        with open(LEDGER) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                at = r.get("at") or ""
                if at < cut or r.get("kind") != "read":
                    continue
                src = r.get("source")
                if src not in ("recent", "tweets"):
                    continue
                key = (at[:10], src)
                p, u = per_day.setdefault(key, (set(), set()))
                p.update(r.get("posts") or [])
                u.update(r.get("users") or [])
    except OSError:
        return {}
    out = collections.Counter()
    for (_, src), (p, u) in per_day.items():
        out[src] += len(p) * USD_PER_POST + len(u) * USD_PER_USER
    return {"list": round(out["tweets"], 3), "search": round(out["recent"], 3)}


def judge(cand: int, skips: int, drafts: int, capped_runs: int,
          runs: int) -> dict:
    """Which bound binds, and the evidence for saying so."""
    if not runs:
        return {"bound": "unknown", "why": "no runs in the window"}
    if runs and capped_runs / runs >= 0.5:
        return {"bound": "cap",
                "why": f"{capped_runs} of {runs} runs hit the {PER_RUN}-draft "
                       f"cap — more candidates would not produce more drafts"}
    rate = (skips / cand) if cand else 0
    per_run = cand / runs
    # Both can be true at once; the one that is cheaper to move decides. A
    # prompt change costs nothing and a read costs money, so a high refusal
    # rate is reported as the drafter's even when supply is also thin.
    if rate >= 0.75 and per_run >= 5:
        return {"bound": "drafter",
                "why": f"{skips} of {cand} candidates refused before any gate "
                       f"({rate * 100:.0f} %), {per_run:.0f} candidates a run — "
                       f"the prompt is the cheap lever, not more reads"}
    if per_run < 5:
        return {"bound": "supply",
                "why": f"only {per_run:.1f} candidates a run reached the "
                       f"drafter; at a {rate * 100:.0f} % refusal rate that is "
                       f"below one draft a run, and reading less makes it worse"}
    return {"bound": "none",
            "why": f"{drafts} drafts from {cand} candidates over {runs} runs, "
                   f"no bound dominating"}


def format_report(k: dict) -> str:
    if k.get("error"):
        return f"Radar-Engpass: Log nicht lesbar — {k['error']}"
    c, b = k["cost"], k["bottleneck"]
    L = [f"📐 <b>Radar-Engpass — {k['days']} Tage</b>", "",
         f"Läufe {k['runs']} · Kandidaten {k['candidates']} · "
         f"Drafter-SKIP {k['drafter_skips']} · Entwürfe {k['drafts']}",
         f"Zugestellt: {k['delivered'] or 'keine'}",
         f"Läufe am 3er-Deckel: {k['capped_runs']}", ""]
    if c:
        for leg, label in (("list", "Liste"), ("search", "Suche")):
            n = k["delivered"].get("list" if leg == "list" else "search", 0)
            usd = c.get(leg, 0.0)
            per = f"${usd / n:.2f}" if n else "— (kein Entwurf)"
            L.append(f"{label}: ${usd:.2f} → {n} zugestellt → {per} je Entwurf")
        L.append("")
    if k["gate_rules"]:
        L.append("Gate-Blocks: " + ", ".join(
            f"{r} {n}×" for r, n in sorted(k["gate_rules"].items(),
                                           key=lambda kv: -kv[1])))
    L += ["", f"<b>Engpass: {b['bound']}</b>", b["why"]]
    if b["bound"] == "supply":
        L.append("")
        L.append("Das ist die Lage, in der ein Lese-Deckel die falsche Grenze "
                 "ist. Vor dem nächsten Schnitt: Kosten je zugestelltem "
                 "Entwurf je Zweig vergleichen, nicht Kosten je Tag.")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    k = collect(a.days)
    print(json.dumps(k, indent=1, ensure_ascii=False) if a.json
          else format_report(k))
    if a.send:
        from app import notify
        notify.send_telegram(format_report(k), channel=notify.STATS,
                             parse_mode="HTML")
    return 0 if k.get("bottleneck", {}).get("bound") in ("cap", "none") else 1


if __name__ == "__main__":
    raise SystemExit(main())
