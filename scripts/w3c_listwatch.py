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

ARCHIVE = "https://lists.w3.org/Archives/Public"

# ─── Watched lists ────────────────────────────────────────────────────────────

LISTS = [
    "public-agent-conformance",
    "public-agentprotocol",
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
]

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


def month_keys(now=None):
    """Current month and the previous one, as the archive spells them."""
    now = now or datetime.now(timezone.utc)
    cur = f"{now.year}{now.strftime('%b')}"
    py, pm = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    prev = f"{py}{datetime(py, pm, 1).strftime('%b')}"
    return [prev, cur]


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


_ENTRY_RE = re.compile(
    r'<li><a id="msg\d+" href="(\d+)\.html">(.*?)</a>\s*'
    r'<span class="messages-list-author">(.*?)</span>',
    re.S,
)


def parse_index(html):
    """[(num, subject, author)] from a month index, oldest number first."""
    out = []
    for num, subj, auth in _ENTRY_RE.findall(html):
        clean = lambda s: html_mod.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        out.append((num, clean(subj), clean(auth)))
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
    months = month_keys()
    sections = []
    total_new = 0

    for lst in LISTS:
        seen_by_month = state.setdefault("lists", {}).setdefault(lst, {})
        new_entries = []

        for mon in months:
            url = f"{ARCHIVE}/{lst}/{mon}/"
            status, text = fetch(url)

            if status == 404:
                log.info("%s %s: no month directory yet", lst, mon)
                continue
            if status != 200:
                log.warning("%s %s: HTTP %s, skipping this month", lst, mon, status)
                continue

            entries = parse_index(text)
            known = set(seen_by_month.get(mon, []))
            fresh = [e for e in entries if e[0] not in known]
            log.info("%s %s: %d message(s), %d new", lst, mon, len(entries), len(fresh))

            for num, subj, auth in fresh:
                body = ""
                if not ARGS.seed:
                    st, mhtml = fetch(f"{ARCHIVE}/{lst}/{mon}/{num}.html")
                    body = parse_body(mhtml) if st == 200 else ""
                    time.sleep(1)          # be polite to the archive
                new_entries.append((mon, num, subj, auth,
                                    triggers_for(lst, auth, subj, body)))

            seen_by_month[mon] = sorted({e[0] for e in entries} | known)

        if new_entries and not ARGS.seed:
            total_new += len(new_entries)
            lines = [f"[{lst}] {len(new_entries)} new"]
            for mon, num, subj, auth, hits in new_entries:
                lines.append(f"  {auth} — {subj}")
                lines.append(f"    {ARCHIVE}/{lst}/{mon}/{num}.html")
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
