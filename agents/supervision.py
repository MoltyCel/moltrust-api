"""Does this machine still do what it says it does. Read-only.

Three questions, in this order:

1. **Is every pipeline meeting its declaration?** `config/expectations.yaml`
   says what each one produces, how long it may be quiet, and which quiet
   states are legitimate. Silence with a declared reason is green. Silence
   without one is red — unknown, reported, not interpreted.
2. **Does every external dependency actually answer?** One cheap real call
   each. Not a config check and not a cached value: on 02.10 a Bluesky video
   post was reported as verified because a field was present, and the resource
   behind it answered 404. Per the deploy doc §4.1a2, a field that exists is
   not proof — the resource behind it has to resolve.
3. **What is it costing?** Day, month and the per-pipeline band.

Each check ends green, yellow or red:

    green   as declared
    yellow  a deviation this run can name AND the positive list can correct
    red     unknown, or a correction that is not word-for-word on the list

The exit code is the worst light: 0, 1, 2. The supervisor workflow reads that.

**Read-only is a property, not an intention.** Nothing here writes a state
file, a flag, a counter or a heartbeat, and nothing posts. The two X calls are
reads of our own account; they cost $0.010 a day together, deduplicated.
Correction is scripts/selfheal.py, which runs only when this one says yellow
and only from its own positive list.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import yaml

GREEN, YELLOW, RED = "green", "yellow", "red"
RANK = {GREEN: 0, YELLOW: 1, RED: 2}

BASE = os.path.expanduser("~/moltstack")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTATIONS = os.path.join(REPO, "config", "expectations.yaml")
TAIL_LINES = 4000

# A dependency that is slow is a finding, not a reason to hang the supervisor.
TIMEOUT = 20.0


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_ts(raw: str) -> datetime.datetime | None:
    try:
        d = datetime.datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def finding(name: str, light: str, detail: str, **extra) -> dict:
    out = {"check": name, "light": light, "detail": detail}
    out.update(extra)
    return out


def tail(path: str, n: int = TAIL_LINES) -> list[str]:
    with open(path, errors="replace") as f:
        return f.read().splitlines()[-n:]


# ── part 1: every pipeline against its declaration ──

def log_evidence(spec: dict, now: datetime.datetime) -> dict:
    """Last run, last output and the reason for silence, out of the log."""
    path = os.path.join(BASE, spec["log"])
    run_re = re.compile(spec["run_marker"]) if spec.get("run_marker") else None
    out_re = re.compile(spec["output_marker"]) if spec.get("output_marker") else None
    reasons = {k: re.compile(v) for k, v in (spec.get("silence_ok") or {}).items()}
    last_run = last_out = None
    reason = None
    try:
        lines = tail(path)
    except OSError as e:
        return {"error": f"{os.path.basename(path)}: {type(e).__name__}"}
    for line in lines:
        m = re.match(r"^\[?(\d{4}-\d\d-\d\dT[\d:.]+)", line)
        stamp = m.group(1) if m else None
        if run_re and run_re.search(line):
            last_run, reason = stamp or last_run, None
        if out_re and out_re.search(line):
            last_out = stamp or last_out
        for name, rx in reasons.items():
            if rx.search(line):
                reason = name
    return {"last_run": last_run, "last_output": last_out, "reason": reason}


def heartbeat_evidence(spec: dict) -> dict:
    path = os.path.join(BASE, spec["heartbeat"])
    try:
        hb = json.load(open(path))
    except OSError as e:
        return {"error": f"{os.path.basename(path)}: {type(e).__name__}"}
    except json.JSONDecodeError as e:
        return {"error": f"{os.path.basename(path)}: unparseable ({e.msg})"}
    detail = hb.get("detail") or ""
    reason = None
    for name, rx in (spec.get("silence_ok") or {}).items():
        if re.search(rx, detail):
            reason = name
            break
    return {"last_run": hb.get("timestamp"), "last_output": hb.get("timestamp"),
            "status": hb.get("status"), "hb_detail": detail, "reason": reason}


def ledger_evidence(spec: dict, now: datetime.datetime) -> dict:
    """For x_meter: the ledger's own last row is the run."""
    path = os.path.join(BASE, spec["ledger"])
    last = None
    try:
        with open(path) as f:
            for line in f:
                try:
                    at = json.loads(line).get("at")
                except json.JSONDecodeError:
                    continue
                if at and (last is None or at > last):
                    last = at
    except OSError as e:
        return {"error": f"{os.path.basename(path)}: {type(e).__name__}"}
    # The reason a ledger is quiet is never in the ledger. It is in the
    # breaker, so that is where it is read from.
    reason = None
    try:
        from agents import x_meter
        if x_meter.reads_paused(now):
            reason = "breaker_closed"
    except Exception:
        pass
    return {"last_run": last, "last_output": last, "reason": reason}


def output_count(spec: dict, since: datetime.datetime) -> int | None:
    """How many artefacts since `since`. None when the shape cannot count."""
    if spec.get("evidence") == "ledger":
        path = os.path.join(BASE, spec["ledger"])
        n = 0
        try:
            with open(path) as f:
                for line in f:
                    try:
                        at = parse_ts(json.loads(line).get("at"))
                    except json.JSONDecodeError:
                        continue
                    if at and at >= since:
                        n += 1
        except OSError:
            return None
        return n
    if not spec.get("output_marker"):
        return None
    out_re = re.compile(spec["output_marker"])
    try:
        lines = tail(os.path.join(BASE, spec["log"]))
    except OSError:
        return None
    n = 0
    for line in lines:
        m = re.match(r"^\[?(\d{4}-\d\d-\d\dT[\d:.]+)", line)
        at = parse_ts(m.group(1)) if m else None
        if at and at >= since and out_re.search(line):
            n += 1
    return n


def check_pipeline(spec: dict, now: datetime.datetime) -> list[dict]:
    name = spec["name"]
    kind = spec.get("evidence") or "log"
    ev = (heartbeat_evidence(spec) if kind == "heartbeat" else
          ledger_evidence(spec, now) if kind == "ledger" else
          log_evidence(spec, now))
    if ev.get("error"):
        return [finding(f"pipeline/{name}", RED,
                        f"Beleg nicht lesbar — {ev['error']}", fix=None)]

    out: list[dict] = []
    last_run = parse_ts(ev.get("last_run"))
    limit = spec.get("max_silence_minutes")
    quiet_m = (now - last_run).total_seconds() / 60 if last_run else None

    if last_run is None:
        out.append(finding(f"pipeline/{name}", RED,
                           f"kein Lauf im Beleg ({kind}) auffindbar",
                           fix=spec.get("fix")))
    elif limit and quiet_m > limit:
        # A run that is overdue is a finding even with a legitimate reason for
        # producing nothing: the reason explains an empty run, not a missing one.
        out.append(finding(
            f"pipeline/{name}", YELLOW if spec.get("fix") not in (None, "none") else RED,
            f"letzter Lauf vor {quiet_m / 60:.1f} h, erlaubt "
            f"{limit / 60:.1f} h · Grund: {ev.get('reason') or 'keiner deklariert'}",
            fix=spec.get("fix"), quiet_hours=round(quiet_m / 60, 1)))
    else:
        out.append(finding(f"pipeline/{name}", GREEN,
                           f"Lauf vor {quiet_m / 60:.1f} h"
                           + (f" · {ev['status']}" if ev.get("status") else ""),
                           fix=None))

    if ev.get("status") and ev["status"] not in (spec.get("ok_status") or ["ok"]):
        out.append(finding(f"pipeline/{name}/status", RED,
                           f"Heartbeat meldet '{ev['status']}': "
                           f"{ev.get('hb_detail') or '—'}", fix=None))

    for window in spec.get("min_output") or []:
        hours, want = window["window_hours"], window["count"]
        got = output_count(spec, now - datetime.timedelta(hours=hours))
        if got is None:
            out.append(finding(f"pipeline/{name}/output", YELLOW,
                               f"Ausgabe über {hours} h nicht zählbar "
                               f"(kein output_marker)", fix=None))
        elif got >= want:
            out.append(finding(f"pipeline/{name}/output", GREEN,
                               f"{got} Artefakte in {hours} h (min {want})",
                               fix=None))
        elif ev.get("reason"):
            # This is the whole point of the register. Quiet is fine when the
            # run said why, in a word that was declared in advance.
            out.append(finding(f"pipeline/{name}/output", GREEN,
                               f"{got} in {hours} h (min {want}), "
                               f"erklärt: {ev['reason']}", fix=None))
        else:
            out.append(finding(f"pipeline/{name}/output", RED,
                               f"{got} Artefakte in {hours} h, erwartet "
                               f"{want}, und kein deklarierter Grund für "
                               f"die Stille", fix=None))

    for src in spec.get("quellen") or []:
        out.append(check_source(name, src, now))
    return [f for f in out if f]


def check_source(pipeline: str, src: dict, now: datetime.datetime) -> dict | None:
    """A dead leg inside a living pipeline — the shape that hid twice."""
    limit = src.get("max_silence_minutes")
    if not limit or not src.get("ledger_source"):
        return None
    path = os.path.join(BASE, "data", "x_meter.jsonl")
    last = None
    try:
        with open(path) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("source") == src["ledger_source"] and row.get("at"):
                    if last is None or row["at"] > last:
                        last = row["at"]
    except OSError:
        return finding(f"source/{pipeline}/{src['name']}", RED,
                       "x_meter-Ledger nicht lesbar", fix=None)
    d = parse_ts(last)
    if d is None:
        return finding(f"source/{pipeline}/{src['name']}", RED,
                       "kein Abruf mit Ergebnis im Ledger", fix=None)
    quiet_m = (now - d).total_seconds() / 60
    light = GREEN if quiet_m <= limit else RED
    return finding(f"source/{pipeline}/{src['name']}", light,
                   f"letzter Abruf mit Ergebnis vor {quiet_m / 60:.1f} h, "
                   f"erlaubt {limit / 60:.1f} h", fix=None)


# ── part 2: every external dependency, with one real call ──
#
# §4.1a2: a field that exists is not proof. Each of these resolves a resource
# and reports what it answered, so "configured" can never pass for "working".

def dep_x() -> dict:
    """Our own account by handle — a profile read, $0.010, deduplicated daily.

    Deliberately not /2/users/me. Through the 44-hour credit outage in
    September that endpoint answered 200 the whole time, so it proves the
    credentials parse and nothing else. A profile read returns a resource and
    is billed like one, which is exactly why it is evidence.
    """
    try:
        from agents import x_post
        auth = x_post.get_auth()
    except Exception as e:
        return finding("dep/x", RED, f"keine X-Credentials: {type(e).__name__}", fix=None)
    if not auth:
        return finding("dep/x", RED, "keine X-Credentials gesetzt", fix=None)
    try:
        r = httpx.get("https://api.twitter.com/2/users/by/username/moltrust",
                      params={"user.fields": "public_metrics"},
                      auth=auth, timeout=TIMEOUT)
    except Exception as e:
        return finding("dep/x", RED, f"{type(e).__name__}: {e}", fix=None)
    if r.status_code == 402:
        return finding("dep/x", RED, "HTTP 402 credits depleted", fix=None)
    if r.status_code != 200:
        return finding("dep/x", RED, f"HTTP {r.status_code}: {r.text[:120]}", fix=None)
    data = (r.json() or {}).get("data") or {}
    followers = (data.get("public_metrics") or {}).get("followers_count")
    if followers is None:
        return finding("dep/x", RED, "200, aber keine public_metrics im Body",
                       fix=None)
    return finding("dep/x", GREEN, f"HTTP 200 · @{data.get('username')} · "
                   f"{followers} Follower", fix=None)


def dep_bluesky() -> dict:
    """getUploadLimits — the one call that would have stopped 02.10."""
    try:
        sys.path.insert(0, os.path.join(REPO, "scripts"))
        import post_video
    except Exception as e:
        return finding("dep/bluesky", RED,
                       f"post_video nicht importierbar: {type(e).__name__}", fix=None)
    sess = post_video.bsky_login()
    if not sess:
        return finding("dep/bluesky", RED, "Login fehlgeschlagen", fix=None)
    ok, why = post_video.bsky_can_upload(sess)
    return finding("dep/bluesky", GREEN if ok else YELLOW,
                   f"canUpload={ok} · {why[:160]}", fix=None)


def dep_telegram() -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return finding("dep/telegram", RED, "TELEGRAM_BOT_TOKEN nicht gesetzt",
                       fix=None)
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=TIMEOUT)
        body = r.json()
    except Exception as e:
        return finding("dep/telegram", RED, f"{type(e).__name__}: {e}", fix=None)
    if r.status_code != 200 or not body.get("ok"):
        # The token itself never goes into the detail, which is why only the
        # status and the description are read out.
        return finding("dep/telegram", RED,
                       f"HTTP {r.status_code}: {str(body.get('description'))[:100]}",
                       fix=None)
    return finding("dep/telegram", GREEN,
                   f"HTTP 200 · @{(body.get('result') or {}).get('username')}",
                   fix=None)


def dep_github() -> dict:
    """Scopes and expiry, both from the response headers of a real call.

    A token that still authenticates but has lost `workflow` cannot dispatch
    the deploy, and one that expires on Friday is a Friday outage nobody
    scheduled. GitHub returns both in headers, so one call answers both.
    """
    token = os.getenv("MOLTYCEL_GH_TOKEN") or os.getenv("GH_TOKEN") or ""
    if not token:
        return finding("dep/github", RED, "kein GitHub-Token gesetzt", fix=None)
    try:
        r = httpx.get("https://api.github.com/user",
                      headers={"Authorization": f"Bearer {token}",
                               "Accept": "application/vnd.github+json"},
                      timeout=TIMEOUT)
    except Exception as e:
        return finding("dep/github", RED, f"{type(e).__name__}: {e}", fix=None)
    if r.status_code != 200:
        return finding("dep/github", RED, f"HTTP {r.status_code}", fix=None)
    scopes = r.headers.get("x-oauth-scopes", "")
    expiry = r.headers.get("github-authentication-token-expiration", "")
    login = (r.json() or {}).get("login")
    detail = f"HTTP 200 · {login} · scopes [{scopes or 'fine-grained'}]"
    light = GREEN
    if expiry:
        exp = parse_ts(expiry.replace(" UTC", "+00:00").replace(" ", "T", 1))
        if exp:
            days = (exp - now_utc()).days
            detail += f" · läuft ab in {days} Tagen ({expiry})"
            if days < 0:
                return finding("dep/github", RED, detail + " — abgelaufen", fix=None)
            if days <= 14:
                light = YELLOW
        else:
            detail += f" · Ablauf '{expiry}' nicht lesbar"
            light = YELLOW
    else:
        detail += " · kein Ablaufdatum im Header"
    return finding("dep/github", light, detail, fix=None)


def dep_rpc() -> dict:
    url = os.getenv("BASE_RPC", "")
    if not url:
        return finding("dep/rpc", RED, "BASE_RPC nicht gesetzt", fix=None)
    try:
        r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1,
                                  "method": "eth_blockNumber", "params": []},
                       timeout=TIMEOUT)
        body = r.json()
    except Exception as e:
        return finding("dep/rpc", RED, f"{type(e).__name__}: {e}", fix=None)
    raw = (body or {}).get("result")
    if r.status_code != 200 or not raw:
        return finding("dep/rpc", RED,
                       f"HTTP {r.status_code}: {str(body)[:120]}", fix=None)
    # A block number that parses and is plausible. A node replaying an old
    # chain answers 200 with a number too, so the number is read, not counted.
    try:
        height = int(raw, 16)
    except (TypeError, ValueError):
        return finding("dep/rpc", RED, f"result nicht hex: {raw!r}", fix=None)
    light = GREEN if height > 50_000_000 else YELLOW
    return finding("dep/rpc", light, f"HTTP 200 · Block {height:,}", fix=None)


def dep_postgres() -> dict:
    try:
        r = subprocess.run(["psql", "-tA", "-d", "moltstack", "-c", "select 1"],
                           capture_output=True, text=True, timeout=TIMEOUT)
    except Exception as e:
        return finding("dep/postgres", RED, f"{type(e).__name__}: {e}", fix=None)
    if r.returncode != 0:
        return finding("dep/postgres", RED,
                       (r.stderr or "").strip().splitlines()[-1][:140] if r.stderr
                       else f"exit {r.returncode}", fix=None)
    if r.stdout.strip() != "1":
        return finding("dep/postgres", RED,
                       f"select 1 ergab {r.stdout.strip()!r}", fix=None)
    return finding("dep/postgres", GREEN, "select 1 → 1", fix=None)


FEED_URL = "https://moltrust.ch/blog/feed.xml"
WEBROOT_BLOG = "/var/www/html/blog"


def dep_feed() -> dict:
    """The feed resolves, and it still matches the directory it describes.

    This is the one that actually broke: one-week-four-announcements.html went
    live on 01.10 and the publish never touched feed.xml, so syndication could
    not see a post that existed. Comparing the feed against the directory is
    the only check that notices — the feed answered 200 the whole time.
    """
    try:
        r = httpx.get(FEED_URL, timeout=TIMEOUT, follow_redirects=True)
    except Exception as e:
        return finding("dep/feed", RED, f"{type(e).__name__}: {e}", fix=None)
    if r.status_code != 200:
        return finding("dep/feed", RED, f"HTTP {r.status_code}", fix=None)
    links = re.findall(r"<link>\s*([^<\s]+)\s*</link>", r.text)
    in_feed = {l.rstrip("/").rsplit("/", 1)[-1] for l in links
               if l.endswith(".html")}
    if not in_feed:
        return finding("dep/feed", RED, "200, aber kein <link> auf eine "
                       ".html-Seite im Feed", fix="regenerate_feed")
    try:
        on_disk = {f for f in os.listdir(WEBROOT_BLOG)
                   if f.endswith(".html") and f != "index.html"}
    except OSError as e:
        return finding("dep/feed", YELLOW,
                       f"HTTP 200, {len(in_feed)} Einträge · Verzeichnis nicht "
                       f"lesbar ({type(e).__name__})", fix=None)
    missing = sorted(on_disk - in_feed)
    if missing:
        return finding("dep/feed", YELLOW,
                       f"HTTP 200, {len(in_feed)} Einträge · {len(missing)} "
                       f"Blogposts fehlen im Feed: {', '.join(missing[:4])}"
                       + (" …" if len(missing) > 4 else ""),
                       fix="regenerate_feed", missing=missing)
    stale = sorted(in_feed - on_disk)
    if stale:
        return finding("dep/feed", YELLOW,
                       f"HTTP 200 · {len(stale)} Feed-Einträge ohne Seite: "
                       f"{', '.join(stale[:4])}", fix="regenerate_feed")
    return finding("dep/feed", GREEN,
                   f"HTTP 200 · {len(in_feed)} Einträge, deckungsgleich mit "
                   f"dem Verzeichnis", fix=None)


DEPENDENCIES = (dep_x, dep_bluesky, dep_telegram, dep_github, dep_rpc,
                dep_postgres, dep_feed)


def check_dependencies() -> list[dict]:
    out = []
    for fn in DEPENDENCIES:
        try:
            out.append(fn())
        except Exception as e:
            # An unknown deviation is red by definition, and a checker that
            # throws is the most unknown of all.
            out.append(finding(f"dep/{fn.__name__[4:]}", RED,
                               f"Prüfung selbst gescheitert: "
                               f"{type(e).__name__}: {e}", fix=None))
    return out
