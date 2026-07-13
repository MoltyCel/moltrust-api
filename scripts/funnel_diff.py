#!/usr/bin/env python3
"""Ambassador funnel diff vs a baseline snapshot + Telegram notify.

Parses workspace/ambassador/MEMORY.md (same unique-agent funnel methodology as the
T0 snapshot), compares against a baseline .md, writes a markdown report, and pushes
a 5-line Telegram summary. Used by the T+2W / T+4W measurement timers.

Usage:
  funnel_diff.py --baseline /home/moltstack/Downloads/ambassador-funnel-T0-20260619.md --label T+2W
  add --dry-run to skip Telegram and write report to /tmp (sanity testing).
"""
import argparse
import re
import subprocess
import sys
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app import notify

MEMORY = Path("/home/moltstack/moltstack/agents/workspace/ambassador/MEMORY.md")
SECRETS = Path("/home/moltstack/.moltrust_secrets")
OUTDIR = Path("/home/moltstack/Downloads")
RANK = {"first_contact": 1, "second_contact": 2, "verified": 3}


def parse_funnel(path: Path) -> dict:
    txt = path.read_text(encoding="utf-8")
    entries = []
    cur = None
    for ln in txt.splitlines():
        h = re.match(r"^### (.+?) — (\d{4}-\d{2}-\d{2}) — ", ln)
        if h:
            cur = {"h": h.group(1).strip(), "s": None}
            entries.append(cur)
            continue
        s = re.match(r"^→ Status: ([a-z_]+)", ln)
        if s and cur:
            cur["s"] = s.group(1)
    entries = [e for e in entries if e["s"]]
    amax = defaultdict(int)
    for e in entries:
        amax[e["h"]] = max(amax[e["h"]], RANK[e["s"]])
    u = len(amax) or 1
    reached_first = sum(1 for v in amax.values() if v >= 1)
    reached_second = sum(1 for v in amax.values() if v >= 2)
    reached_verified = sum(1 for v in amax.values() if v >= 3)
    stuck_first = sum(1 for v in amax.values() if v == 1)
    return {
        "entries": len(entries),
        "unique": len(amax),
        "reached_first": reached_first,
        "reached_second": reached_second,
        "reached_verified": reached_verified,
        "stuck_first": stuck_first,
        "dropoff_pct": round(stuck_first / u * 100, 1),
        "s2v_pct": round(reached_verified / reached_second * 100, 1) if reached_second else 0.0,
    }


def parse_baseline(path: Path) -> dict:
    txt = path.read_text(encoding="utf-8")
    out = {"dropoff_pct": 35.9, "s2v_pct": 78.0, "verified": 32}  # fallback = T0
    m = re.search(r"drop-?off rate:\s*\d+/\d+\s*=\s*([\d.]+)%", txt)
    if m:
        out["dropoff_pct"] = float(m.group(1))
    m = re.search(r"Stage-2 ?(?:->|→) ?Verified.*?=\s*([\d.]+)%", txt)
    if m:
        out["s2v_pct"] = float(m.group(1))
    m = re.search(r"reached[_ ]verified[:|]\s*(\d+)", txt) or re.search(r"\bverified\b[^\d]*(\d+)\s*$", txt, re.M)
    if m:
        out["verified"] = int(m.group(1))
    return out


def telegram(msg: str) -> bool:
    if not notify.telegram_allowed("funnel_diff.telegram"):
        return False
    token = chat = None
    for ln in SECRETS.read_text().splitlines():
        ln = ln.strip()
        if ln.startswith("TELEGRAM_BOT_TOKEN="):
            token = ln.split("=", 1)[1].strip().strip('"').strip("'")
        elif ln.startswith("TELEGRAM_CHAT_ID="):
            chat = ln.split("=", 1)[1].strip().strip('"').strip("'")
    if not token or not chat:
        print("telegram: token/chat missing", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception as e:
        print(f"telegram error: {e}", file=sys.stderr)
        return False


def self_remove(label: str):
    """Remove this run's own line from the moltstack user crontab (no sudo)."""
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        kept = [l for l in cur.splitlines()
                if not ("funnel_diff.py" in l and f"--label {label}" in l)]
        subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True)
        print(f"self-removed crontab line for {label}")
    except Exception as e:
        print(f"self-remove failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-remove", action="store_true",
                    help="remove own crontab line after a successful run")
    args = ap.parse_args()

    cur = parse_funnel(MEMORY)
    base = parse_baseline(Path(args.baseline))
    d_drop = round(cur["dropoff_pct"] - base["dropoff_pct"], 1)
    d_s2v = round(cur["s2v_pct"] - base["s2v_pct"], 1)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    verdict = ("improved (keep hook)" if cur["dropoff_pct"] < base["dropoff_pct"] - 2
               else "worse (consider revert)" if cur["dropoff_pct"] > base["dropoff_pct"] + 2
               else "inconclusive")

    outdir = Path("/tmp") if args.dry_run else OUTDIR
    outdir.mkdir(parents=True, exist_ok=True)
    report = outdir / f"ambassador-funnel-{args.label}-{date_str}.md"
    report.write_text(f"""# Ambassador Funnel — {args.label} ({date_str})

Comparison vs baseline `{args.baseline}` (T0 = 2026-06-19).

| Metric | T0 baseline | {args.label} | Δ |
|---|---|---|---|
| Stage-1→2 drop-off | {base['dropoff_pct']}% | {cur['dropoff_pct']}% | {d_drop:+.1f} pp |
| Stage-2→Verified | {base['s2v_pct']}% | {cur['s2v_pct']}% | {d_s2v:+.1f} pp |

## Current funnel (unique agents = {cur['unique']}, entries = {cur['entries']})
- reached ≥first: {cur['reached_first']}
- reached ≥second: {cur['reached_second']}
- reached verified: {cur['reached_verified']}
- stuck at first (drop-off): {cur['stuck_first']}

## Verdict: **{verdict}**
Target was drop-off < 25%. Decision (keep vs revert hook) due after T+4W.
""", encoding="utf-8")

    summary = (f"Ambassador funnel {args.label}\n"
               f"Drop-off Stage-1→2 (Baseline {base['dropoff_pct']}%): {args.label} {cur['dropoff_pct']}%, Δ {d_drop:+.1f} percentage points. "
               f"Stage-2→3 (Baseline {base['s2v_pct']}%): {args.label} {cur['s2v_pct']}%. "
               f"Verified-Count: {cur['reached_verified']}. "
               f"Verdict: {verdict}. Voller Report in Downloads ({report.name}).")
    print(summary)
    print(f"\nreport: {report}")
    if not args.dry_run:
        print("telegram sent:", telegram(summary))
        if args.self_remove:
            self_remove(args.label)


if __name__ == "__main__":
    main()
