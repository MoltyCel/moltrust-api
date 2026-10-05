"""Did the replies do anything. The figure the 18.10 decision rests on.

The reply branch is on probation: fourteen days from 2026-10-04, and without
measurable effect it is discontinued. This produces the number, and — more
important — says which part of it can carry a decision and which cannot.

**What each column is worth.**

    impressions     how many people saw it. Reliable, per reply.
    profile_clicks  how many went from the reply to our profile. The one
                    column that measures the reply doing something *for us*
                    rather than for somebody else's thread. Reliable, per reply.
    likes           weak at this scale: one like is one person.
    followers_24h   **not attributable.** Seven replies and a daily digest share
                    one follower count. A follower gained on a day with three
                    posts belongs to none of them in particular. Reported
                    because it was asked for, never as the deciding figure.

So the decision rule this offers is impressions and profile clicks per reply,
against the same two for the digest series over the same days — the comparison
the video series already uses. A branch that costs $0.22 a draft and returns
no profile click in two weeks has not earned the slot.

    python3 scripts/reply_impact.py              # the table
    python3 scripts/reply_impact.py --send       # and to Telegram
    python3 scripts/reply_impact.py --since 2026-10-04
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify

BASE = os.path.expanduser("~/moltstack")
METRICS = os.path.join(BASE, "data", "digest_metrics.jsonl")
DECISION_DATE = "2026-10-18"
WINDOW_START = "2026-10-04"

# Fixed on 2026-10-04, before the window it judges, and recorded in
# docs/reply-radar.md so it cannot drift. Both conditions, not either.
#
# A median rather than a mean: one reply under a 53 000-impression target post
# would carry a mean by itself and say nothing about the other twelve. And
# profile clicks as the second condition, because impressions alone measure
# somebody else's thread — four hundred people scrolling past is not an effect.
MEDIAN_IMPRESSIONS_MIN = 30
CLICKS_PER_REPLY_MIN = 0.5


def verdict(k: dict) -> dict:
    """Continue or discontinue, computed from the thresholds, not from a mood."""
    w = k["window"]
    n = w["impressions"]["n"]
    median = w["impressions"]["median"]
    clicks = w["profile_clicks"]["sum"]
    ratio = (clicks / n) if n else None
    met_median = median is not None and median >= MEDIAN_IMPRESSIONS_MIN
    met_clicks = ratio is not None and ratio >= CLICKS_PER_REPLY_MIN
    return {
        "replies": n, "median_impressions": median,
        "clicks": clicks, "clicks_per_reply": round(ratio, 2) if ratio else ratio,
        "median_ok": met_median, "clicks_ok": met_clicks,
        # No replies at all is not a pass. A branch that produced nothing in
        # fourteen days has answered the question it was asked.
        "continue": bool(n and met_median and met_clicks),
    }


RADAR_STATE = os.path.join(BASE, "data", "reply_radar_state.json")


def by_prompt(days: int = 14) -> dict:
    """Candidates, drafts and gate-passes per day per prompt version.

    Separated because the drafter changed on 2026-10-05 and the 18.10 decision
    has to be read against the version that was running. A rate averaged over
    two prompts describes neither.
    """
    try:
        with open(RADAR_STATE) as f:
            book = (json.load(f).get("by_prompt") or {})
    except Exception:
        return {}
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    return {d: v for d, v in book.items() if d >= cut}


def rows(kind: str) -> list[dict]:
    out = []
    try:
        with open(METRICS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("kind") == kind:
                    out.append(r)
    except OSError:
        return []
    return out


def latest_per_post(kind: str, since: str | None = None) -> list[dict]:
    """The newest measurement of each post — the one with the final numbers."""
    best: dict[str, dict] = {}
    for r in rows(kind):
        tid = r.get("tweet_id")
        if not tid:
            continue
        if since and (r.get("posted_at") or "") < since:
            continue
        cur = best.get(tid)
        if cur is None or (r.get("measured_at") or "") > (cur.get("measured_at") or ""):
            best[tid] = r
    return sorted(best.values(), key=lambda r: r.get("posted_at") or "")


# Enforced on read, not only on write. The first version of the writer allowed
# up to 48 h and stamped one delta at 47.3 h; the row stays as the record of
# what was measured, and this refuses to report it as a 24-hour figure. Same
# shape as the claim guard, which withheld claims for four days because its
# window was only applied when writing.
READ_WINDOW = (12.0, 36.0)


def delta_24h(tid: str) -> tuple[int | None, float | None]:
    """The 24 h follower delta if a measurement fell in the window, else None.

    Deliberately not the nearest available number. A delta taken at 80 hours is
    not a 24-hour delta, and filling the column from it would make four
    unmeasured replies look measured.
    """
    best = None
    for r in rows("reply"):
        if r.get("tweet_id") != tid or r.get("followers_delta_24h") is None:
            continue
        age = r.get("delta_24h_taken_at_hours")
        if age is None or not READ_WINDOW[0] <= age <= READ_WINDOW[1]:
            continue
        # Closest to 24 h wins, so a second measurement inside the window does
        # not overwrite a better one by arriving later.
        if best is None or abs(age - 24) < abs(best[1] - 24):
            best = (r["followers_delta_24h"], age)
    return best if best else (None, None)


def collect(since: str = WINDOW_START) -> dict:
    replies = latest_per_post("reply")
    in_window = [r for r in replies if (r.get("posted_at") or "") >= since]
    before = [r for r in replies if (r.get("posted_at") or "") < since]
    digests = [r for r in latest_per_post("digest")
               if (r.get("posted_at") or "") >= since]

    def agg(rs, field):
        vals = [r.get(field) for r in rs if isinstance(r.get(field), int)]
        return {"n": len(vals), "sum": sum(vals),
                "median": statistics.median(vals) if vals else None,
                "max": max(vals) if vals else None} if vals else {
                    "n": 0, "sum": 0, "median": None, "max": None}

    return {
        "since": since, "decision_date": DECISION_DATE,
        "by_prompt": by_prompt(),
        "replies_in_window": [_shape(r) for r in in_window],
        "replies_before": [_shape(r) for r in before],
        "window": {f: agg(in_window, f) for f in
                   ("impressions", "profile_clicks", "likes", "engagements")},
        "retro": {f: agg(before, f) for f in
                  ("impressions", "profile_clicks", "likes", "engagements")},
        "digest": {f: agg(digests, f) for f in ("impressions", "likes")},
        "digest_n": len(digests),
    }


def _shape(r: dict) -> dict:
    d24, at = delta_24h(r.get("tweet_id", ""))
    return {"tweet_id": r.get("tweet_id"), "posted_at": r.get("posted_at"),
            "route": r.get("route"), "impressions": r.get("impressions"),
            "likes": r.get("likes"), "profile_clicks": r.get("profile_clicks"),
            "engagements": r.get("engagements"),
            "target_impressions": r.get("target_impressions"),
            "followers_delta_24h": d24, "delta_taken_at_hours": at}


def format_report(k: dict) -> str:
    L = [f"🔁 <b>Reply-Wirkung</b> — Fenster ab {k['since']}, "
         f"Entscheidung {k['decision_date']}", ""]
    for label, key, rs in (("Im Fenster", "window", k["replies_in_window"]),
                           ("Rückwirkend (vor dem Fenster)", "retro",
                            k["replies_before"])):
        if not rs:
            L += [f"<b>{label}</b>: keine", ""]
            continue
        L.append(f"<b>{label}</b> — {len(rs)} Replies")
        L.append("<pre>")
        L.append("Datum       Impr  Likes  Klicks  Ziel-Impr  Δ24h")
        for r in rs:
            d = ("—" if r["followers_delta_24h"] is None
                 else f"{r['followers_delta_24h']:+d}@{r['delta_taken_at_hours']:.0f}h")
            L.append(f"{(r['posted_at'] or '?')[:10]}  {_n(r['impressions']):>4}  "
                     f"{_n(r['likes']):>5}  {_n(r['profile_clicks']):>6}  "
                     f"{_n(r['target_impressions']):>9}  {d}")
        L.append("</pre>")
        a = k[key]
        L.append(f"Summe: {a['impressions']['sum']} Impressionen · "
                 f"{a['profile_clicks']['sum']} Profilklicks · "
                 f"{a['likes']['sum']} Likes")
        if a["impressions"]["n"]:
            L.append(f"Median je Reply: {a['impressions']['median']:.0f} Impr · "
                     f"max {a['impressions']['max']}")
        L.append("")

    d = k["digest"]
    if k["digest_n"]:
        L += [f"<b>Digest im selben Fenster</b> — {k['digest_n']} Posts, "
              f"Median {d['impressions']['median']:.0f} Impr je Post", ""]
    else:
        L += ["<b>Digest im selben Fenster</b>: keine Messung", ""]

    bp = k.get("by_prompt") or {}
    if bp:
        L += ["<b>Entwürfe je Kandidat, getrennt nach Prompt-Version</b>",
              "<pre>",
              "Tag         Version          Läufe  Kand  Entw  Gate  Quote"]
        for day in sorted(bp):
            for ver, r in sorted(bp[day].items()):
                cand = r.get("candidates") or 0
                rate = (r.get("drafts", 0) / cand * 100) if cand else 0
                L.append("%-10s  %-15s %5d %5d %5d %5d %5.0f %%"
                         % (day, ver, r.get("runs", 0), cand,
                            r.get("drafts", 0), r.get("gate_pass", 0), rate))
        L += ["</pre>", ""]
    v = verdict(k)
    L += ["<b>Schwelle, festgeschrieben am 2026-10-04</b>",
          f"Median Impressionen je Reply: <b>{_n(v['median_impressions'])}</b> "
          f"gegen ≥ {MEDIAN_IMPRESSIONS_MIN} → "
          f"{'erfüllt' if v['median_ok'] else 'nicht erfüllt'}",
          f"Profilklicks je Reply: <b>{_n(v['clicks_per_reply'])}</b> "
          f"gegen ≥ {CLICKS_PER_REPLY_MIN} → "
          f"{'erfüllt' if v['clicks_ok'] else 'nicht erfüllt'}",
          f"<b>{'Fortführung' if v['continue'] else 'Einstellung'}</b>"
          + ("" if v["continue"] else " — Radar abschalten, Budget auf Null")
          + (f" ({v['replies']} Replies im Fenster)" if v["replies"]
             else " (keine Replies im Fenster — das ist kein Bestehen)"),
          "",
          "<b>Was die Zahlen tragen</b>",
          "Impressionen und Profilklicks sind je Reply belastbar. Der "
          "Follower-Δ24h ist es nicht: sieben Replies und der Digest teilen "
          "einen Followerstand, ein Follower an einem Tag mit drei Posts "
          "gehört keinem davon. Steht als Spalte da, weil er beauftragt ist, "
          "nie als entscheidende Zahl.",
          "",
          "Entscheidungsgrundlage am " + k["decision_date"] + ": Impressionen "
          "und Profilklicks je Reply gegen dieselben zwei der Digest-Reihe im "
          "selben Zeitraum."]
    return "\n".join(L)


def _n(v) -> str:
    return "—" if v is None else str(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=WINDOW_START)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    k = collect(a.since)
    print(json.dumps(k, indent=1, ensure_ascii=False) if a.json
          else format_report(k))
    if a.send:
        notify.send_telegram(format_report(k), channel=notify.STATS,
                             parse_mode="HTML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
