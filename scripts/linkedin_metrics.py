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
COLUMNS = ("posted_at", "url", "topic", "impressions", "reactions",
           "comments", "reposts", "clicks", "followers_total", "read_at")

# What LinkedIn calls them, so the prompt and the panel use the same words.
LABELS = {"impressions": "Impressions", "reactions": "Reactions",
          "comments": "Comments", "reposts": "Reposts",
          "clicks": "Clicks (Link + Page)",
          "followers_total": "Page followers (total, heute)"}


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
    have = {r.get("url") for r in read(series_path()) if r.get("url")}
    posted_at = {r.get("posted_at") for r in read(series_path())}
    out = []
    for d in read(drafts_path()):
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
    L += ["", "<pre>" + "  ".join(LABELS[c] for c in
                                  ("impressions", "reactions", "comments",
                                   "reposts", "clicks")) + "</pre>",
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


def table() -> str:
    rows = sorted(read(series_path()), key=lambda r: r.get("posted_at") or "")
    if not rows:
        # An empty series is not the same as nothing owed. The first version
        # returned here and hid the pending count, which is the one number an
        # empty table still has to carry.
        pend = outstanding()
        return ("LinkedIn-Reihe ist leer. Schema steht, erste Zeile kommt mit "
                "der ersten Nacherfassung."
                + (f"\n{len(pend)} Post(s) ohne Zahlen — pending, nicht null."
                   if pend else ""))
    L = ["Datum       Impr  React  Komm  Repost  Klicks  Follower  Thema"]
    for r in rows:
        if r.get("not_posted"):
            L.append(f"{(r.get('posted_at') or '?')[:10]}  — nicht gepostet")
            continue
        L.append("%-10s  %4s  %5s  %4s  %6s  %6s  %8s  %s" % (
            (r.get("posted_at") or "?")[:10],
            _n(r.get("impressions")), _n(r.get("reactions")),
            _n(r.get("comments")), _n(r.get("reposts")), _n(r.get("clicks")),
            _n(r.get("followers_total")), (r.get("topic") or "")[:28]))
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
