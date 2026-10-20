"""MolTrust Agent Watchdog - Monitors all cron agents and alerts on failure."""

import os, sys, json, datetime, glob, hashlib, httpx, logging, re

# Der Pfad zum Repo, aus der Datei selbst. Python legt beim Skriptaufruf
# das Verzeichnis des Skripts auf sys.path, nicht das
# Arbeitsverzeichnis — ohne diese Zeile braucht der Aufruf ein
# PYTHONPATH aus der Crontab, und eine Crontab, die den Suchpfad setzt,
# ist dieselbe unsichtbare Ueberstimmung wie POLL_RPC_URL am 09.10.2026.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify
from app.base_rpc import base_rpc_url

from agents import x_budget, x_meter

DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- Discovery-surface reconciliation ---------------------------------------
# Two surfaces agents discover us through: the MCP tool catalog (Smithery
# listing) and the A2A Agent-Card. Smithery is winding down self-service
# updates: since 2026-10-19 its API refuses new servers ("Updating servers has
# been removed"), and an existing listing can be re-published until 2026-10-31,
# after which it stays as it is (ops SMITHERY-EXIT, Lars 20.10.2026). The count
# on Smithery therefore stops tracking the origin, and a difference says nothing
# we could act on. The one finding left is the entry disappearing.
MCP_LOCAL_URL = "http://127.0.0.1:8002/mcp"
SMITHERY_REGISTRY_URL = "https://registry.smithery.ai/servers/@moltrust/moltrust-mcp-server"
SMITHERY_FROZEN_FROM = datetime.date(2026, 10, 31)
# Lars asked Smithery support on 2026-10-20 for a rescan (56 -> 61 tools) and
# for the listing to point at our endpoint instead of their run.tools proxy. If
# either lands, that is news worth one message, not an hourly one: the file
# remembers what has been announced.
SMITHERY_DIRECT_HOST = "api.moltrust.ch"
# The count Smithery has shown since before the request. Any higher count is the
# rescan, whatever the origin serves by then: 61 when the mail went out, 64 the
# same day after the canary tools (#860).
SMITHERY_LISTED_BEFORE_RESCAN = 56
SMITHERY_NOTICE_FILE = os.path.expanduser("~/moltstack/data/smithery_rescan_notice.json")
GLAMA_LISTING_URL = "https://glama.ai/mcp/servers/MoltyCel/moltrust-mcp-server"
# Glama re-crawls on its own schedule and has no API we can read without a key,
# so the listing is scraped and checked weekly rather than hourly: a fresh
# origin change takes days to show up there, and an hourly alarm about it would
# be noise for six days out of seven.
GLAMA_CHECK_WEEKDAY = 0  # Monday

# The package is published to two indices from one tag, and on 2026-09-22 they
# came apart: PyPI took 1.2.3 and the MCP registry refused the record, because
# the registry had tightened its description cap to 100 characters since 0.7.0
# was published. The release looked finished — the tag was pushed, the wheel was
# up — and the registry sat five versions back. Nothing would have said so.
MCP_REGISTRY_SEARCH_URL = (
    "https://registry.modelcontextprotocol.io/v0/servers?search=moltrust-mcp-server"
)
MCP_REGISTRY_SERVER_NAME = "io.github.MoltyCel/moltrust-mcp-server"
PYPI_PACKAGE_URL = "https://pypi.org/pypi/moltrust-mcp-server/json"
REGISTRY_CHECK_WEEKDAY = 0  # Monday, with the other listing checks

# Four tools render an answer the API withheld, and through 1.2.3 three of
# them called it "not found" instead — a checked negative, for an answer that
# was never checked. The package has vectors for this; they run against
# recorded responses. This asks the hosted origin, which is where a stale
# venv or a half-finished deploy would put the old text back in front of a
# reader without a single test going red.
API_BASE = "https://api.moltrust.ch"
WITHHELD_CHECK_WEEKDAY = 0  # Monday
# (tool, arguments, the API route behind it, where withheld sits in the body,
# phrases that mean the route did not answer this time).
# The inputs are chosen because they are withheld today, not because they must
# stay that way — the check reads the API first and compares only what it
# actually withholds, so an agent gaining endorsers retires a probe instead of
# raising an alarm.
#
# The last field exists because the API read and the tool call are two separate
# requests, and /resolve/erc8004/ sits behind a rate-limited public RPC: the
# first can succeed while the second comes back empty-handed. That is not the
# tool mis-rendering anything, and it must not be reported as though it were.
WITHHELD_PROBES = (
    ("moltrust_verify", {"did": "did:web:example.com"},
     "/identity/verify/did:web:example.com", (), ()),
    ("mt_get_trust_score", {"did": "did:web:example.com"},
     "/skill/trust-score/did:web:example.com", (), ()),
    ("mt_get_badge", {"did": "did:moltrust:157224190be24072"},
     "/identity/badge/did:moltrust:157224190be24072", (), ()),
    ("moltrust_erc8004", {"action": "resolve", "agent_id": 21351},
     "/resolve/erc8004/21351", ("moltrust_trust_score",),
     ("not found on ERC-8004 IdentityRegistry", "Base RPC did not answer",
      "Error 503")),
)

# The weekday alone was not enough. The watchdog runs hourly, so "on Mondays"
# meant twenty-four identical alerts every Monday — on 2026-09-21 the Glama
# drift was reported once an hour from midnight. A weekly check has to name an
# hour as well; _is_weekly_slot below is what every weekly check asks.
WEEKLY_CHECK_HOUR = 6  # UTC, before the working day

X402_DISCOVERY_URL = "https://api.moltrust.ch/.well-known/x402.json"
X402_PRICED_SAMPLE = (
    "https://api.moltrust.ch/guard/api/agent/score/"
    "0x0000000000000000000000000000000000000001"
)
AGENT_CARD_URL = "https://api.moltrust.ch/.well-known/agent-card.json"
# The Agent-Card has no independent live source-of-truth for "expected skills",
# so this is a pinned counter — BUMP IT when you add/remove a skill (see the
# Discovery-Checklist in CLAUDE.md). Mismatch => card regressed OR baseline stale.
EXPECTED_AGENT_CARD_SKILLS = 15

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
# httpx logs every request URL at INFO, which writes the Telegram bot token
# into the log file in clear text. Keep it at WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("watchdog")


def _is_weekly_slot(now: datetime.datetime, weekday: int) -> bool:
    """True in the single hourly run that carries a weekly check."""
    return now.weekday() == weekday and now.hour == WEEKLY_CHECK_HOUR


# Agent definitions: name, max_hours without activity, check method.
# "cron" is the text that identifies the agent's crontab line. A max_hours below
# the longest gap in that schedule alarms every day in normal operation;
# threshold_findings() compares the two, and c-watchdog-threshold-fits-cron
# runs it daily.
# Moltbook Poster: DISABLED 2026-03-30 — Moltbook API down post Meta acquisition (500 errors since 2026-03-27)
AGENTS = [
    {
        "name": "Herald",
        "heartbeat_file": os.path.join(DATA_DIR, "herald_heartbeat.json"),
        # Once a day at 12:00 since 2026-09-21, so 24 h x 1.5. The 12 h from the
        # four-a-day schedule stayed behind and alarmed 13 to 23 h after every
        # digest, twelve times a day, until 2026-10-16 (ops WD-1).
        "max_hours": 36,
        "fallback_glob": "herald_*.md",
        "cron": "agents/herald_v3.py digest",
    },
    {
        "name": "Scout",
        "heartbeat_file": None,
        "max_hours": 15,  # runs 08:00 and 18:00, longest gap 14h
        "fallback_glob": "scout_*.md",
        "cron": "agents/scout.py",
    },
    {
        "name": "Ambassador",
        "heartbeat_file": None,
        "max_hours": 1.5,  # runs every 30min, give 1.5h grace
        "fallback_log": "ambassador.log",
        "cron": "agents/ambassador.py run",
    },
    {
        "name": "News Scout",
        "heartbeat_file": os.path.join(DATA_DIR, "news_scout_heartbeat.json"),
        "max_hours": 26,  # runs 1x/day, give 2h grace
        "fallback_glob": None,
        "cron": "agents/news_scout.py",
    },
    # REMOVED 2026-05-13 — TrustScout-Monitoring via state-file-field
    # ist strukturell unzuverlaessig (multi-writer trustscout.py + moltguard.py
    # schreiben dasselbe File, last_post_time kann stale werden ohne dass Posts
    # ausfallen). Diagnose 13.05.26: Posts auf Moltbook funktionieren weiter
    # (verifiziert via MolTrust Telegram Stats), State-File-Update ist nicht
    # zuverlaessiges Health-Signal. Falls zukuenftig TrustScout-Health-Check
    # gewuenscht: separater Check direkt gegen Moltbook-Post-Resultat (nicht
    # state-file). Bis dahin: kein Watchdog-Eintrag.
]


# --- the external deadman ----------------------------------------------------
# A watchdog cannot report its own absence, and twice on 2026-10-03 it had to:
# the supervisor stopped and only the other side noticed. So the heartbeat now
# leaves the building. healthchecks.io expects a ping every hour with thirty
# minutes of grace and raises the alarm itself, over its own Telegram
# integration — deliberately not over ours, because a server that is down
# cannot send the message saying it is down.
#
# **Only a run that finished pings.** A watchdog that reports itself healthy
# while failing is the exact defect this is here to catch, and we had it twice.
# A crash sends /fail instead, which turns a ninety-minute wait into an
# immediate alarm.
#
# The URL is a secret of a particular kind: it grants no access, it only
# suppresses an alarm. Whoever holds it can keep Lars from being told that the
# server is dead. So it is treated like any other credential — never logged,
# never in a report, never in a job summary — and the code below reports status
# codes and exception types, never the URL.
HEALTHCHECK_ENV = "HEALTHCHECK_URL"


HEARTBEAT = os.path.join(DATA_DIR, "watchdog_heartbeat.json")


def stamp_heartbeat(now) -> None:
    """A local trace that this run happened, written where the ping is sent.

    The watchdog ran hourly and left nothing on disk: its evidence was a log
    line and an outbound ping. So a local expectation of "something checked in
    within two hours" had nothing hourly to read, and the only heartbeat on the
    machine came from GitHub Actions, which delivers about every 3.4 hours.
    Setting a two-hour limit against that measures somebody else's queue.

    Written after the ping, so the file says a run finished rather than that
    one started.
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(HEARTBEAT, "w") as fh:
            json.dump({"at": now.isoformat(timespec="seconds"),
                       "source": "agents/watchdog.py"}, fh)
    except OSError as e:
        # A missing stamp is a finding for the checker, not a reason to fail
        # the run that produced it.
        log.warning(f"  heartbeat not written: {type(e).__name__}")


def ping_healthcheck(ok: bool) -> str:
    """Tell the external receiver this run finished. Returns a loggable word."""
    url = os.getenv(HEALTHCHECK_ENV, "").strip()
    if not url:
        return "not configured"
    if not url.startswith("https://"):
        # A plaintext ping would put the URL on the wire for anyone to reuse,
        # and reusing it means silencing the alarm.
        return "refused: not https"
    target = url if ok else url.rstrip("/") + "/fail"
    try:
        r = httpx.get(target, timeout=10.0)
    except Exception as e:
        # The type, not the message: an httpx error message contains the URL.
        return f"failed: {type(e).__name__}"
    return f"{'ok' if ok else 'fail'} -> HTTP {r.status_code}"


def send_telegram(message: str, *, channel: str = notify.ALERTS) -> bool:
    if not notify.telegram_allowed("watchdog.send_telegram", logger=log):
        return False
    if not TELEGRAM_BOT_TOKEN or not notify.chat_id_for(channel):
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": notify.chat_id_for(channel), "text": message, "parse_mode": "HTML"},
            timeout=10.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


# --- The radar's silence ----------------------------------------------------
# Three times now a source died and every run afterwards reported "0
# candidates": the list answered 400 for 24 hours in September, the account ran
# out of credits in the same week, and on 2026-10-02 the breaker closed at
# 11:00 UTC and cancelled the 14:05 and 18:05 runs. Each time the component
# that knew was the one that had stopped talking, so nobody was told.
#
# This check is deliberately not in reply_radar.py. A run cannot report that it
# never happened, and a radar that is wedged cannot alert about being wedged.
# The watchdog reads the same files from outside and says what it sees.
RADAR_LOG = os.path.join(LOG_DIR, "reply_radar.log")
RADAR_STATE = os.path.join(DATA_DIR, "reply_radar_state.json")
# Same shape as an AGENTS entry, so threshold_findings() compares this one too.
# Until ops WD-2 the threshold was a bare 12 h against a schedule whose longest
# pause is 14 h — 16:05 to 06:05 — so the check went red every night and twice
# every morning. From 2026-10-18 the three guarded radar lines expire and only
# 06:05 remains, which makes the pause 24 h; 36 h is that pause with the same
# half-again grace Herald gets. Since ops CH8 the check derives its threshold
# from the crontab on every run (derived_max_hours()); the 36 h here is the
# fallback for a crontab that cannot be read. "cron" matches several lines here, so the entry
# says so: a schedule can be spread over more than one crontab line, and the
# five-minute --consume pass is not one of them (it sends button presses, never
# a reading list).
RADAR_DEADMAN = {
    "name": "ReplyRadar",
    "max_hours": 36,
    "cron": "agents/reply_radar.py",
    "cron_lines": "many",
    "cron_not": "--consume",
}
# How often the finding is repeated is no longer decided here: since ops WD-1
# every check of this watchdog goes through the same state file (see
# debounce()), once on the change and then once a day.
RADAR_TAIL_LINES = 4000
READ_LIST_SENT = re.compile(r"reading list: [1-9]\d* posts of \d+ candidates sent$")


def _radar_tail(path: str = RADAR_LOG, n: int = RADAR_TAIL_LINES) -> list:
    with open(path, errors="replace") as f:
        return f.read().splitlines()[-n:]


def _parse_ts(raw: str) -> "datetime.datetime | None":
    try:
        d = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def radar_silence(now: datetime.datetime, lines: list,
                  max_hours: "float | None" = None) -> dict:
    """Hours since the last draft reached Telegram, and the reason it did not.

    The reason is read off the last run that actually ran, in the order the run
    itself would know it:

      Job lief nicht   — no run header within the cadence
      Breaker zu       — the run started and read nothing
      Quelle fehlerhaft— a 4xx/402 from one of the three sources
      0 Kandidaten     — read fine, nothing came through the filter
      alle verworfen   — candidates existed and the drafter declined them all

    A blocked draft counts as delivered: it is sent to Telegram and read there.
    A skip is not — nothing was sent.
    """
    last_draft = last_run = None
    paused = None
    candidates = skips = None
    errors = []
    for line in lines:
        m = re.match(r"^\[(\S+)\] \w+: (.*)$", line)
        if not m:
            continue
        at, msg = m.group(1), m.group(2)
        if msg.startswith("REPLY RADAR — "):
            last_run, paused, candidates, skips, errors = at, None, None, 0, []
        # From 18.10.2026 the radar drafts nothing and sends a reading list
        # instead (Lars, BW1). A list that reached Telegram is the delivery
        # this check asks about; a dry run's list is not.
        elif re.match(r"\s*draft \d+ for ", msg) or READ_LIST_SENT.match(msg):
            last_draft = at
        elif msg.startswith("Reads paused:"):
            paused = msg.split("Reads paused:", 1)[1].strip()
        elif "Candidates after filtering:" in msg:
            g = re.search(r"filtering: (\d+)", msg)
            candidates = int(g.group(1)) if g else None
        elif msg.lstrip().startswith("skip ") and skips is not None:
            skips += 1
        else:
            g = re.search(r"GET (\S+) -> (\d{3})", msg)
            if g and not g.group(2).startswith("2"):
                errors.append((g.group(1).rsplit("/", 1)[-1], g.group(2)))

    d = _parse_ts(last_draft) if last_draft else None
    r = _parse_ts(last_run) if last_run else None
    hours = (now - d).total_seconds() / 3600 if d else None

    limit = RADAR_DEADMAN["max_hours"] if max_hours is None else max_hours
    if r is None or (now - r).total_seconds() / 3600 > limit:
        reason = ("Job lief nicht — keine Lauf-Kopfzeile seit "
                  + (last_run or f"den letzten {RADAR_TAIL_LINES} Logzeilen"))
    elif paused:
        reason = f"Breaker zu — {paused}"
    elif errors:
        reason = "Quelle fehlerhaft — " + ", ".join(f"{n} HTTP {s}" for n, s in errors)
    elif not candidates:
        reason = "0 Kandidaten — gelesen, nichts kam durch den Filter"
    else:
        reason = (f"alle verworfen — {candidates} Kandidaten, {skips} abgelehnt "
                  f"(Drafter, nicht Filter)")
    return {"hours": hours, "last_draft": last_draft, "last_run": last_run,
            "reason": reason}


def check_radar_deadman(now: datetime.datetime, crontab: "str | None" = None) -> dict:
    """No delivery within the radar's own threshold is a finding.

    The threshold is derived from the radar's crontab lines on every run
    (derived_max_hours(), ops CH8); RADAR_DEADMAN["max_hours"] is only the
    fallback for a crontab that cannot be read, and the message says which of
    the two applied.
    """
    try:
        lines = _radar_tail()
    except OSError as e:
        return {"surface": "ReplyRadar", "ok": False,
                "detail": f"Log nicht lesbar: {type(e).__name__}: {e}"}
    if crontab is None:
        crontab = _crontab_text()
    limit, basis = derived_max_hours(RADAR_DEADMAN, crontab)
    s = radar_silence(now, lines, limit)
    if s["hours"] is not None and s["hours"] < limit:
        return {"surface": "ReplyRadar", "ok": True,
                "detail": f"letzter Entwurf vor {s['hours']:.1f} h · {basis}"}
    since = (f"{s['hours']:.1f} h" if s["hours"] is not None
             else f"keiner in den letzten {RADAR_TAIL_LINES} Logzeilen")
    return {"surface": "ReplyRadar", "ok": False,
            "detail": (f"kein Entwurf seit {since} · Grund: {s['reason']} · "
                       f"letzter Lauf {s['last_run'] or '—'} · {basis}")}


# --- Moltbook: did anything actually appear ----------------------------------
# The Ambassador's log said "Run done" every thirty minutes from 2026-10-10 to
# 2026-10-16 and its heartbeat check above stayed green, while not one reply
# went out for 140 hours: the author listing had stopped returning our posts
# without a sort parameter (ops W9). In the same week the poster logged
# "POSTED" every morning, and from 2026-10-11 Moltbook answered each of those
# with the id of an older post carrying the same fallback text. A log line says
# that a run happened; only the platform says that something appeared.
#
# So this asks Moltbook, per account, when it last showed a new post or comment
# of ours. Each entry names the agent that is scheduled to write it; "cron" is
# the marker threshold_findings() checks max_hours against, as for AGENTS.
# Not on the list, with the reason (scripts/moltbook_writers.py has the
# inventory): moltbook/heartbeat.py only upvotes, which leaves nothing datable;
# agents/auditor.py and agents/trustscout.py are not scheduled.
MOLTBOOK_API = "https://www.moltbook.com/api/v1"
MOLTBOOK_READ_KEY_ENV = "MOLTBOOK_AGENT_KEY"
MOLTBOOK_WRITERS = [
    {
        "surface": "Moltbook/Ambassador",
        "account": "moltrust-agent",
        "writes": "comment",
        # Replies depend on someone commenting, so a quiet day can be honest.
        # A day is the limit set in ops W9; six days went unnoticed before.
        "max_hours": 24,
        "cron": "agents/ambassador.py run",
    },
    {
        "surface": "Moltbook/Poster",
        "account": "moltrust-agent",
        "writes": "post",
        "max_hours": 36,  # once a day at 09:00, so 24 h x 1.5
        "cron": "agents/moltbook_poster.py",
    },
    {
        "surface": "Moltbook/MoltGuard",
        "account": "moltguard_v1",
        "writes": "post",
        "max_hours": 36,  # post-deep once a day at 13:00, so 24 h x 1.5
        "cron": "agents/moltguard.py post-deep",
    },
]


def _moltbook_stamp(raw) -> "datetime.datetime | None":
    if not isinstance(raw, str) or not raw:
        return None
    try:
        when = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def _author(row: dict) -> "str | None":
    a = row.get("author")
    return a.get("name") if isinstance(a, dict) else a


def last_moltbook_write(writer: dict, get) -> "datetime.datetime | None":
    """Newest post or comment of the account, as Moltbook dates it.

    `get(path, params)` returns the parsed JSON or raises. A listing that comes
    back with somebody else's rows has ignored its filter, which is what
    `agent=` does on /posts; that is an unreadable source, not a date.
    """
    if writer["writes"] == "comment":
        # /agents/me/comments only answers for the key's own account.
        data = get("/agents/me/comments", {"limit": 100})
        rows = data.get("comments") if isinstance(data, dict) else data
    else:
        data = get("/posts", {"author": writer["account"], "sort": "new", "limit": 5})
        rows = data.get("posts", data.get("data")) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("listing without a posts array")
        strangers = {_author(r) for r in rows} - {writer["account"]}
        if strangers:
            raise ValueError(f"filter ignored, rows by {sorted(strangers)[:3]}")
    if not isinstance(rows, list):
        raise ValueError("listing without a rows array")
    stamps = [s for s in (_moltbook_stamp(r.get("created_at")) for r in rows) if s]
    if rows and not stamps:
        raise ValueError(f"{len(rows)} rows, none with a readable created_at")
    return max(stamps) if stamps else None


def _moltbook_get(path: str, params: dict):
    key = os.environ.get(MOLTBOOK_READ_KEY_ENV, "")
    if not key:
        raise RuntimeError(f"{MOLTBOOK_READ_KEY_ENV} not set")
    r = httpx.get(f"{MOLTBOOK_API}{path}", params=params, timeout=15,
                  headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    return r.json()


def check_moltbook_writers(now: datetime.datetime, get=None) -> list:
    """One verdict per scheduled Moltbook writer: has it written within max_hours."""
    get = get or _moltbook_get
    out = []
    for w in MOLTBOOK_WRITERS:
        what = "Kommentar" if w["writes"] == "comment" else "neuer Post"
        try:
            last = last_moltbook_write(w, get)
        except Exception as e:
            out.append({"surface": w["surface"], "ok": False, "unverifiable": True,
                        "detail": f"Moltbook nicht lesbar: {type(e).__name__}"})
            continue
        if last is None:
            out.append({"surface": w["surface"], "ok": False,
                        "detail": f"kein {what} von {w['account']} auffindbar"})
            continue
        hours = (now - last).total_seconds() / 3600
        stamp = last.strftime("%Y-%m-%d %H:%MZ")
        if hours < w["max_hours"]:
            out.append({"surface": w["surface"], "ok": True,
                        "detail": f"letzter {what} vor {hours:.1f} h ({stamp})"})
        else:
            out.append({"surface": w["surface"], "ok": False,
                        "detail": (f"kein {what} von {w['account']} seit {hours:.0f} h "
                                   f"(max {w['max_hours']} h), letzter {stamp}")})
    return out


# --- One message per outage -------------------------------------------------
# From 2026-09-21 to 2026-10-16 Herald was reported twelve times a day, every
# hour from 13 to 23 hours after its digest, about 300 messages for one wrong
# threshold. Each one differed from the last by its hour count, so the
# fingerprint throttle in app/notify.py saw a new message every time. The
# watchdog itself knew nothing about its previous run.
#
# It does now. Every check carries a key, and the state per key is kept in
# ALERT_STATE between runs. A message goes out when a key turns red, once a
# day while it stays red, and once when it turns green again. A key that was
# not measured in a run (a weekly check on the other six days, a source that
# could not be read) keeps its state: not seen is not recovered.
ALERT_STATE = os.path.join(DATA_DIR, "watchdog_alert_state.json")
REMIND_HOURS = 24


def load_alert_state(path: "str | None" = None) -> dict:
    try:
        with open(path or ALERT_STATE) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        # An unreadable state file means every red key is reported once more,
        # which is the safe direction.
        log.warning(f"  alert state unreadable ({type(e).__name__}), starting empty")
        return {}


def save_alert_state(state: dict, path: "str | None" = None) -> None:
    path = path or ALERT_STATE
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        log.warning(f"  alert state not written: {type(e).__name__}")


def debounce(observations: list, state: dict,
             now: datetime.datetime) -> "tuple[list, dict]":
    """Decide which observations become a line in the message.

    observations: [{"key", "ok", "line"}], one per check measured in this run.
    Returns the lines to send and the state to keep if they were delivered.
    """
    new = dict(state)
    lines = []
    at = now.isoformat(timespec="seconds")
    for o in observations:
        key, prev = o["key"], state.get(o["key"]) or {}
        if o["ok"]:
            if prev.get("ok") is False:
                lines.append(f"✅ <b>{key}</b> wieder in Ordnung: {o['line']}")
            new[key] = {"ok": True, "since": at if prev.get("ok") is not True
                        else prev.get("since", at)}
            continue
        if prev.get("ok") is not False:
            lines.append(o["line"])
            new[key] = {"ok": False, "since": at, "sent": at}
            continue
        sent = _parse_ts(prev.get("sent", ""))
        if sent is None or (now - sent).total_seconds() / 3600 >= REMIND_HOURS:
            since = _parse_ts(prev.get("since", "")) or now
            hours = (now - since).total_seconds() / 3600
            lines.append(f"{o['line']} (Erinnerung, rot seit {hours:.0f} h)")
            new[key] = dict(prev, sent=at)
        else:
            new[key] = prev
    return lines, new


# --- Thresholds against the schedule ----------------------------------------
def _cron_field(field: str, lo: int, hi: int) -> "list[int] | None":
    """Expand one cron minute/hour field. None for what this does not parse."""
    out = set()
    for part in field.split(","):
        rng, _, step = part.partition("/")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = (int(x) for x in rng.split("-", 1))
        elif rng.isdigit():
            a = b = int(rng)
        else:
            return None
        if step and not step.isdigit():
            return None
        if step and rng.isdigit():
            b = hi
        out.update(range(a, b + 1, int(step) if step else 1))
    return sorted(out)


def cron_gap_hours(line: str) -> "float | None":
    """Longest gap between two runs of a daily crontab schedule, in hours."""
    f = line.split()
    if len(f) < 6 or f[2:5] != ["*", "*", "*"]:
        return None
    minutes, hours = _cron_field(f[0], 0, 59), _cron_field(f[1], 0, 23)
    if not minutes or not hours:
        return None
    runs = sorted(h * 60 + m for h in hours for m in minutes)
    gaps = [b - a for a, b in zip(runs, runs[1:])] + [runs[0] + 1440 - runs[-1]]
    return max(gaps) / 60


# A crontab line that switches itself off on a date: the radar's three
# daytime runs end on 2026-10-18 this way. The schedule after that date is the
# one a threshold has to survive, so the comparison below reads both.
_CRON_GUARD = re.compile(r"-lt\s+(\d{8})\b")


def _weekly_runs(line: str) -> "list[int] | None":
    """Run times of one crontab line as minutes from the start of the week.

    cron_gap_hours() only reads schedules that run every day. A radar line runs
    on three weekdays, and three such lines together form one schedule — which
    is why this counts over a week instead of a day (ops WD-2).
    """
    f = line.split()
    if len(f) < 6 or f[2] != "*" or f[3] != "*":
        return None
    minutes, hours = _cron_field(f[0], 0, 59), _cron_field(f[1], 0, 23)
    days = _cron_field(f[4], 0, 7)
    if not minutes or not hours or not days:
        return None
    days = {0 if d == 7 else d for d in days}  # cron says 0 and 7 for Sunday
    return sorted(d * 1440 + h * 60 + m for d in days for h in hours for m in minutes)


def schedule_gap_hours(lines: list) -> "float | None":
    """Longest pause in a schedule spread over several crontab lines."""
    runs: set = set()
    for line in lines:
        r = _weekly_runs(line)
        if r is None:
            return None
        runs.update(r)
    if not runs:
        return None
    ordered = sorted(runs)
    gaps = ([b - a for a, b in zip(ordered, ordered[1:])]
            + [ordered[0] + 7 * 1440 - ordered[-1]])
    return max(gaps) / 60


def _lasting(lines: list) -> list:
    """The lines that are still there once every date guard has passed."""
    return [l for l in lines if not _CRON_GUARD.search(l)]


# Every check of this watchdog is either compared against a schedule or
# declared here as having none. A check that reads a live state — whether two
# surfaces still say the same thing, whether a signature still verifies — has
# no cadence to outlast, and saying so is what keeps a new check from slipping
# in without anybody asking the question (ops WD-2). Matched by prefix, because
# the surfaces carry their own names: PlatformId/<url>, Withheld/<tool>.
NO_CADENCE = (
    ("CONFORMANCE Drift", "vergleicht CONFORMANCE.md mit dem Repo"),
    ("Discovery/", "vergleicht die Discovery-Flaechen gegeneinander"),
    ("PlatformId/", "liest die Agent-Kennung von den Flaechen"),
    ("AnchorProof", "rechnet den veroeffentlichten Beweis nach"),
    ("x402Validator/", "fragt den fremden Validator"),
    ("Smithery/", "liest das Smithery-Listing"),
    ("Registry↔PyPI", "vergleicht zwei Indizes"),
    ("Withheld/", "prueft die Antwortform des Origin"),
    ("CardSignature/", "prueft die Signatur der Agentenkarte"),
    ("XBudget", "liest das Budget-Fenster von X"),
    ("XSpend", "liest den Tagesverbrauch vom Zaehler"),
)


def watched_names() -> set:
    """Names of the checks that carry a threshold of their own."""
    return {a.get("name") or a.get("surface")
            for a in AGENTS + MOLTBOOK_WRITERS + [RADAR_DEADMAN]}


def cadence_findings(keys) -> list:
    """Checks the watchdog has run that no threshold and no declaration covers.

    data/watchdog_alert_state.json carries one key per check of the last runs,
    so this reads what the watchdog actually did rather than what the tables
    say it does. Until ops WD-2 the radar's twelve hours sat beside those
    tables and nothing compared them.
    """
    named = watched_names()
    out = []
    for key in sorted(keys):
        if key in named or any(key.startswith(p) for p, _ in NO_CADENCE):
            continue
        out.append(f"{key}: keine Takt-Zuordnung und nicht als taktlos erklaert")
    return out


def _state_keys() -> list:
    try:
        with open(ALERT_STATE) as f:
            return list(json.load(f))
    except (OSError, ValueError):
        return []


def _active_lines(crontab: str) -> list:
    return [l for l in crontab.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def _hits(agent: dict, active: list) -> list:
    marker = agent["cron"]
    return [l for l in active if marker in l
            and not (agent.get("cron_not") and agent["cron_not"] in l)]


def _many_gap(hits: list) -> "float | None":
    # Several lines, and the ones with a date guard disappear. The threshold
    # has to outlast both schedules, so the longer pause of the two decides.
    spans = [schedule_gap_hours(hits), schedule_gap_hours(_lasting(hits))]
    return max([g for g in spans if g is not None], default=None)


# Herald and the radar both got the longest pause times one and a half when
# their thresholds were last set by hand (ops WD-1, WD-2). The derivation keeps
# that grace, so a schedule read today gives the number that was typed then.
SCHEDULE_GRACE = 1.5


def derived_max_hours(agent: dict, crontab: "str | None") -> tuple:
    """The threshold for one job, from the crontab lines that schedule it.

    Returns (hours, basis). The basis is the text the watch message carries,
    so a reader sees whether the number came from the schedule or from the
    fallback in the table. Until ops CH8 the radar's threshold was a fixed
    number; in October it was 12 h against a 14 h night pause between 16:05
    and 06:05 and alarmed every night. A schedule that cannot be read does not
    silence the check: it falls back to the table's max_hours and says so.
    """
    fallback = agent["max_hours"]
    if crontab is None:
        return fallback, f"Schwelle {fallback:g} h (Rueckfall, Crontab nicht lesbar)"
    hits = _hits(agent, _active_lines(crontab))
    gap = (_many_gap(hits) if agent.get("cron_lines") == "many"
           else (cron_gap_hours(hits[0]) if len(hits) == 1 else None))
    if not hits or gap is None:
        return fallback, (f"Schwelle {fallback:g} h (Rueckfall, {len(hits)} "
                          f"Crontab-Zeilen fuer '{agent['cron']}', Takt nicht lesbar)")
    limit = gap * SCHEDULE_GRACE
    return limit, f"Schwelle {limit:g} h aus Takt {gap:g} h"


def _crontab_text() -> "str | None":
    """The live crontab, or None when it cannot be read."""
    import subprocess
    try:
        p = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                           timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def threshold_findings(crontab: str, keys=None) -> list:
    """Every watched check whose max_hours does not outlast its schedule."""
    active = _active_lines(crontab)
    out = []
    for agent in AGENTS + MOLTBOOK_WRITERS + [RADAR_DEADMAN]:
        marker = agent.get("cron")
        if not marker:
            continue
        name = agent.get("name") or agent["surface"]
        hits = _hits(agent, active)
        if agent.get("cron_lines") == "many":
            gap = _many_gap(hits)
            if not hits or gap is None:
                out.append(f"{name}: {len(hits)} Crontab-Zeilen fuer '{marker}', "
                           f"Takt nicht lesbar")
            elif agent["max_hours"] <= gap:
                out.append(f"{name}: max {agent['max_hours']} h, Takt {gap:g} h")
            continue
        gaps = [cron_gap_hours(l) for l in hits]
        if len(hits) != 1 or gaps[0] is None:
            out.append(f"{name}: {len(hits)} Crontab-Zeilen fuer '{marker}', Takt nicht lesbar")
        elif agent["max_hours"] <= gaps[0]:
            out.append(f"{name}: max {agent['max_hours']} h, Takt {gaps[0]:g} h")
    return out + cadence_findings(_state_keys() if keys is None else keys)


def check_heartbeat(agent: dict, now: datetime.datetime) -> dict:
    """Check agent health. Returns {ok: bool, detail: str}."""
    name = agent["name"]

    # Method 1: heartbeat JSON file
    hb_file = agent.get("heartbeat_file")
    if hb_file and os.path.exists(hb_file):
        try:
            with open(hb_file) as f:
                hb = json.load(f)
            ts_key = agent.get("heartbeat_ts_key", "timestamp")
            ts = datetime.datetime.fromisoformat(hb[ts_key])
            age_h = (now - ts).total_seconds() / 3600
            status = hb.get("status", "unknown")
            if age_h > agent["max_hours"]:
                return {"ok": False, "detail": f"Last heartbeat {age_h:.1f}h ago (max {agent['max_hours']}h), status={status}"}
            if status in ("crash", "error"):
                return {"ok": False, "detail": f"Heartbeat status={status}: {hb.get('detail', '')[:200]}"}
            return {"ok": True, "detail": f"Heartbeat {age_h:.1f}h ago, status={status}"}
        except Exception as e:
            return {"ok": False, "detail": f"Heartbeat file unreadable: {e}"}

    # Method 2: check latest glob file
    fallback_glob = agent.get("fallback_glob")
    if fallback_glob:
        files = sorted(glob.glob(os.path.join(LOG_DIR, fallback_glob)))
        if not files:
            return {"ok": False, "detail": "No output files found"}
        latest = files[-1]
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(latest), tz=datetime.UTC)
        age_h = (now - mtime).total_seconds() / 3600
        if age_h > agent["max_hours"]:
            return {"ok": False, "detail": f"Latest file {age_h:.1f}h old (max {agent['max_hours']}h): {os.path.basename(latest)}"}
        return {"ok": True, "detail": f"Latest file {age_h:.1f}h ago: {os.path.basename(latest)}"}

    # Method 3: check log file mtime
    fallback_log = agent.get("fallback_log")
    if fallback_log:
        log_path = os.path.join(LOG_DIR, fallback_log)
        if not os.path.exists(log_path):
            return {"ok": False, "detail": f"Log file missing: {fallback_log}"}
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(log_path), tz=datetime.UTC)
        age_h = (now - mtime).total_seconds() / 3600
        if age_h > agent["max_hours"]:
            return {"ok": False, "detail": f"Log stale: {age_h:.1f}h old (max {agent['max_hours']}h)"}
        return {"ok": True, "detail": f"Log updated {age_h:.1f}h ago"}

    return {"ok": False, "detail": "No check method configured"}



def _live_mcp_tool_count() -> "int | None":
    """tools/list from the running MCP server (local :8002, no auth needed)."""
    import asyncio
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    async def _q() -> int:
        async with streamablehttp_client(MCP_LOCAL_URL) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return len((await s.list_tools()).tools)

    try:
        return asyncio.run(_q())
    except Exception:
        return None


def _smithery_frozen_verdict(r, live: int, today: datetime.date) -> dict:
    """The Smithery entry no longer tracks the origin; only its loss is a finding."""
    if r.status_code in (404, 410):
        return {"surface": "MCP↔Smithery", "ok": False,
                "detail": f"Smithery entry gone (HTTP {r.status_code}) — the listing "
                          "was removed and Smithery no longer accepts new servers"}
    try:
        r.raise_for_status()
        listed = len(r.json().get("tools") or [])
    except Exception as e:
        return {"surface": "MCP↔Smithery", "ok": True, "unverifiable": True,
                "detail": f"Smithery registry answered {r.status_code} "
                          f"({type(e).__name__}), skipped"}
    if listed == 0:
        return {"surface": "MCP↔Smithery", "ok": False,
                "detail": "Smithery entry lists no tools — the record was emptied"}
    seen = {"listed": listed, "live": live, "deployment_url": _smithery_deployment_url(r)}
    if today < SMITHERY_FROZEN_FROM:
        return {"surface": "MCP↔Smithery", "ok": True, "frozen": False, "seen": seen,
                "detail": f"Smithery lists {listed} tools, origin {live}; re-publish "
                          f"possible until {SMITHERY_FROZEN_FROM.isoformat()}, frozen after"}
    return {"surface": "MCP↔Smithery", "ok": True, "frozen": True, "seen": seen,
            "detail": f"Smithery frozen at {listed} tools since "
                      f"{SMITHERY_FROZEN_FROM.isoformat()} (updates disabled by Smithery), "
                      f"origin {live}"}


def _smithery_deployment_url(r) -> str:
    try:
        d = r.json()
        urls = [d.get("deploymentUrl")] + [c.get("deploymentUrl") for c in d.get("connections") or []]
        return next((u for u in urls if u and SMITHERY_DIRECT_HOST in u), d.get("deploymentUrl") or "")
    except Exception:
        return ""


def _smithery_news(seen: dict) -> list:
    """What the support request asked for and Smithery now shows, as notice keys."""
    news = []
    if seen["listed"] > SMITHERY_LISTED_BEFORE_RESCAN:
        news.append("tools")
    if SMITHERY_DIRECT_HOST in (seen["deployment_url"] or ""):
        news.append("direct_url")
    return news


def _announce_smithery_news(seen: dict, path: str | None = None) -> list:
    """Send one WORKLOG message per newly seen change; returns the keys sent."""
    path = path or SMITHERY_NOTICE_FILE
    try:
        with open(path) as fh:
            done = json.load(fh)
    except Exception:
        done = {}
    fresh = [k for k in _smithery_news(seen) if k not in done]
    if not fresh:
        return []
    parts = {"tools": f"lists {seen['listed']} tools (origin {seen['live']})",
             "direct_url": f"points at {seen['deployment_url']}"}
    text = ("Smithery took the rescan: @moltrust/moltrust-mcp-server now "
            + " and ".join(parts[k] for k in fresh) + ".")
    if not notify.send_telegram(text, channel=notify.WORKLOG):
        return []  # not recorded, so the next run tries again
    stamp = datetime.datetime.now(datetime.UTC).isoformat()
    done.update({k: stamp for k in fresh})
    try:
        with open(path, "w") as fh:
            json.dump(done, fh)
    except Exception:
        pass
    return fresh


def check_discovery_drift(now: datetime.datetime) -> list:
    """Reconcile the two discovery surfaces against what we actually serve.
    Returns a list of {surface, ok, detail}."""
    out = []
    # 1) MCP tool catalog: running server vs Smithery listing.
    live = _live_mcp_tool_count()
    if live is None:
        out.append({"surface": "MCP", "ok": False,
                    "detail": "tools/list unreachable (mcp_http :8002 down?)"})
    else:
        try:
            # cache-bust: registry.smithery.ai sits behind Cloudflare (max-age 4h,
            # stale-while-revalidate 24h); a fresh read keeps "gone" from lagging.
            r = httpx.get(SMITHERY_REGISTRY_URL, params={"_cb": int(now.timestamp())},
                          headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
                          timeout=12.0)
        except Exception as e:
            r = None
            # A Smithery registry outage must not masquerade as a lost entry.
            out.append({"surface": "MCP↔Smithery", "ok": True, "unverifiable": True,
                        "detail": f"Smithery registry unreachable ({type(e).__name__}), skipped"})
        if r is not None:
            verdict = _smithery_frozen_verdict(r, live, now.date())
            out.append(verdict)
            if "seen" in verdict:
                _announce_smithery_news(verdict["seen"])
    # 1b) Glama listing vs the same origin count, once on Mondays.
    if _is_weekly_slot(now, GLAMA_CHECK_WEEKDAY) and live is not None:
        out.append(_check_glama(live))

    # 1c) The published x402 terms vs what the API actually challenges for.
    out.append(_check_x402_discovery())

    # 2) A2A Agent-Card skills vs pinned baseline.
    try:
        card = httpx.get(AGENT_CARD_URL, timeout=10.0).json()
        skills = card.get("skills") or card.get("capabilities") or []
        n = len(skills) if isinstance(skills, list) else 0
        if n != EXPECTED_AGENT_CARD_SKILLS:
            out.append({"surface": "Agent-Card", "ok": False,
                        "detail": f"card exposes {n} skills, baseline {EXPECTED_AGENT_CARD_SKILLS} "
                                  f"— update the card or bump EXPECTED_AGENT_CARD_SKILLS"})
        else:
            out.append({"surface": "Agent-Card", "ok": True,
                        "detail": f"{n} skills == baseline"})
    except Exception as e:
        out.append({"surface": "Agent-Card", "ok": False,
                    "detail": f"agent-card fetch failed: {type(e).__name__}"})
    return out


# Each tool Glama has actually indexed renders an "<name>Arguments" block on the
# listing page. Counting those counts Glama's crawl.
#
# The earlier check was `live in {every "N tools" on the page}`, which is a much
# weaker question. The page is 790 KB: it carries the neighbouring servers in
# the sidebar, each with its own tool count, and it carries our own description
# text, which we wrote and which says "53 tools across 12 areas". On 2026-09-21
# that description reached the page and the check went green while Glama's own
# analysis still read "With 48 tools, the count is excessive" — a listing that
# had not re-crawled, reported as in sync. A check that can go quiet for the
# wrong reason is worse than no check.
_GLAMA_INDEXED_TOOL = re.compile(r"\b([a-z][a-z0-9_]{3,40})Arguments\b")


def _package_mcp_tool_count() -> "tuple[int | None, str]":
    """What the published moltrust-mcp-server package declares, and why not.

    Not the same number as the running server. `services/mcp_http.py` composes
    the hosted endpoint from the package plus `register_molcheck_tools`, which
    lives in this repository and ships to nobody. The package is what a user
    installs and what the GitHub repository declares.

    A `None` here is this deployment's defect, not a condition to wait out. The
    package is a dependency of the watchdog's own venv: if it cannot be
    imported, the comparison underneath never runs, and every Monday reports a
    green skip. Run from an interpreter without it installed — which is every
    ad-hoc `python3 -c` on this host — the whole Glama check reads as a quiet
    success. The reason travels with the number so the alert can name it.
    """
    import asyncio

    try:
        from moltrust_mcp_server.server import mcp as package_mcp
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    try:
        return len(asyncio.run(package_mcp.list_tools())), ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _check_glama(live: int) -> dict:
    """Compare the Glama listing against the *package*, not the running server.

    This compared Glama against the origin until 2026-09-21 and read every
    difference as a stale listing. It is not: Glama indexes the GitHub
    repository, and the repository declares 48 tools — the exact 48 Glama shows,
    name for name. The origin exposes 53 because the hosted endpoint adds five
    moltproof_* tools that exist only in this repository.

    Two surfaces, two correct answers to two different questions: Smithery
    lists the remote at origin and says 53, Glama lists the package and says 48.
    Comparing one against the other produced a drift alarm for an architecture
    decision, and advised a re-index that would have changed nothing.

    Glama has no unauthenticated API, so this reads the public page. A parse
    that finds nothing is reported as a skip, not as drift — a layout change on
    their side is not our listing going stale.
    """
    try:
        html = httpx.get(GLAMA_LISTING_URL, timeout=15.0,
                         headers={"User-Agent": "MolTrust-Watchdog/1.0"}).text
    except Exception as e:
        return {"surface": "MCP↔Glama", "ok": True, "unverifiable": True,
                "detail": f"Glama unreachable ({type(e).__name__}), skipped"}

    indexed = {m.group(1) for m in _GLAMA_INDEXED_TOOL.finditer(html)}
    if not indexed:
        return {"surface": "MCP↔Glama", "ok": True, "unverifiable": True,
                "detail": "no indexed tools found on the listing page, skipped"}

    packaged, why = _package_mcp_tool_count()
    if packaged is None:
        # Not a skip. Glama being unreachable is their side; the package not
        # importing is ours, and it silences the comparison indefinitely.
        return {"surface": "MCP↔Glama", "ok": False,
                "detail": f"Glama has indexed {len(indexed)} tools, but "
                          f"moltrust-mcp-server could not be read in this "
                          f"interpreter ({why}) — the comparison did not run. "
                          f"Install the package into the watchdog venv: "
                          f"~/moltstack/venv/bin/pip install -U moltrust-mcp-server"}

    hosted_extra = (live - packaged) if live is not None else None
    suffix = ("" if not hosted_extra
              else f"; the hosted endpoint adds {hosted_extra} more, by design")
    if len(indexed) == packaged:
        return {"surface": "MCP↔Glama", "ok": True,
                "detail": f"{packaged} tools indexed (package == listing){suffix}"}
    return {"surface": "MCP↔Glama", "ok": False,
            "detail": f"the package declares {packaged} tools, Glama has indexed "
                      f"{len(indexed)} — the listing has not re-crawled since the "
                      f"package changed{suffix}"}


def _version_key(v: str) -> tuple:
    """Order versions numerically. `max()` over strings puts 0.9.0 above 0.10.0,
    and the registry hands back every version it has ever held."""
    parts = []
    for chunk in v.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_registry_matches_pypi() -> dict:
    """One package, two indices, one tag — and they came apart anyway.

    On 2026-09-22 the v1.2.3 tag published the wheel to PyPI and then failed at
    `mcp-publisher validate`: the registry caps descriptions at 100 characters
    now, ours was 214, and the rule had tightened since 0.7.0 was published. The
    workflow ordering makes that the expensive half — PyPI first, registry
    second — so the failure left the package current and the registry five
    versions behind, with nothing to say so. Before that it had sat at 0.7.0 for
    four months for the same reason: nothing compared them.

    An index being unreachable is reported as a skip. A disagreement is not.
    """
    try:
        pypi = httpx.get(PYPI_PACKAGE_URL, timeout=15.0).json()["info"]["version"]
    except Exception as e:
        return {"surface": "Registry↔PyPI", "ok": True, "unverifiable": True,
                "detail": f"PyPI unreachable ({type(e).__name__}), skipped"}
    try:
        servers = httpx.get(MCP_REGISTRY_SEARCH_URL, timeout=15.0).json()["servers"]
    except Exception as e:
        return {"surface": "Registry↔PyPI", "ok": True, "unverifiable": True,
                "detail": f"MCP registry unreachable ({type(e).__name__}), skipped"}

    # The search endpoint matches on a substring, so someone else's fork would
    # come back in the same list. Only our own name decides.
    ours = [s["server"]["version"] for s in servers
            if s.get("server", {}).get("name") == MCP_REGISTRY_SERVER_NAME]
    if not ours:
        return {"surface": "Registry↔PyPI", "ok": False,
                "detail": f"PyPI has {pypi}, and {MCP_REGISTRY_SERVER_NAME} is not "
                          f"in the MCP registry at all — the listing was removed "
                          f"or renamed"}

    registry = max(ours, key=_version_key)
    if _version_key(registry) == _version_key(pypi):
        return {"surface": "Registry↔PyPI", "ok": True,
                "detail": f"both indices on {pypi}"}
    return {"surface": "Registry↔PyPI", "ok": False,
            "detail": f"PyPI has {pypi}, the MCP registry has {registry} — a "
                      f"release reached one index and not the other. Re-run the "
                      f"publish workflow on main (workflow_dispatch); it skips "
                      f"the PyPI upload it already made."}


def _mcp_session(url: str, timeout: float = 20.0) -> "tuple[httpx.Client, str]":
    """Open a streamable-http MCP session the way a client does.

    initialize, then the initialized notification, then the session id travels
    in a header. Answers come back as SSE frames even for a single reply.
    """
    client = httpx.Client(timeout=timeout)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    r = client.post(url, headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "moltrust-watchdog", "version": "1.0"}}})
    r.raise_for_status()
    sid = r.headers.get("Mcp-Session-Id") or ""
    client.post(url, headers={**headers, "Mcp-Session-Id": sid},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    return client, sid


def _mcp_call_text(client: "httpx.Client", url: str, sid: str,
                   tool: str, args: dict) -> str:
    """Call one tool and return the text a reader would see."""
    r = client.post(url, headers={"Content-Type": "application/json",
                                  "Accept": "application/json, text/event-stream",
                                  "Mcp-Session-Id": sid},
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": tool, "arguments": args}})
    r.raise_for_status()
    body = r.text
    payload = None
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            break
    if payload is None and body.strip():
        payload = json.loads(body)
    result = (payload or {}).get("result", payload or {})
    parts = [c.get("text", "") for c in result.get("content", [])
             if isinstance(c, dict)]
    return "\n".join(parts) or json.dumps(result)


def check_withheld_rendering() -> list:
    """Does the hosted origin still tell withheld apart from not-found?

    `verified: false` covers two findings that mean opposite things. One is that
    we looked and there is nothing. The other is that we hold no opinion,
    because the DID belongs to a method we do not issue or the score has too few
    endorsers behind it. The API separates them; through 1.2.3 the MCP package
    did not, and a well-formed did:web came back as "Agent not found in MolTrust
    registry".

    The package's own vectors cover this against recorded responses. What they
    cannot see is the origin running a venv nobody upgraded — on 2026-09-22 the
    hosted endpoint served 1.2.2 for a day after 1.2.3 was published, and no
    test anywhere was red about it.

    Each probe reads the API first. Only a response the API actually withholds
    is compared, so an agent that gains endorsers retires its probe rather than
    raising an alarm about a fix that is still in place.
    """
    out = []
    try:
        client, sid = _mcp_session(MCP_LOCAL_URL)
    except Exception as e:
        return [{"surface": "Withheld/origin", "ok": True, "unverifiable": True,
                 "detail": f"MCP origin unreachable ({type(e).__name__}), skipped"}]

    try:
        for tool, args, route, subkeys, upstream_miss in WITHHELD_PROBES:
            try:
                body = httpx.get(API_BASE + route, timeout=15.0).json()
            except Exception as e:
                out.append({"surface": f"Withheld/{tool}", "ok": True, "unverifiable": True,
                            "detail": f"API unreachable ({type(e).__name__}), skipped"})
                continue
            payload = body
            for k in subkeys:
                payload = (payload or {}).get(k) or {}
            if not isinstance(payload, dict) or not payload.get("withheld"):
                out.append({"surface": f"Withheld/{tool}", "ok": True,
                            "detail": f"the API no longer withholds {route}; "
                                      f"nothing to compare, probe retired"})
                continue

            try:
                text = _mcp_call_text(client, MCP_LOCAL_URL, sid, tool, args)
            except Exception as e:
                out.append({"surface": f"Withheld/{tool}", "ok": False,
                            "detail": f"the tool could not be called at the origin "
                                      f"({type(e).__name__}: {e})"})
                continue

            miss = next((m for m in upstream_miss if m.lower() in text.lower()), None)
            if miss:
                out.append({"surface": f"Withheld/{tool}", "ok": True,
                            "detail": f"the route behind {tool} did not answer this "
                                      f"call ({miss!r}); nothing to compare"})
                continue

            low = text.lower()
            reason = payload.get("withheld_reason")
            problems = []
            if "withheld" not in low:
                problems.append("the text does not say withheld")
            if "not found" in low:
                problems.append('it says "not found" for an answer that was withheld')
            if reason and reason not in text:
                problems.append(f"it does not name the API's reason ({reason})")
            if problems:
                out.append({"surface": f"Withheld/{tool}", "ok": False,
                            "detail": "; ".join(problems)
                                      + f" — first line: {text.splitlines()[0][:80]!r}. "
                                      f"Check the package version in the origin venv."})
            else:
                out.append({"surface": f"Withheld/{tool}", "ok": True,
                            "detail": f"withheld rendered"
                                      + (f" with reason {reason}" if reason else "")})
    finally:
        client.close()
    return out


def _check_x402_discovery() -> dict:
    """Compare /.well-known/x402.json against a live 402 challenge.

    These are two statements of the same terms to the same audience, and they
    have disagreed before: the document listed market/feed as free while the
    middleware charged 0.10 for it, and declared protocol version 1 while the
    challenge advertised 2. The Bazaar indexes the document, so a disagreement
    is published rather than merely internal.
    """
    try:
        doc = httpx.get(X402_DISCOVERY_URL, timeout=10.0).json()
        challenge = httpx.get(X402_PRICED_SAMPLE, timeout=12.0).json()
    except Exception as e:
        return {"surface": "x402-discovery", "ok": True, "unverifiable": True,
                "detail": f"could not read both surfaces ({type(e).__name__}), skipped"}

    accepts = (challenge.get("x402") or {}).get("accepts") or [{}]
    offer = accepts[0]
    problems = []

    doc_version = str(doc.get("version", ""))
    # x402Version since moltguard#23 brought the challenge onto the spec shape;
    # `version` was the old string-valued field. Both are read so this keeps
    # working across a deploy in either direction rather than reporting a
    # phantom drift for the minutes in between.
    _x402 = challenge.get("x402") or {}
    live_version = str(_x402.get("x402Version", _x402.get("version", "")))
    if doc_version != live_version:
        problems.append(f"version {doc_version!r} in the document, {live_version!r} in the challenge")

    if str(doc.get("payTo", "")).lower() != str(offer.get("payTo", "")).lower():
        problems.append("payTo differs")
    if str(doc.get("asset", "")).lower() != str(offer.get("asset", "")).lower():
        problems.append("asset differs")
    if str(doc.get("network", "")) != str(offer.get("network", "")):
        problems.append("network differs")

    # The sampled endpoint is the one the document prices first; if its price
    # moved, the rest of the table is suspect too. The live offer carries the
    # amount under `amount`; `maxAmountRequired` was the v1 spelling and is gone
    # from the v2 requirements.
    priced = {e.get("path"): e.get("price") for e in (doc.get("endpoints") or [])}
    doc_price = priced.get("/guard/api/agent/score/{address}")
    live_amount = offer.get("amount")
    if doc_price is not None and live_amount is not None:
        if int(round(float(doc_price) * 1_000_000)) != int(live_amount):
            problems.append(f"score price {doc_price} in the document, {live_amount} base units live")

    if problems:
        return {"surface": "x402-discovery", "ok": False,
                "detail": "; ".join(problems) + " — /.well-known/x402.json is published to the Bazaar"}
    return {"surface": "x402-discovery", "ok": True,
            "detail": "document matches the live challenge"}


def check_conformance_drift() -> dict:
    """Check if CONFORMANCE.md files match live API checksum."""
    import subprocess
    try:
        result = subprocess.run(
            ["/home/moltstack/moltguard/scripts/check_drift.sh"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {"ok": True, "detail": "CONFORMANCE.md in sync with API"}
        elif result.returncode == 1:
            # Extract drift details from output
            lines = [l for l in result.stdout.strip().split("\n") if "DRIFT" in l or "Missing" in l]
            detail = "; ".join(lines[:3]) if lines else "Drift detected"
            return {"ok": False, "detail": detail}
        else:
            return {"ok": False, "detail": f"API unreachable (exit {result.returncode})"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "Drift check timed out (15s)"}
    except Exception as e:
        return {"ok": False, "detail": f"Drift check error: {e}"}


PLATFORM_ID_SURFACES = (
    "https://moltrust.ch/llms.txt",
    "https://moltrust.ch/agents.txt",
    "https://api.moltrust.ch/llms.txt",
)

# The secondary registration is real and stays resolvable, so a page may name
# it — but only as the earlier one, never as the platform identity.
_SECONDARY_OK = ("earlier registration", "secondary", "also registered")


def check_platform_id_drift() -> list:
    """Do the machine-readable surfaces still name the canonical agent id?

    The id moved from 33553 to 21023 on 2026-09-19 and four text surfaces kept
    the old number for a day. A number in prose has no schema to validate it
    against, so nothing caught it — this does.
    """
    from app.erc8004 import (
        MOLTRUST_PLATFORM_AGENT_ID as CANON,
        MOLTRUST_PLATFORM_SECONDARY_AGENT_IDS as SECONDARY,
    )
    results = []
    for url in PLATFORM_ID_SURFACES:
        try:
            resp = httpx.get(url, timeout=15,
                             headers={"User-Agent": "MolTrust-watchdog/1.0"})
            resp.raise_for_status()
            body = resp.text
        except Exception as e:
            results.append({"surface": url, "ok": False,
                            "detail": f"unreachable: {type(e).__name__}"})
            continue

        if str(CANON) not in body:
            results.append({"surface": url, "ok": False,
                            "detail": f"canonical agent id {CANON} not mentioned"})
            continue

        # A stale id is only a fault when it is not marked as the old one.
        stale = [s for s in SECONDARY
                 if str(s) in body and not any(m in body.lower() for m in _SECONDARY_OK)]
        if stale:
            results.append({"surface": url, "ok": False,
                            "detail": f"names {stale} without marking it as superseded"})
            continue

        results.append({"surface": url, "ok": True, "detail": f"agent id {CANON}"})
    return results



# Weekly: does our own published proof actually replay?
#
# An external auditor did this by hand on 2026-09-20 and found three defects.
# What they did is what this does, using nothing but the public API, the public
# RPC and the rule on anchoring.html — no internal function is called, because
# a check that shares code with the thing it checks agrees with it by
# construction.
PROOF_CHECK_WEEKDAY = 6  # Sunday, so a failure lands before the week starts
PROOF_CHECK_DID = "did:moltrust:157224190be24072"
ANCHOR_CALLDATA_PREFIX = "MolTrust/VC/v1/"
# Our own run, so it belongs on our own quota. The public endpoint promises
# nothing and throttles at its own discretion — measured on 2026-10-04 and -05:
# eth_getLogs capped at 50 blocks, and 403 from one of the open providers mid
# verification.
#
# Read at the call site, not here. Importing this module is not using the
# endpoint: four test modules import it for entirely unrelated checks, and on
# 2026-10-09 the strict reader turned that import into a collection error in
# CI. An endpoint that is only needed inside one function is only read there.


def _replay_merkle(leaf_hex: str, path: list) -> str:
    """Fold a proof into a root, per anchoring.html#proof.

    SHA-256 over the concatenated raw bytes, sibling on the side its
    `position` names. Hex text is not hashed; hashing the hex would produce a
    different tree and is the mistake this spells out to avoid.
    """
    cur = bytes.fromhex(leaf_hex)
    for step in path:
        sib = bytes.fromhex(step["hash"])
        cur = hashlib.sha256(
            (sib + cur) if step.get("position") == "left" else (cur + sib)
        ).digest()
    return cur.hex()


def check_anchor_proof_replay() -> dict:
    """Fetch a credential's proof and recompute it against the chain."""
    try:
        r = httpx.get(f"https://api.moltrust.ch/identity/verify/{PROOF_CHECK_DID}",
                      timeout=20.0, headers={"User-Agent": "MolTrust-Watchdog/1.0"})
        r.raise_for_status()
        creds = r.json().get("credentials") or []
    except Exception as e:
        return {"ok": False, "detail": f"identity/verify unreachable: {type(e).__name__}"}

    anchored = [c for c in creds if (c.get("anchor") or {}).get("tx_hash")]
    if not anchored:
        return {"ok": False, "detail": f"{PROOF_CHECK_DID} has no anchored credential"}

    a = anchored[0]["anchor"]
    proof, root, tx = a.get("merkle_proof"), a.get("merkle_root"), a["tx_hash"]

    # An anchor without a proof is the state this check exists to catch: it
    # looks fine in the response and cannot be verified by anyone.
    if not proof or not proof.get("leaf") or not isinstance(proof.get("path"), list):
        return {"ok": False, "detail": f"credential {anchored[0].get('id')} has no usable proof"}

    try:
        computed = _replay_merkle(proof["leaf"], proof["path"])
    except Exception as e:
        return {"ok": False, "detail": f"proof malformed: {type(e).__name__}"}
    if computed != root:
        return {"ok": False,
                "detail": f"proof does not replay: got {computed[:16]}…, root {str(root)[:16]}…"}

    # The root has to be the one actually on chain, read as UTF-8 text.
    try:
        resp = httpx.post(base_rpc_url(), timeout=20.0,
                          headers={"User-Agent": "MolTrust-Watchdog/1.0"},
                          json={"jsonrpc": "2.0", "id": 1,
                                "method": "eth_getTransactionByHash", "params": [tx]})
        resp.raise_for_status()
        result = resp.json().get("result")
        if not result:
            return {"ok": False, "detail": f"anchor tx {tx[:12]}… not found on Base"}
        calldata = bytes.fromhex(result["input"][2:]).decode("utf-8", "replace")
    except Exception as e:
        return {"ok": False, "detail": f"Base RPC unreachable: {type(e).__name__}"}

    if not calldata.startswith(ANCHOR_CALLDATA_PREFIX):
        return {"ok": False, "detail": f"calldata is not {ANCHOR_CALLDATA_PREFIX}…: {calldata[:40]!r}"}
    if calldata.rsplit("/", 1)[-1] != root:
        return {"ok": False, "detail": "calldata root differs from the published root"}

    return {"ok": True,
            "detail": f"proof replays, {len(proof['path'])} steps, root on chain in {tx[:12]}…"}


# ---------------------------------------------------------------------------
# Weekly: does the published agent card still verify for an outsider?
#
# On 2026-09-20 the static web-root card was edited in place and not re-signed.
# For a day it shipped a signature covering a body that no longer existed, and
# nothing noticed: the test suite verified synthetic cards with the signer's own
# canonicalizer, and no check ever looked at the served bytes. A card with a
# broken signature is worse than an unsigned one — it advertises verifiability
# and then fails the check, so an A2A registry reads it as a forgery rather than
# as an unsigned card.
#
# This fetches the two documents a stranger has — the card and the JWK — and
# verifies them with lib.agent_card_verify, which reimplements RFC 8785 instead
# of importing the canonicalizer that produced the signature.
CARD_CHECK_WEEKDAY = 6  # Sunday, alongside the anchor-proof replay
CARD_SURFACES = (
    ("api.moltrust.ch", "https://api.moltrust.ch/.well-known/agent-card.json"),
    ("moltrust.ch", "https://moltrust.ch/.well-known/agent-card.json"),
)
REGISTRY_KEY_URL = "https://api.moltrust.ch/.well-known/registry-key.json"


# ---------------------------------------------------------------------------
# Weekly: do the three things Smithery verification rests on still hold?
#
# There is no flag to read. The registry record at
# registry.smithery.ai/servers/moltrust/moltrust-mcp-server carries
# qualifiedName, tools, connections and deployment, and nothing about
# verification — checked 2026-09-21, and the "verified" that greps out of it is
# our own tool description ("Verified by MolTrust badge status").
#
# So this checks the preconditions instead, which are the parts that can quietly
# stop being true:
#
#   the TXT record   smithery-verification=… on moltrust.ch. A DNS edit at the
#                    registrar, by anyone, removes it silently.
#   the backlinks    developers.html and the moltrust-mcp-server README. A page
#                    rewrite drops a link without anyone noticing it was load
#                    bearing.
#   the listing      still reachable at all.
#
# Not their badge endpoint: it answers 500 for every server, ours and
# @smithery-ai/github alike, so alerting on it would alert on their outage.
SMITHERY_CHECK_WEEKDAY = 0  # Monday, with the Glama check
SMITHERY_TXT_PREFIX = "smithery-verification="
SMITHERY_LISTING_URL = "https://smithery.ai/servers/moltrust/moltrust-mcp-server"
SMITHERY_BACKLINKS = (
    ("developers.html", "https://moltrust.ch/developers.html"),
    ("mcp-server README",
     "https://raw.githubusercontent.com/MoltyCel/moltrust-mcp-server/main/README.md"),
)


def _txt_records(name: str) -> "list[str] | None":
    """TXT records for `name`, or None when DNS itself could not be asked."""
    import subprocess  # nosec B404 - dig with a constant argv, no shell

    try:
        out = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
            ["/usr/bin/dig", "+short", "TXT", name, "@1.1.1.1"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [line.strip().strip('"') for line in out.stdout.splitlines() if line.strip()]


def check_smithery_verification() -> list:
    """The TXT record, the two backlinks, and that the listing still answers."""
    out = []

    txt = _txt_records("moltrust.ch")
    if txt is None:
        out.append({"surface": "Smithery/TXT", "ok": True, "unverifiable": True,
                    "detail": "DNS not answerable from here, skipped"})
    elif any(r.startswith(SMITHERY_TXT_PREFIX) for r in txt):
        out.append({"surface": "Smithery/TXT", "ok": True,
                    "detail": "verification TXT record present on moltrust.ch"})
    else:
        out.append({"surface": "Smithery/TXT", "ok": False,
                    "detail": f"no {SMITHERY_TXT_PREFIX}… record on moltrust.ch — "
                              "verification lapses without it"})

    for label, url in SMITHERY_BACKLINKS:
        try:
            body = httpx.get(url, timeout=20.0, follow_redirects=True,
                             headers={"User-Agent": "MolTrust-Watchdog/1.0"}).text
        except Exception as e:
            out.append({"surface": f"Smithery/{label}", "ok": True, "unverifiable": True,
                        "detail": f"unreachable ({type(e).__name__}), skipped"})
            continue
        if "smithery.ai/servers/moltrust/moltrust-mcp-server" in body:
            out.append({"surface": f"Smithery/{label}", "ok": True,
                        "detail": "backlink present"})
        else:
            out.append({"surface": f"Smithery/{label}", "ok": False,
                        "detail": "backlink gone — verification asks for it, and a "
                                  "page rewrite drops it without anyone noticing"})

    try:
        r = httpx.get(SMITHERY_LISTING_URL, timeout=20.0, follow_redirects=True,
                      headers={"User-Agent": "MolTrust-Watchdog/1.0"})
        detail = f"listing answers {r.status_code}"
        # A redirect off smithery.ai is the shape the Arcade migration would
        # take, and it would otherwise read as a healthy 200 after following.
        final = str(r.url)
        if "smithery.ai" not in final:
            out.append({"surface": "Smithery/listing", "ok": False,
                        "detail": f"listing now redirects to {final[:80]} — "
                                  "the migration moved it"})
        else:
            out.append({"surface": "Smithery/listing",
                        "ok": r.status_code == 200, "detail": detail})
    except Exception as e:
        out.append({"surface": "Smithery/listing", "ok": True, "unverifiable": True,
                    "detail": f"listing unreachable ({type(e).__name__}), skipped"})

    out.extend(_check_smithery_record())
    out.extend(_check_arcade_migration())
    return out


# The registry's *search* endpoint carries what the detail record does not:
# verified, useCount, unlisted, inactive. Found 2026-09-21 — the detail record
# at /servers/<ns>/<slug> has none of them, which is why an earlier reading of
# this API concluded there was no verification flag at all.
SMITHERY_SEARCH_URL = "https://registry.smithery.ai/servers?q=moltrust"
SMITHERY_QUALIFIED_NAME = "moltrust/moltrust-mcp-server"
SMITHERY_USE_COUNT_FILE = os.path.join(DATA_DIR, "smithery_use_count.json")


def _check_smithery_record() -> list:
    """verified, useCount and the two flags that mean we have been delisted."""
    try:
        r = httpx.get(SMITHERY_SEARCH_URL, timeout=20.0,
                      headers={"User-Agent": "MolTrust-Watchdog/1.0"})
        r.raise_for_status()
        servers = r.json().get("servers") or []
    except Exception as e:
        return [{"surface": "Smithery/record", "ok": True, "unverifiable": True,
                 "detail": f"registry unreachable ({type(e).__name__}), skipped"}]

    ours = next((s for s in servers
                 if (s.get("qualifiedName") or "").lower() == SMITHERY_QUALIFIED_NAME), None)
    if ours is None:
        return [{"surface": "Smithery/record", "ok": False,
                 "detail": f"{SMITHERY_QUALIFIED_NAME} no longer in the registry search "
                           "— delisted, renamed, or the search changed shape"}]

    out = []
    # Delisting is the alarm. `verified` is reported but is not one: it has been
    # False since the beginning and the fifth requirement (a paid plan) cannot
    # be met — the subscribe button does nothing, checked 2026-09-21.
    if ours.get("unlisted") or ours.get("inactive"):
        out.append({"surface": "Smithery/record", "ok": False,
                    "detail": f"unlisted={ours.get('unlisted')} "
                              f"inactive={ours.get('inactive')} — the entry is no "
                              "longer being served to users"})
    else:
        out.append({"surface": "Smithery/record", "ok": True,
                    "detail": f"listed and active, verified={ours.get('verified')}, "
                              f"deployed={ours.get('isDeployed')}"})

    # Usage is a trend, not a threshold. Reported with the delta so a flat line
    # or a fall is visible without anyone having to remember last week's number.
    count = ours.get("useCount")
    if isinstance(count, int):
        previous = None
        try:
            with open(SMITHERY_USE_COUNT_FILE) as fh:
                previous = json.load(fh).get("useCount")
        except Exception:
            previous = None
        if previous is None:
            trend = "first reading"
        else:
            delta = count - previous
            trend = f"{delta:+d} since the last check"
        out.append({"surface": "Smithery/usage", "ok": True,
                    "detail": f"useCount {count} ({trend})"})
        try:
            with open(SMITHERY_USE_COUNT_FILE, "w") as fh:
                json.dump({"useCount": count,
                           "read_at": datetime.datetime.now(datetime.UTC).isoformat()}, fh)
        except Exception:
            pass
    return out


# Smithery was acquired by Arcade.dev; the pricing page carries the banner and
# the subscribe button does nothing. Nobody has said what happens to the
# listings, so these are the shapes a migration would arrive in.
ARCADE_BANNER_MARKERS = ("arcade.dev", "part of arcade", "arcade")
SMITHERY_HOME = "https://smithery.ai/"


def _check_arcade_migration() -> list:
    """Has smithery.ai started moving, redirecting or renaming itself?"""
    try:
        r = httpx.get(SMITHERY_HOME, timeout=20.0, follow_redirects=True,
                      headers={"User-Agent": "MolTrust-Watchdog/1.0"})
    except Exception as e:
        return [{"surface": "Smithery/migration", "ok": True, "unverifiable": True,
                 "detail": f"home unreachable ({type(e).__name__}), skipped"}]

    final = str(r.url)
    if "smithery.ai" not in final:
        return [{"surface": "Smithery/migration", "ok": False,
                 "detail": f"smithery.ai now redirects to {final[:80]} — the "
                           "migration has started; the listing and its backlinks "
                           "need re-pointing"}]

    body = r.text.lower()
    hits = [m for m in ARCADE_BANNER_MARKERS if m in body]
    if not hits:
        # The banner going away is as informative as it arriving: either the
        # acquisition note was dropped, or the page changed enough that this
        # check stopped meaning anything.
        return [{"surface": "Smithery/migration", "ok": True,
                 "detail": "no Arcade banner on smithery.ai any more — either "
                           "resolved or the page changed; worth a look"}]
    return [{"surface": "Smithery/migration", "ok": True,
             "detail": f"still smithery.ai, Arcade banner present ({hits[0]})"}]


def check_agent_card_signature() -> list:
    """Verify every served agent card against the published key."""
    from lib.agent_card_verify import CardVerificationError, verify_agent_card

    headers = {"User-Agent": "MolTrust-Watchdog/1.0"}
    try:
        r = httpx.get(REGISTRY_KEY_URL, timeout=20.0, headers=headers)
        r.raise_for_status()
        jwk = r.json()
    except Exception as e:
        return [{"ok": False, "surface": "registry-key",
                 "detail": f"published key unreachable: {type(e).__name__}"}]

    results = []
    for name, url in CARD_SURFACES:
        try:
            r = httpx.get(url, timeout=20.0, headers=headers)
            r.raise_for_status()
            card = r.json()
        except Exception as e:
            results.append({"ok": False, "surface": name,
                            "detail": f"card unreachable: {type(e).__name__}"})
            continue
        try:
            header = verify_agent_card(card, jwk)
        except CardVerificationError as e:
            results.append({"ok": False, "surface": name, "detail": str(e)})
            continue
        results.append({"ok": True, "surface": name,
                        "detail": f"signature verifies, kid {header.get('kid')}"})
    return results


# Surfaces this run could not read. Reset per run, reported at the end: a run
# that could not look at six of its surfaces is not a healthy run, and "All
# agents healthy" was the line it printed.
_BLIND: list[str] = []


def _blind(label: str, exc: BaseException) -> None:
    _BLIND.append(f"{label} ({type(exc).__name__})")


def _mark(r: dict) -> str:
    """One glyph per verdict, and a third one for "I could not look".

    A check that could not reach its source answers ok, because it has nothing
    else to answer with — and then reads as a pass in the log. Those verdicts
    carry "unverifiable" since 2026-10-03 and show as ❔. They raise no alert,
    which is the point: unreachable third-party indices are not our outage. But
    they no longer count as evidence either, and the run says how many there
    were. scripts/check_green_on_unreadable.py keeps them declared.
    """
    if r.get("unverifiable"):
        _BLIND.append(str(r.get("surface") or r.get("check") or "?"))
        return "❔"
    return "✅" if r.get("ok") else "❌"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def run():
    now = _now()
    _BLIND.clear()
    log.info(f"Watchdog run at {now.strftime('%Y-%m-%d %H:%M UTC')}")

    # Every check reports here, red or green; debounce() decides what of it is
    # news. Events are news by nature (a listing going live, the breaker
    # tripping) and carry their own once-only logic, so they bypass it.
    observations = []
    events = []

    def observe(key: str, r: dict, line: str) -> None:
        # Not measured is not a verdict: it neither raises nor clears a key.
        if r.get("unverifiable"):
            return
        observations.append({"key": key, "ok": bool(r.get("ok")),
                             "line": line if not r.get("ok") else r.get("detail", "")})

    for agent in AGENTS:
        result = check_heartbeat(agent, now)
        status = _mark(result)
        log.info(f"  {status} {agent['name']}: {result['detail']}")
        observe(agent["name"], result, f"❌ <b>{agent['name']}</b>: {result['detail']}")

    # CONFORMANCE.md drift check
    drift = check_conformance_drift()
    status = _mark(drift)
    log.info(f"  {status} CONFORMANCE Drift: {drift['detail']}")
    observe("CONFORMANCE Drift", drift, f"❌ <b>CONFORMANCE Drift</b>: {drift['detail']}")

    # Discovery-surface reconciliation (MCP↔Smithery, Agent-Card)
    for r in check_discovery_drift(now):
        status = _mark(r)
        log.info(f"  {status} Discovery/{r['surface']}: {r['detail']}")
        observe(f"Discovery/{r['surface']}", r,
                f"❌ <b>Discovery/{r['surface']}</b>: {r['detail']}")

    # Platform agent id on the machine-readable surfaces
    for r in check_platform_id_drift():
        status = _mark(r)
        log.info(f"  {status} PlatformId/{r['surface']}: {r['detail']}")
        observe(f"PlatformId/{r['surface']}", r,
                f"❌ <b>PlatformId</b> {r['surface']}: {r['detail']}")

    # Weekly: our own published proof, recomputed the way a stranger would.
    if _is_weekly_slot(now, PROOF_CHECK_WEEKDAY):
        pr = check_anchor_proof_replay()
        status = _mark(pr)
        log.info(f"  {status} AnchorProof: {pr['detail']}")
        observe("AnchorProof", pr, f"❌ <b>AnchorProof</b>: {pr['detail']}")

    # Hourly: would the Bazaar crawler still accept our paid endpoints?
    # Coinbase's validator is the only authority on that — its rules are not
    # published in full, and our manifest has already looked right to us while
    # being wrong in ways only the crawler could see.
    try:
        from scripts.x402_validator_check import run_checks as x402_validator_checks

        for vr in x402_validator_checks():
            status = _mark(vr)
            log.info(f"  {status} x402Validator/{vr['name']}: {vr['detail']}")
            observe(f"x402Validator/{vr['name']}", vr,
                    f"❌ <b>x402Validator</b> {vr['name']}: {vr['detail']}")
            if vr["ok"] and vr.get("changed"):
                # A state change that is not a failure is still news — this is
                # how the Bazaar listing going live gets reported.
                events.append(f"ℹ️ <b>x402Validator</b> {vr['name']}: {vr['detail']}")
    except Exception as e:
        log.warning(f"  ❔ x402Validator: check did not run ({type(e).__name__})")

    # Weekly: the three things Smithery verification rests on.
    if _is_weekly_slot(now, SMITHERY_CHECK_WEEKDAY):
        for sr in check_smithery_verification():
            status = _mark(sr)
            log.info(f"  {status} {sr['surface']}: {sr['detail']}")
            observe(sr["surface"], sr, f"❌ <b>{sr['surface']}</b>: {sr['detail']}")

    # Weekly: does the MCP registry carry the version PyPI carries?
    if _is_weekly_slot(now, REGISTRY_CHECK_WEEKDAY):
        rr = check_registry_matches_pypi()
        status = _mark(rr)
        log.info(f"  {status} {rr['surface']}: {rr['detail']}")
        observe(rr["surface"], rr, f"❌ <b>{rr['surface']}</b>: {rr['detail']}")

    # Weekly: does the origin still tell withheld apart from not-found?
    if _is_weekly_slot(now, WITHHELD_CHECK_WEEKDAY):
        for wr in check_withheld_rendering():
            status = _mark(wr)
            log.info(f"  {status} {wr['surface']}: {wr['detail']}")
            observe(wr["surface"], wr, f"❌ <b>{wr['surface']}</b>: {wr['detail']}")

    # Weekly: the published card, verified the way a stranger would.
    if _is_weekly_slot(now, CARD_CHECK_WEEKDAY):
        for cr in check_agent_card_signature():
            status = _mark(cr)
            log.info(f"  {status} CardSignature/{cr['surface']}: {cr['detail']}")
            observe(f"CardSignature/{cr['surface']}", cr,
                    f"❌ <b>CardSignature</b> {cr['surface']}: {cr['detail']}")

    # Every run, not weekly: an exhausted X budget stops the digest, the proof
    # post, the radar and every measurement at once, and on 2026-09-25 it did
    # so for 44 hours without anyone being told.
    try:
        # Imported here, not at module scope. x_post pulls in requests_oauthlib,
        # which the CI unit job does not install, and a top-level import made
        # tests/test_watchdog_weekly_checks.py fail to collect — the watchdog
        # stopped being testable because of an optional check inside it.
        from agents import x_post
        xb = x_budget.check(x_post.get_auth(), now)
        status = _mark(xb)
        log.info(f"  {status} {xb['surface']}: {xb['detail']}")
        observe(xb["surface"], xb, f"❌ <b>{xb['surface']}</b>: {xb['detail']}")

        # What today has cost so far, counted from the meter with the same
        # per-UTC-day deduplication X applies.
        xs = x_meter.check()
        status = _mark(xs)
        log.info(f"  {status} {xs['surface']}: {xs['detail']}")
        observe(xs["surface"], xs, f"❌ <b>{xs['surface']}</b>: {xs['detail']} "
                                   f"(Alarm ab ${x_meter.DAILY_ALARM_USD:.2f}/Tag)")
        # Reporting is not stopping. Past the breaker, reads are switched off
        # for the rest of the UTC day; posting is untouched.
        if xs.get("break") and x_meter.trip_breaker(xs["spend"]["day"],
                                                    xs["spend"]["usd"]):
            events.append(
                f"🛑 <b>XSpend</b>: Lesen pausiert bis 00:00 UTC — "
                f"${xs['spend']['usd']:.2f} heute, Breaker "
                f"${x_meter.DAILY_BREAK_USD:.2f}. Digest, Proof und "
                f"Syndication posten weiter.")
    except Exception as e:
        log.warning(f"  ❔ XBudget: check did not run ({type(e).__name__})")
        _blind("XBudget", e)

    # Every run: what Moltbook shows of our scheduled writers. The heartbeat
    # above only proves the Ambassador ran; this proves something appeared.
    try:
        for mw in check_moltbook_writers(now):
            log.info(f"  {_mark(mw)} {mw['surface']}: {mw['detail']}")
            observe(mw["surface"], mw, f"🔇 <b>{mw['surface']}</b>: {mw['detail']}")
    except Exception as e:
        log.warning(f"  ❔ Moltbook: check did not run ({type(e).__name__})")
        _blind("Moltbook", e)

    # Every run: has a draft reached Telegram within the threshold the radar's
    # own schedule gives, and if not, which component went quiet. This is the one check that fires on
    # silence rather than on an error, so it is the only one that catches a
    # source that answers "nothing" instead of failing.
    try:
        dm = check_radar_deadman(now)
        log.info(f"  {'✅' if dm['ok'] else '❌'} {dm['surface']}: {dm['detail']}")
        observe(dm["surface"], dm, f"🔇 <b>{dm['surface']}</b>: {dm['detail']}")
    except Exception as e:
        log.warning(f"  ❔ ReplyRadar: deadman did not run ({type(e).__name__})")
        _blind("ReplyRadar/deadman", e)

    # GitHub Actions runs the selftest on this machine at :17. Measured over the
    # first nine due ticks on 2026-10-03: two fired, 22 %, and the second of
    # those started at 19:50 for a 19:17 tick — 33 minutes late. A schedule that
    # drops 78 % of its runs and shifts the rest carries no timing guarantee, so
    # an expectation built on it measures GitHub's queue and not this machine.
    #
    # It is therefore logged and reported, never alerted. The deadman that does
    # need a timing guarantee has an external receiver (see ping_healthcheck),
    # which is a service whose whole job is being on time.
    try:
        from agents import supervision
        sv = supervision.check_supervisor(now, supervision.load_expectations())
        log.info(f"  {'✅' if sv['light'] == 'green' else 'ℹ️'} "
                 f"{sv['check']}: {sv['detail']}")
    except Exception as e:
        log.warning(f"  ❔ Supervisor: check did not run ({type(e).__name__})")
        _blind("Supervisor", e)

    blind = sorted(set(_BLIND))
    if blind:
        log.info(f"  ❔ {len(blind)} Oberflächen nicht lesbar: {', '.join(blind)}")

    state = load_alert_state()
    lines, new_state = debounce(observations, state, now)
    red = sorted(o["key"] for o in observations if not o["ok"])
    if red and not lines:
        log.info(f"  (rot, schon gemeldet, keine Erinnerung faellig: {', '.join(red)})")
    lines += events

    if lines:
        alarm = any(not l.startswith(("✅", "ℹ️")) for l in lines)
        msg = ("🐕 <b>Watchdog Alert</b>\n\n" if alarm else "🐕 <b>Watchdog</b>\n\n") + "\n".join(lines)
        if blind:
            msg += (f"\n\n❔ nicht gemessen ({len(blind)}): "
                    f"{', '.join(blind)}")
        log.warning(f"Sending {len(lines)} line(s)")
        if send_telegram(msg):
            save_alert_state(new_state)
        else:
            # Kept as it was, so the next run tries the same lines again.
            log.warning("  message not delivered, alert state unchanged")
    else:
        save_alert_state(new_state)
        if blind:
            # Not an alert and not a clean bill either. Saying "all healthy"
            # after failing to read six surfaces is how a watchdog lies without
            # a bug.
            log.info(f"No alerts — but {len(blind)} of the surfaces were not "
                     f"measured, so this is not a clean run")
        elif not red:
            log.info("All agents healthy")

    # Last thing in the run, and only here: reaching this line is what the
    # external check is being told about. An early return or an exception
    # leaves the ping unsent, which is the point.
    log.info(f"  healthcheck ping: {ping_healthcheck(True)}")
    stamp_heartbeat(now)


def main(argv: list) -> int:
    if argv[1:] == ["--thresholds"]:
        # For c-watchdog-threshold-fits-cron: the count on stdout, which is
        # the value the invariant reads, and the findings on stderr.
        import subprocess
        tab = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                             check=True).stdout
        found = threshold_findings(tab)
        for f in found:
            print(f, file=sys.stderr)
        print(len(found))
        return 0
    try:
        run()
    except BaseException:
        # /fail rather than silence: the receiver would alarm after ninety
        # minutes anyway, and ninety minutes is a long time to not know.
        log.error(f"  healthcheck fail ping: {ping_healthcheck(False)}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
