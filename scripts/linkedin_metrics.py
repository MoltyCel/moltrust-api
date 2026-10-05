"""The LinkedIn series, kept by hand until there is an app to keep it.

LinkedIn is the only channel we post to without an API. `agents/syndicate.py`
drafts the post, Lars pastes it into the company page, and the numbers live in
LinkedIn's own analytics — nowhere we can read. So the table exists now and the
capture is manual, which means two things have to be true or the series is
worthless:

**The schema is fixed before the first row.** A column added later leaves every
earlier row null, and a series with holes cannot be compared across weeks. The
columns below are exactly what the LinkedIn post analytics panel shows, so
reading them off is transcription and not judgement.

**A missing row says so.** An unanswered Sunday prompt leaves `pending`, never
a zero. Nothing here ever guesses a number: that is the whole difference
between this file and remembering roughly how a post did.

    python3 scripts/linkedin_metrics.py --prompt          # Sunday: ask
    python3 scripts/linkedin_metrics.py --record <file>   # write the answers
    python3 scripts/linkedin_metrics.py --table           # what we have
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify

from app import paths

# None means "ask app.paths". A path assigned here wins, which is how a
# test redirects the series — and the reason the first version wrote three
# fixture rows into the live file is that these were constants.
SERIES: str | None = None
DRAFTS: str | None = None


def series_path() -> str:
    return SERIES or paths.data("linkedin_metrics.jsonl")


def drafts_path() -> str:
    return DRAFTS or paths.data("linkedin_drafts.jsonl")

# Exactly the figures the post analytics panel shows, in its order, so that
# reading them off is transcription. `followers_total` is the page figure at
# the time of reading, not a delta: a delta needs two readings and the second
# one is next Sunday's.
# `impressions` counts how often the post was put on a screen,
# `members_reached` counts how many people saw it. On the first measured post
# that was 59 against 20, and the gap is the finding — a format that is served
# three times to the same twenty people is not a format that reached sixty.
# Both stand as their own columns so no later reader has to guess which one a
# number was.
#
# `link_engagements` is not mapped onto `clicks`. The panel reports them as
# separate things and we do not know they are the same, so the export's name
# is kept and `clicks` stays absent where the panel did not give it.
COLUMNS = ("key", "posted_at", "url", "topic", "format",
           "impressions", "members_reached",
           "reactions", "comments", "reposts", "saves", "sends",
           "clicks", "link_engagements",
           "video_views", "watch_time", "avg_watch_time",
           "profile_views_from_post", "followers_gained",
           "followers_total", "read_at")

# What LinkedIn calls them, so the prompt and the panel use the same words.
LABELS = {"impressions": "Impressions", "members_reached": "Members reached",
          "reactions": "Reactions", "comments": "Comments",
          "reposts": "Reposts", "saves": "Saves", "sends": "Sends",
          "clicks": "Clicks (Link + Page)",
          "link_engagements": "Link engagements",
          "video_views": "Video views", "watch_time": "Watch time (s)",
          "avg_watch_time": "Average watch time (s)",
          "profile_views_from_post": "Profile views from post",
          "followers_gained": "Followers gained",
          "followers_total": "Page followers (total, heute)"}

# What the prompt asks for, by format. A text post has no watch time, and
# asking for it invites a zero where the right answer is "not applicable".
ASK = {
    "video": ("impressions", "members_reached", "video_views", "watch_time",
              "avg_watch_time", "reactions", "comments", "reposts", "saves",
              "sends", "profile_views_from_post", "followers_gained"),
    None: ("impressions", "members_reached", "reactions", "comments",
           "reposts", "saves", "sends", "clicks", "link_engagements",
           "profile_views_from_post", "followers_gained"),
}


def read(path: str) -> list[dict]:
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return out


def append(row: dict, path: str | None = None) -> None:
    """Resolved at call time, not at import.

    `path: str = SERIES` binds the module constant when the function is
    defined, so reassigning SERIES afterwards changes nothing and every write
    goes to the old file. Exactly the defect x_meter.spend() carried until
    2026-10-03, found there by a test that could not redirect it — and found
    here the same way.
    """
    path = path or series_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    os.chmod(path, 0o640)


def outstanding() -> list[dict]:
    """Drafts that went out and have no numbers yet.

    The draft ledger is what syndicate.py writes when it hands a LinkedIn post
    to Telegram. A draft Lars never posted is not outstanding — it is absent,
    and `--record` marks it `not_posted` rather than leaving it to look owed
    forever.
    """
    # A series row only answers a draft once it carries a figure. It used to be
    # enough that the row existed, and `record()` in agents/linkedin_post.py
    # creates one the moment a post goes out — so every posted share looked
    # answered immediately and was never asked about. Found on 2026-10-05, when
    # the first real post produced "Keine offenen Posts" with its own pending
    # row sitting in the series.
    FIGURES = ("impressions", "reactions", "comments", "clicks")

    def answered(r: dict) -> bool:
        if r.get("not_posted"):
            return True          # explicitly not owed, see --record
        return any(r.get(k) is not None for k in FIGURES)

    rows = read(series_path())
    # Key first. One post can carry two identifiers — a ugcPost URN and the
    # activity URN that wraps it, seven seconds apart on the 2026-10-02 video —
    # and then url and timestamp both miss. The key is ours and survives that.
    keys = {r.get("key") for r in rows if r.get("key") and answered(r)}
    have = {r.get("url") for r in rows if r.get("url") and answered(r)}
    posted_at = {r.get("posted_at") for r in rows if answered(r)}
    out = []
    for d in read(drafts_path()):
        if d.get("key") and d["key"] in keys:
            continue
        if d.get("url") and d["url"] in have:
            continue
        if d.get("at") in posted_at:
            continue
        out.append(d)
    return out


def prompt() -> str:
    """The Sunday ask. One message, the columns named as LinkedIn names them."""
    pend = outstanding()
    series = read(series_path())
    L = ["\U0001f4ca <b>LinkedIn — Zahlen nachtragen</b>", ""]
    if not pend:
        L += ["Keine offenen Posts.",
              f"Reihe: {len(series)} Zeilen."
              if series else "Reihe: noch leer."]
        return "\n".join(L)
    L += [f"{len(pend)} Post(s) ohne Zahlen. Aus dem Analytics-Panel des "
          f"jeweiligen Posts, in dieser Reihenfolge:", ""]
    for d in pend:
        L.append(f"· <b>{(d.get('title') or d.get('topic') or '?')[:70]}</b>")
        L.append(f"  gepostet {(d.get('at') or '?')[:16]} · "
                 f"{d.get('url') or 'URL bitte mitschicken'}")
    fmts = {d.get("format") for d in pend}
    cols = ASK["video"] if fmts == {"video"} else ASK[None]
    L += ["", "<pre>" + "  ·  ".join(LABELS[c] for c in cols) + "</pre>",
          f"Dazu einmal: {LABELS['followers_total']}.", "",
          "Eine Zahl, die nicht im Panel steht, bitte weglassen statt "
          "schätzen — eine Lücke ist auswertbar, eine geschätzte Zahl nicht."]
    return "\n".join(L)


def record(path: str) -> int:
    """Write answers from a small JSON file. Nothing is inferred.

    A field absent from the file stays absent in the row. There is no default
    of zero, because zero impressions and an unread panel are different facts
    and the table has to keep them apart.
    """
    try:
        payload = json.load(open(path))
    except Exception as e:
        print(f"{path}: {type(e).__name__}: {e}")
        return 2
    entries = payload if isinstance(payload, list) else [payload]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    written = 0
    for e in entries:
        row = {"kind": "linkedin", "read_at": now}
        for c in COLUMNS:
            if c in e:
                row[c] = e[c]
        # A supplied figure that is not a column used to be dropped without a
        # word, which is the same defect as a swallowed 403: the record looks
        # complete and a measurement is gone. Named, and the row is still
        # written — losing the whole reading over one unknown key would be
        # worse.
        extra = [k for k in e
                 if k not in COLUMNS and k not in ("kind", "not_posted",
                                                   "pending", "key", "media",
                                                   "posted_by", "urn",
                                                   "urn_activity",
                                                   "compare_group", "note")]
        if extra:
            print(f"  nicht uebernommen, keine Spalte: {', '.join(sorted(extra))}")
        if e.get("not_posted"):
            row["not_posted"] = True
        missing = [c for c in ("posted_at", "url") if c not in row
                   and not row.get("not_posted")]
        if missing:
            print(f"übersprungen, {', '.join(missing)} fehlt: "
                  f"{json.dumps(e)[:90]}")
            continue
        append(row)
        written += 1
        print(f"notiert: {row.get('url') or row.get('posted_at')}")
    return 0 if written else 1


def latest_per_post(rows: list[dict]) -> list[dict]:
    """One line per post: the newest reading, placeholder included if it is all
    there is.

    The series stays append-only — a delta needs two readings and deleting the
    first one throws the delta away. But `record()` appends, so a post with a
    pending placeholder and a measured reading has two rows, and a table that
    shows both shows one post twice with a row of dashes next to it.
    """
    best: dict[str, dict] = {}
    for r in rows:
        # Key before url: the url of a reading can be the ugcPost permalink
        # while the placeholder carries the activity link for the same post.
        k = r.get("key") or r.get("url") or r.get("posted_at") or "?"
        prev = best.get(k)
        if prev is None or (r.get("read_at") or "") >= (prev.get("read_at") or ""):
            best[k] = {**(prev or {}), **r}
    return sorted(best.values(), key=lambda r: r.get("posted_at") or "")


def table() -> str:
    rows = latest_per_post(read(series_path()))
    if not rows:
        # An empty series is not the same as nothing owed. The first version
        # returned here and hid the pending count, which is the one number an
        # empty table still has to carry.
        pend = outstanding()
        return ("LinkedIn-Reihe ist leer. Schema steht, erste Zeile kommt mit "
                "der ersten Nacherfassung."
                + (f"\n{len(pend)} Post(s) ohne Zahlen — pending, nicht null."
                   if pend else ""))
    # Reach sits next to impressions on purpose: 59 impressions against 20
    # people reached is the kind of pair that gets misread the moment the two
    # are in different places.
    L = ["Datum       Format      Impr  Reach  React  Komm  Rep  "
         "Views  ⌀Watch  Profil  Thema"]
    for r in rows:
        if r.get("not_posted"):
            L.append(f"{(r.get('posted_at') or '?')[:10]}  — nicht gepostet")
            continue
        L.append("%-10s  %-10s  %4s  %5s  %5s  %4s  %3s  %5s  %6s  %6s  %s" % (
            (r.get("posted_at") or "?")[:10],
            (r.get("format") or "—")[:10],
            _n(r.get("impressions")), _n(r.get("members_reached")),
            _n(r.get("reactions")), _n(r.get("comments")),
            _n(r.get("reposts")), _n(r.get("video_views")),
            _n(r.get("avg_watch_time")), _n(r.get("profile_views_from_post")),
            (r.get("topic") or "")[:26]))
    pend = outstanding()
    if pend:
        L.append(f"\n{len(pend)} Post(s) ohne Zahlen — pending, nicht null.")
    return "\n".join(L)


def _n(v) -> str:
    return "—" if v is None else str(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt", action="store_true")
    ap.add_argument("--record", metavar="FILE")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    if a.record:
        return record(a.record)
    text = prompt() if a.prompt else table()
    print(text)
    if a.send and a.prompt:
        notify.send_telegram(text, channel=notify.STATS, parse_mode="HTML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
