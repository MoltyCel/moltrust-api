#!/usr/bin/env python3
"""MolTrust Discovery: finds new GitHub threads on agent identity, trust and
authorization, once a day at 06:00 UTC.

Every new thread goes into ~/moltycelbot/discovery_candidates.json, which the
Content-Scout reads. Telegram gets only the threads where Lars has something to
decide or do (ops DISC-1, rule MELD-1):

- no counters (new / pruned / total) and no list of threads without an action;
  they stay in the candidates file and in ~/logs/discovery.log;
- a filter drops coordination, hub, backlog and roadmap issues whose title
  names nothing about agent identity, trust or authorization;
- one message per run, one block per thread: link, one plain sentence on what
  is going on there, exactly one recommendation, and a reply draft when the
  recommendation is "antworten";
- a run without such a thread sends nothing.

Moved into the repo from the untracked ~/moltycelbot/scripts/discovery.py
(sha256 23d375db..., last changed 2026-10-09). The rule: what runs from the
crontab lives in the repo.

    python scripts/discovery.py                 # production run
    python scripts/discovery.py --dry-run       # search, filter, print; no write, no send
    python scripts/discovery.py --replay URL..  # given issues through filter and message; no write, no send
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import gh, notify  # noqa: E402

HOME = Path(os.path.expanduser("~"))
BASE_DIR = Path(os.environ.get("DISCOVERY_BASE_DIR", HOME / "moltycelbot"))
CANDIDATES_FILE = BASE_DIR / "discovery_candidates.json"
HEALTH_FILE = BASE_DIR / "discovery_health.json"
LOG_DIR = Path(os.environ.get("DISCOVERY_LOG_DIR", HOME / "logs"))
LOG_FILE = LOG_DIR / "discovery.log"

# Alert on the third consecutive failure, then every seventh. A search that has
# been dead for three days needs Lars (credential); one bad day does not.
FAILURES_BEFORE_ALERT = 3
ALERT_REPEAT_EVERY = 7

QUERIES = [
    "agent identity verification W3C DID",
    "agent trust infrastructure verifiable credentials",
    "x402 payment agent authorization",
    "A2A agent-to-agent trust",
    "AI agent identity authorization AAE",
    "did:moltrust OR MolTrust agent",
]

SKIP_REPOS = [
    "MoltyCel", "status.moltrust", "moltrust-mcp",
    "moltrust-protocol", "moltrust-api", "moltrust-sdk",
    "moltrust-x402", "moltrust-openclaw", "moltrust-verify",
]

SPAM_INDICATORS = ["赌", "蜘蛛", "百度", "备用", "网赌", "网址"]

STALE_DAYS = 14
MAX_NEW = 20

USER_AGENT = "MolTrust-Discovery/1.1"

# ── Filter (DISC-1 criterion 3) ─────────────────────────────────────────────
#
# GitHub search matches the issue body, so almost every hit mentions a query
# word somewhere; the body says nothing about what the thread is about. The
# title and the labels do. A thread is kept when its title or a label names
# agent identity, trust or authorization.
RELEVANT = [
    (re.compile(r"\bidentit(?:y|ies)\b", re.I), "identity"),
    (re.compile(r"\bdid:", re.I), "did:"),
    (re.compile(r"\bdecentrali[sz]ed identifiers?\b", re.I), "decentralized identifier"),
    (re.compile(r"\bverifiable (?:credentials?|presentations?)\b", re.I), "verifiable credential"),
    (re.compile(r"\bcredentials?\b", re.I), "credential"),
    (re.compile(r"\battestations?\b", re.I), "attestation"),
    (re.compile(r"\btrust(?:ed|less|worthy|worthiness)?\b", re.I), "trust"),
    (re.compile(r"\bauthori[sz](?:ation|ations|e|ed|ing)\b", re.I), "authorization"),
    (re.compile(r"\bauthz\b", re.I), "authz"),
    (re.compile(r"\bdelegat(?:ion|ions|ed|e)\b", re.I), "delegation"),
    (re.compile(r"\bmandates?\b", re.I), "mandate"),
    (re.compile(r"\bx402\b", re.I), "x402"),
    (re.compile(r"\bknow[- ]your[- ]agent\b|\bkya\b", re.I), "know your agent"),
    (re.compile(r"\bagent (?:passports?|cards?)\b", re.I), "agent passport/card"),
    (re.compile(r"\breputation\b", re.I), "reputation"),
    (re.compile(r"\bprovenance\b", re.I), "provenance"),
    (re.compile(r"\bmoltrust\b", re.I), "MolTrust"),
]

# Coordination, hub, backlog and roadmap threads: standing channels between
# agent sessions, umbrella trackers, triage seats, digests. Matched on title and
# labels; CJK forms because such hubs are written in Korean and Chinese too.
HUB = [
    re.compile(p, re.I) for p in (
        r"\bcoordinat(?:e|es|ion|ing)\b", r"\bhub\b", r"\bbacklog\b",
        r"\broad ?map\b", r"\btriage\b", r"\brelay\b", r"\bchannel\b",
        r"\bdigest\b", r"\btracker\b", r"\bumbrella\b", r"\bstanding\b",
        r"\bledger\b", r"\blaunch gate\b", r"\brelease train\b",
        r"\bcentral command\b", r"\bstatus board\b", r"\bwork[- ]package\b",
        r"\[wp\b", r"\bpm[: ]seat\b", r"\bsession_[0-9a-z]+", r"^meta$",
        r"\bcheckpoint\b", r"\bmonitor-digest\b",
        "로드맵", "路线图", "分诊", "协调",
    )
]

# A thread with this many comments is a channel, whatever its title says.
HUB_COMMENTS = 300

# A negated term is no relation: "release notes without identity changes" is
# not about identity. The negator and the two words after it are removed
# before RELEVANT is searched (CLAUDE.md, "Eine Verneinung ist keine Bejahung").
NEGATION = re.compile(r"\b(?:no|not|non|without|never|zero|kein(?:e|en|er|es)?|ohne)\b(?:[\s-]+[^\s]+){0,2}", re.I)


def _labels(item: dict) -> list[str]:
    out = []
    for lab in item.get("labels") or []:
        name = lab.get("name") if isinstance(lab, dict) else lab
        if name:
            out.append(str(name))
    return out


def classify(item: dict) -> tuple[bool, str]:
    """(keep, reason). Reason in German, one line, for the run table."""
    title = item.get("title") or ""
    fields = [title] + _labels(item)
    plain = [NEGATION.sub(" ", f) for f in fields]

    terms = []
    for rx, name in RELEVANT:
        if any(rx.search(f) for f in plain) and name not in terms:
            terms.append(name)

    hub = []
    for rx in HUB:
        for f in fields:
            m = rx.search(f)
            if m and m.group(0).lower() not in hub:
                hub.append(m.group(0).lower())
    comments = int(item.get("comments") or 0)
    if comments >= HUB_COMMENTS:
        hub.append(f"{comments} Kommentare")

    if terms:
        return True, "Titel/Label nennt " + ", ".join(terms)
    if hub:
        return False, ("Koordinations-/Sammel-Issue (" + ", ".join(hub)
                       + "), ohne Bezug zu Identität, Vertrauen oder Autorisierung")
    return False, "Titel und Labels ohne Bezug zu Identität, Vertrauen oder Autorisierung"


# ── Text je Kandidat (DISC-1 criterion 4) ───────────────────────────────────

EMPFEHLUNGEN = ("antworten", "lesen", "nichts")

_SYSTEM = """You prepare one item of a daily decision message for Lars, founder of MolTrust \
(open W3C-based trust infrastructure for AI agents: DIDs, verifiable credentials, \
an authorization envelope called AAE). You get one public GitHub issue.

Return JSON only: {"satz": str, "empfehlung": "antworten"|"lesen"|"nichts", "entwurf": str}

satz: ONE German sentence, plain everyday words, max 30 words, saying what is \
going on in the thread. No praise, no marketing words, no "spannend", no \
"nicht X, sondern Y". Technical terms get a plain explanation in the same sentence.
empfehlung: "antworten" only if the thread asks an open question or proposes a \
design where a short factual comment on agent identity, trust or authorization \
would help its authors. "lesen" if it is on topic but a reply would not add \
anything yet. "nichts" if it is off topic, a coordination channel, a bot log, or \
closed.
entwurf: only for "antworten", else "". English GitHub comment, 60-120 words, \
plain business English. Open with the technical point of the thread, never with \
"we", "our", MolTrust or a thank-you. No "great point", "exactly", "leverage", \
"seamless", "robust", "delve", no em-dash chains. Name MolTrust at most once, \
late, and end with something the reader can check (a spec link or a concrete \
question)."""


def _fallback(item: dict) -> dict:
    return {"satz": f"Offenes Issue in {item['repo']} mit dem Titel „{item['title'][:100]}“.",
            "empfehlung": "lesen", "entwurf": "", "quelle": "ohne Modell"}


def describe(item: dict, client=None) -> dict:
    """{'satz', 'empfehlung', 'entwurf', 'quelle'} for one kept thread.

    Without a model client, or when its answer cannot be read, the thread is
    still shown, with the title as sentence and "lesen" as recommendation:
    the filter has already judged it on topic, and a missing model must not
    make a relevant thread disappear.
    """
    if client is None:
        return _fallback(item)
    from workers.content_scout import config as cs_config
    body = (item.get("body") or "")[:4000]
    user = (f"Repo: {item['repo']}\nIssue #{item['number']}: {item['title']}\n"
            f"Labels: {', '.join(_labels(item)) or '-'}\nComments: {item.get('comments', 0)}\n\n{body}")
    try:
        resp = client.messages.create(
            model=cs_config.MODEL_CLASSIFY, max_tokens=700, system=_SYSTEM,
            messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception as e:  # noqa: BLE001 - any model failure falls back
        print(f"  describe: model unusable for {item['url']}: {type(e).__name__}")
        return _fallback(item)
    empf = str(data.get("empfehlung", "")).strip().lower()
    satz = " ".join(str(data.get("satz", "")).split())
    entwurf = str(data.get("entwurf", "")).strip()
    if empf not in EMPFEHLUNGEN or not satz:
        return _fallback(item)
    if empf == "antworten" and not entwurf:
        empf = "lesen"
    if empf != "antworten":
        entwurf = ""
    return {"satz": satz, "empfehlung": empf, "entwurf": entwurf, "quelle": cs_config.MODEL_CLASSIFY}


_HANDGRIFF = {
    "antworten": "Empfehlung: antworten. Entwurf unten, posten im Thread machst du selbst.",
    "lesen": "Empfehlung: lesen und entscheiden, ob eine Antwort lohnt.",
}


def build_message(items: list[dict], today: str) -> str | None:
    """The one Telegram message of a run, or None when nothing needs Lars.

    `items` are kept threads with their describe() result under "text".
    Threads with recommendation "nichts" are left out. No counters, no list of
    dropped threads.
    """
    shown = [t for t in items if t["text"]["empfehlung"] in _HANDGRIFF]
    if not shown:
        return None
    n = len(shown)
    lines = [f"Discovery {today}: {n} Thread{'s' if n != 1 else ''} für dich",
             "Rückfragen im Console-Tab.", ""]
    for i, t in enumerate(shown, 1):
        x = t["text"]
        lines.append(f"{i}. {t['url']}")
        lines.append(x["satz"])
        lines.append(_HANDGRIFF[x["empfehlung"]])
        if x["empfehlung"] == "antworten":
            lines.append("Entwurf:")
            lines.append(x["entwurf"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ── GitHub ──────────────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {"Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT}


def _get(url: str, token: str, timeout: int = 15):
    req = urllib.request.Request(url, headers=_headers(token))
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - literal https://api.github.com host
        return resp.status, json.loads(resp.read())


def token_is_live(token: str) -> tuple[bool, str]:
    """Check the credential before spending the run on it: a dead token turns
    every search into a caught exception and the run into a quiet day."""
    try:
        status, _ = _get("https://api.github.com/user", token)
        return status == 200, f"HTTP {status}"
    except urllib.error.HTTPError as e:
        return False, "credential rejected" if e.code == 401 else f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _thread(item: dict, query: str) -> dict:
    return {
        "repo": item["repository_url"].replace("https://api.github.com/repos/", ""),
        "number": item["number"],
        "title": (item.get("title") or "")[:120],
        "url": item["html_url"],
        "updated_at": item.get("updated_at"),
        "query": query,
        "labels": _labels(item),
        "comments": int(item.get("comments") or 0),
        "body": (item.get("body") or "")[:4000],
    }


def search_github(query: str, token: str) -> list:
    params = urllib.parse.urlencode({"q": f"{query} is:open is:issue",
                                     "sort": "updated", "order": "desc", "per_page": 5})
    try:
        _, data = _get(f"https://api.github.com/search/issues?{params}", token)
        return data.get("items", [])
    except Exception as e:  # noqa: BLE001
        print(f"  Search error for '{query}': {e}")
        return []


def moltycel_already_commented(repo: str, number: int, token: str) -> bool:
    try:
        _, comments = _get(f"https://api.github.com/repos/{repo}/issues/{number}/comments?per_page=100",
                           token, timeout=10)
        return any((c.get("user") or {}).get("login", "").lower() == "moltycel" for c in comments)
    except Exception:  # noqa: BLE001
        return False


def discover_threads(token: str) -> list:
    found, seen = [], set()
    for query in QUERIES:
        print(f"  Searching: {query}")
        for item in search_github(query, token):
            t = _thread(item, query)
            if any(skip in t["repo"] for skip in SKIP_REPOS):
                continue
            if any(s in t["title"] for s in SPAM_INDICATORS):
                continue
            if t["url"] in seen:
                continue
            seen.add(t["url"])
            found.append(t)
        time.sleep(2)

    out = []
    for t in found:
        if moltycel_already_commented(t["repo"], t["number"], token):
            print(f"  SKIP (MoltyCel present): {t['repo']}#{t['number']}")
            continue
        out.append(t)
        time.sleep(1)
    return out[:MAX_NEW]


def fetch_issue(url: str, token: str) -> dict:
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)", url)
    if not m:
        raise ValueError(f"not an issue URL: {url}")
    _, item = _get(f"https://api.github.com/repos/{m.group(1)}/issues/{m.group(2)}", token)
    return _thread(item, "replay")


# ── State files ─────────────────────────────────────────────────────────────

def load_candidates() -> dict:
    try:
        return json.loads(CANDIDATES_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {"candidates": []}


def save_candidates(data: dict) -> None:
    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_FILE.write_text(json.dumps(data, indent=2))


def load_health() -> dict:
    try:
        return json.loads(HEALTH_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {"consecutive_failures": 0, "last_ok": None}


def save_health(data: dict) -> None:
    try:
        HEALTH_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:  # noqa: BLE001
        print(f"  Could not write {HEALTH_FILE.name}: {e}")


def send(msg: str) -> None:
    ok = notify.send_telegram(msg, channel=notify.WORKLOG, chunk=True)
    if not ok:
        print(f"  Telegram: nicht gesendet ({notify.letzter_grund() or 'unbekannt'})")


def record_failure(reason: str) -> int:
    """Count a failed run; alert once it stops looking like a blip, because
    then Lars has to fix the credential."""
    health = load_health()
    n = int(health.get("consecutive_failures", 0)) + 1
    health.update(consecutive_failures=n, last_error=reason)
    save_health(health)
    print(f"MolTrust Discovery FAILED ({n} in a row): {reason}")
    if n == FAILURES_BEFORE_ALERT or (
            n > FAILURES_BEFORE_ALERT and (n - FAILURES_BEFORE_ALERT) % ALERT_REPEAT_EVERY == 0):
        send(f"Discovery läuft seit {n} Tagen nicht: {reason}.\n"
             f"Letzter guter Lauf: {health.get('last_ok') or 'unbekannt'}. "
             f"Handgriff: GitHub-Token MOLTYCEL_GH_TOKEN prüfen. Log: {LOG_FILE}")
    return n


def record_success(today: str) -> None:
    previous = int(load_health().get("consecutive_failures", 0))
    save_health({"consecutive_failures": 0, "last_ok": today, "last_error": None})
    if previous >= FAILURES_BEFORE_ALERT:
        # Recovery is a status, not an action (MELD-1): log only.
        print(f"MolTrust Discovery running again after {previous} failed runs.")


def _model_client():
    try:
        from workers.content_scout import config as cs_config
        import anthropic
        key = cs_config.anthropic_key(cs_config.load_secrets())
        return anthropic.Anthropic(api_key=key) if key else None
    except Exception as e:  # noqa: BLE001
        print(f"  describe: no model client ({type(e).__name__}); titles only")
        return None


def evaluate(threads: list, client) -> tuple[list, list]:
    """(kept_with_text, rows) — rows are (thread, keep, reason) for the log table."""
    rows, kept = [], []
    for t in threads:
        keep, reason = classify(t)
        rows.append((t, keep, reason))
        if keep:
            kept.append({**t, "text": describe(t, client)})
    return kept, rows


def print_table(rows: list, kept: list) -> None:
    text = {k["url"]: k["text"] for k in kept}
    for t, keep, reason in rows:
        tag = "KEEP" if keep else "DROP"
        extra = f" -> {text[t['url']]['empfehlung']}" if keep else ""
        print(f"  {tag} {t['repo']}#{t['number']}: {reason}{extra}")


# ── Main ────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true", help="no file write, no Telegram")
    ap.add_argument("--replay", nargs="+", metavar="URL", help="run given issues through filter and message (implies --dry-run)")
    ap.add_argument("--no-model", action="store_true", help="skip the model; sentence from the title")
    args = ap.parse_args(argv)
    dry = args.dry_run or bool(args.replay)

    today = datetime.date.today().isoformat()
    print(f"MolTrust Discovery — {today}{' (dry run)' if dry else ''}")

    token = gh.token()
    if not token:
        sys.stderr.write("FATAL: MOLTYCEL_GH_TOKEN not set\n")
        return 1
    live, detail = token_is_live(token)
    if not live:
        if not dry:
            record_failure(f"GitHub credential unusable ({detail})")
        else:
            print(f"GitHub credential unusable ({detail})")
        return 1

    client = None if args.no_model else _model_client()

    if args.replay:
        threads = [fetch_issue(u, token) for u in args.replay]
        kept, rows = evaluate(threads, client)
        print_table(rows, kept)
        msg = build_message(kept, today)
        print("--- Telegram (not sent, replay) ---")
        print(msg if msg else "(keine Meldung)")
        return 0

    state = load_candidates()
    existing = {c["url"] for c in state["candidates"]}
    new = [t for t in discover_threads(token) if t["url"] not in existing]
    kept, rows = evaluate(new, client)
    print_table(rows, kept)

    for t, keep, reason in rows:
        state["candidates"].append({
            "repo": t["repo"], "number": t["number"], "title": t["title"],
            "url": t["url"], "added_at": today, "query": t.get("query", ""),
            "filter": "keep" if keep else "drop", "filter_reason": reason,
        })
        print(f"  NEW: {t['repo']}#{t['number']} — {t['title'][:60]}")

    cutoff = (datetime.date.today() - datetime.timedelta(days=STALE_DAYS)).isoformat()
    before = len(state["candidates"])
    state["candidates"] = [c for c in state["candidates"] if c.get("added_at", today) > cutoff]
    pruned = before - len(state["candidates"])
    summary = (f"MolTrust Discovery: {len(new)} new, {len(kept)} kept, "
               f"{pruned} pruned, {len(state['candidates'])} total")
    print(summary)

    msg = build_message(kept, today)
    if dry:
        print("--- Telegram (not sent, dry run) ---")
        print(msg if msg else "(keine Meldung)")
        return 0

    save_candidates(state)
    record_success(today)
    if msg:
        send(msg)
    else:
        print("Nothing for Lars — no Telegram sent")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.datetime.now(datetime.timezone.utc).isoformat()} — {summary}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
