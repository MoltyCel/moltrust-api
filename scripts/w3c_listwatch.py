#!/usr/bin/env python3
"""W3C ListWatch — inbound monitor for W3C public mailing lists.

Separate from ThreadWatch on purpose. ThreadWatch speaks only to the GitHub API
and carries a thread/ack/roster model built around issue numbers. A mailing-list
archive is HTML with different failure modes, so it gets its own script, its own
state file and its own cron slot rather than a second source class inside
threadwatch.py.

What it does, twice a day:
  1. Fetch the month index of each watched list (current month + previous month,
     so a rollover cannot hide the last messages of the old month).
  2. Diff the message numbers against state/w3c_listwatch.json.
  3. For each new message, fetch its body and evaluate the trigger rules below.
  4. Send one Telegram report if anything is new, through the shared
     app.notify gate (MOLTRUST_NOTIFY).

A month directory does not exist until its first message arrives, so HTTP 404
on a month index means "nothing posted yet" and is not an error.

CLI flags:
  --dry-run     print the report instead of sending it; state is NOT written
  --seed        record the current state without reporting (first-run baseline)
"""

import argparse
import html as html_mod
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import requests

from app import notify

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE = Path.home() / "moltstack"
STATE_FILE = BASE / "state" / "w3c_listwatch.json"
LOG_FILE = BASE / "logs" / "w3c_listwatch.log"
SECRETS_FILE = Path.home() / ".moltrust_secrets"

# ─── Archive sources ──────────────────────────────────────────────────────────
#
# Two archives, and they agree on almost nothing above the message body. W3C
# numbers messages sequentially and names the author in a span; IETF keys them
# by a message-id hash and uses an em. The month path differs too (2026Sep vs
# 2026-09). Only `<pre>` for the body is common, which is why parse_body and
# fetch stay shared while everything above them is per-source.
#
# Each source supplies: the month-path format, the index URL, an entry regex,
# and a builder that turns one regex match into (id, subject, author, msg_url).

_W3C_ENTRY_RE = re.compile(
    r'<li><a id="msg\d+" href="(\d+)\.html">(.*?)</a>\s*'
    r'<span class="messages-list-author">(.*?)</span>',
    re.S,
)

# IETF: <li><a id="<hash>" href="/arch/msg/<list>/<hash>/">[list] Subject</a>, <em>Author</em></li>
_IETF_ENTRY_RE = re.compile(
    r'<li>\s*<a id="([^"]+)" href="(/arch/msg/[^"]+)">(.*?)</a>\s*,\s*<em>(.*?)</em>',
    re.S,
)

SOURCES = {
    "w3c": {
        "month": lambda d: f"{d.year}{d.strftime('%b')}",
        "index": "https://lists.w3.org/Archives/Public/{lst}/{mon}/",
        "entry_re": _W3C_ENTRY_RE,
        # groups: (num, subject, author)
        "build": lambda g, lst, mon: (
            g[0], g[1], g[2],
            f"https://lists.w3.org/Archives/Public/{lst}/{mon}/{g[0]}.html",
        ),
    },
    "ietf": {
        "month": lambda d: f"{d.year}-{d.month:02d}",
        "index": "https://mailarchive.ietf.org/arch/browse/static/{lst}/{mon}/",
        "entry_re": _IETF_ENTRY_RE,
        # groups: (msgid_hash, path, subject, author)
        "build": lambda g, lst, mon: (
            g[0], g[2], g[3], f"https://mailarchive.ietf.org{g[1]}",
        ),
    },
}

# ─── Watched lists ────────────────────────────────────────────────────────────
#
# (list name, source key). State is keyed by list name, so the two archives must
# not reuse a name; they do not today.

LISTS = [
    ("public-agent-conformance", "w3c"),
    ("public-agentprotocol", "w3c"),
    ("agent2agent", "ietf"),
    ("agentproto", "ietf"),
    ("audit", "ietf"),
    # SAMP discussion is moving here from agent2agent; dmsc is the ART-area BoF
    # "Dynamic Multi-agent Secured Collaboration", registered 2026-06-29.
    ("dmsc", "ietf"),
]

# ─── Trigger rules ────────────────────────────────────────────────────────────
#
# Each rule is (label, predicate) over a parsed message. They only decorate the
# report — every new message is reported regardless. A trigger says "read this
# one first", never "suppress the rest".

# Participants already known, per list. A sender outside the set means the group
# is drawing people in, which is itself the signal. Only lists with an entry
# here get the NEW SENDER trigger: on a list whose roster we have not
# established, every sender would look new and the trigger would be noise.
KNOWN_SENDERS = {
    "public-agent-conformance": {
        "kenne ives",
        "nicolas rocchia",
        "nicolás rocchia",
        "julian joseph",
        "w3c community development team",
    },
}

# Matched on word boundaries. Plain substring matching fires "repo" on every
# occurrence of "report" and "reporting", which on these lists is most messages.
STRUCTURE_TERMS = [
    "work item", "first work item", "charter", "chartered", "charters",
    "repository", "repo",
]

MOLTRUST_TERMS = [
    "moltrust", "aae", "action_ref", "action ref", "emilia", "kroehl",
    "moltycel", "psea",
]

CONVERGENCE_TERMS = [
    "8 sept", "8 september", "sept 8", "september 8",
    "evidence-record group", "evidence record group",
    # CMN-nn issue references from the IETF strands. Matched on a word boundary,
    # so "cmn" also catches "CMN-11".
    "cmn",
]

# Deliberately NOT a term: "audit". Every message on the audit list is reported
# anyway — triggers decorate, they never gate — so the term would add nothing
# there, while on agent2agent, agentproto and the two W3C lists "audit trail",
# "auditor" and "auditability" are ordinary vocabulary and would fire constantly.

# ─── CLI ──────────────────────────────────────────────────────────────────────

ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true",
                help="print the report, do not send, do not persist state")
ap.add_argument("--seed", action="store_true",
                help="record current message numbers without reporting")
ARGS = ap.parse_args()

# ─── Logging ──────────────────────────────────────────────────────────────────

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
_fh = TimedRotatingFileHandler(LOG_FILE, when="D", interval=1, backupCount=14,
                               encoding="utf-8")
_fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s",
                                   datefmt="%Y-%m-%dT%H:%M:%SZ"))
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
log = logging.getLogger("w3c_listwatch")
log.setLevel(logging.INFO)
log.addHandler(_fh)
log.addHandler(_sh)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def load_secrets_into_env():
    """Mirror ~/.moltrust_secrets into os.environ.

    app.notify.send_telegram reads the token and chat id from os.environ. Cron
    lines that source the secrets file cover this already; doing it here too
    means the script also works when invoked by hand.
    """
    if not SECRETS_FILE.exists():
        return
    for line in SECRETS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            log.warning("state file unreadable, starting fresh")
    return {"lists": {}, "last_run": None}


def save_state(s):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)


def month_keys(source, now=None):
    """Previous and current month, spelled the way this source's archive does."""
    now = now or datetime.now(timezone.utc)
    py, pm = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    fmt = SOURCES[source]["month"]
    return [fmt(datetime(py, pm, 1, tzinfo=timezone.utc)), fmt(now)]


def fetch(url):
    """GET a URL. Returns (status, text). Network failure reads as status 0.

    The archive serves UTF-8 but does not always say so, and requests then
    guesses ISO-8859-1 and mangles every em-dash and accented name. Force it.
    """
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "moltrust-listwatch/1.0"})
        r.encoding = "utf-8"
        return r.status_code, r.text
    except Exception as e:
        log.warning("fetch failed %s: %s", url, type(e).__name__)
        return 0, ""


def parse_index(html, source, lst, mon):
    """[(id, subject, author, msg_url)] from a month index, sorted by id.

    IETF ids are message-id hashes, so the sort is lexical rather than
    chronological there. Order only decides how the report reads; membership is
    what the state diff turns on.
    """
    src = SOURCES[source]
    clean = lambda s: html_mod.unescape(re.sub(r"<[^>]+>", "", s)).strip()
    out = []
    for g in src["entry_re"].findall(html):
        mid, subj, auth, url = src["build"](g, lst, mon)
        out.append((mid, clean(subj), clean(auth), url))
    return sorted(out, key=lambda t: t[0])


def parse_body(html):
    """The message body, with quoted lines dropped.

    Quoted material is removed before the triggers run: a reply that quotes an
    earlier message would otherwise re-fire every trigger the original fired,
    every time someone answers it.
    """
    m = re.search(r"<pre[^>]*>(.*?)</pre>", html, re.S)
    if not m:
        return ""
    body = html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
    return "\n".join(l for l in body.splitlines() if not l.lstrip().startswith(">"))


def _matches(terms, hay):
    """Terms present in hay on word boundaries, deduplicated and sorted."""
    return sorted({t for t in terms
                   if re.search(r"\b" + re.escape(t) + r"\b", hay)})


def triggers_for(lst, author, subject, body):
    hay = f"{subject}\n{body}".lower()
    hits = []
    roster = KNOWN_SENDERS.get(lst)
    if roster is not None and author.strip().lower() not in roster:
        hits.append("NEW SENDER")
    for label, terms in (("STRUCTURE", STRUCTURE_TERMS),
                         ("MOLTRUST", MOLTRUST_TERMS),
                         ("CONVERGENCE", CONVERGENCE_TERMS)):
        found = _matches(terms, hay)
        if found:
            hits.append(f"{label}: {', '.join(found)}")
    return hits


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    load_secrets_into_env()
    state = load_state()
    sections = []
    total_new = 0

    for lst, source in LISTS:
        seen_by_month = state.setdefault("lists", {}).setdefault(lst, {})
        new_entries = []

        for mon in month_keys(source):
            url = SOURCES[source]["index"].format(lst=lst, mon=mon)
            status, text = fetch(url)

            if status == 404:
                log.info("%s %s: no month directory yet", lst, mon)
                continue
            if status != 200:
                log.warning("%s %s: HTTP %s, skipping this month", lst, mon, status)
                continue

            # An IETF month page exists before its first message and answers 200
            # with an empty index, where W3C would still 404. Both mean the same
            # thing and both are normal; parse_index simply returns nothing.
            entries = parse_index(text, source, lst, mon)
            known = set(seen_by_month.get(mon, []))
            fresh = [e for e in entries if e[0] not in known]
            log.info("%s %s: %d message(s), %d new", lst, mon, len(entries), len(fresh))

            for mid, subj, auth, msg_url in fresh:
                body = ""
                if not ARGS.seed:
                    st, mhtml = fetch(msg_url)
                    body = parse_body(mhtml) if st == 200 else ""
                    time.sleep(1)          # be polite to the archive
                new_entries.append((mon, mid, subj, auth, msg_url,
                                    triggers_for(lst, auth, subj, body)))

            seen_by_month[mon] = sorted({e[0] for e in entries} | known)

        if new_entries and not ARGS.seed:
            total_new += len(new_entries)
            lines = [f"[{lst}] {len(new_entries)} new"]
            for mon, mid, subj, auth, msg_url, hits in new_entries:
                lines.append(f"  {auth} — {subj}")
                lines.append(f"    {msg_url}")
                for h in hits:
                    lines.append(f"    !! {h}")
            sections.append("\n".join(lines))

    state["last_run"] = datetime.now(timezone.utc).isoformat()

    if ARGS.seed:
        if not ARGS.dry_run:
            save_state(state)
        log.info("seeded; no report sent")
        return 0

    if not sections:
        if not ARGS.dry_run:
            save_state(state)
        log.info("nothing new")
        return 0

    report = ("W3C ListWatch — %d new message(s)\n\n%s"
              % (total_new, "\n\n".join(sections)))

    if ARGS.dry_run:
        print("─── REPORT ───")
        print(report)
        print("─── /REPORT ─── (dry-run: state not written)")
        return 0

    if notify.send_telegram(report, chunk=True):
        log.info("report sent (%d new)", total_new)
        save_state(state)
        return 0

    # State is deliberately not advanced. A message counted as seen but never
    # delivered is a silently lost alert; leaving it unseen costs one repeat.
    log.error("Telegram send failed or suppressed — state not advanced, "
              "these %d message(s) will be reported again next run", total_new)
    return 1


if __name__ == "__main__":
    sys.exit(main())
