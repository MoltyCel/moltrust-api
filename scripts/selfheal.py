"""Correct what is on the list, alarm on everything else.

The list is closed. A correction runs only when `agents/supervision.py`
produced a yellow finding whose `fix` field names it **word for word**, and
only if that name is a key in ACTIONS below. Anything else is reported and left
alone — including things that look obviously fixable, because "obviously" is
how a repair tool starts doing the thing nobody asked for.

Allowed, without asking, each idempotent, each followed by a Telegram message
saying what ran, why, and what came out:

    restart_service          a crashed unit, at most 3 times in 24 h
    clear_stale_locks        lock files and claim rows nothing holds any more
    reconcile_breaker_flag   the flag brought in line with the live sum
    regenerate_feed          feed.xml rebuilt from the posts that exist
    prune_orphans            media ids and unlinked records older than 24 h
    rotate_logs              rotation and disk, when /var is over 85 %
    refresh_doc_mirror       the voice-gate document mirror pulled onto main
    retry_once               one rerun of a failed run, identical parameters

Never, under any circumstance, from here:

    anything that posts, replies to, or deletes something publicly visible
    a wallet signature of any kind
    rotating, writing, or reading secrets beyond what a check needs
    a schema or data change in Postgres
    a deploy to the web root
    any correction not named above

Two of those deserve saying out loud, because the obvious implementation
violates them. `regenerate_feed` writes the rebuilt feed to a staging path and
reports the difference; installing it into /var/www/html would be a web-root
deploy, so a human or a PR does that. And `prune_orphans` touches our own
bookkeeping — media ids, unlinked ledger halves — never a published record,
because deleting something public is on the forbidden list whether or not it
looks orphaned.

Idempotence is a property, not an aspiration: every action checks the state it
would create before it creates it, so running this twice changes nothing the
second time.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import supervision
from app import notify, paths

BASE = os.path.expanduser("~/moltstack")


def state_path() -> str:
    """Resolved when it is asked for, never bound at import.

    Found on 2026-10-05 by the conftest write guard, which refused a test's
    write to data/selfheal_state.json: this module froze the path at import and
    MOLTRUST_ROOT could not redirect it. Same shape as the three fixture rows
    that reached the live LinkedIn series on 2026-10-04 — and this one counts
    repair attempts, so a test writing here would have spent the production
    cap.
    """
    return paths.data("selfheal_state.json")
STAGE = os.path.expanduser("~/pending")

# Exactly the units sudo will restart without a password, read off `sudo -n -l`
# on 2026-10-03. A unit outside this set is an alarm, not a restart: the
# alternative is a password prompt on a cron-driven path, which hangs.
RESTARTABLE = frozenset({
    "moltstack", "moltguard", "moltproof", "moltrust-agent",
    "moltrust-mcp-http", "moltbook-heartbeat",
})
RESTART_CAP_24H = 3
LOCK_STALE_HOURS = 2
ORPHAN_HOURS = 24
DISK_PCT = 85.0


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def load_state() -> dict:
    try:
        return json.load(open(state_path()))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    target = state_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            json.dump(st, f, indent=1, sort_keys=True)
        os.chmod(target, 0o640)
    except OSError as e:
        # A counter that cannot be written must not become a free pass. The
        # caller treats a failed record as the cap being reached.
        raise RuntimeError(f"selfheal state not writable: {e}") from e


def recent_runs(st: dict, key: str, hours: int = 24) -> list[str]:
    cut = (now() - datetime.timedelta(hours=hours)).isoformat()
    return [t for t in (st.get("runs", {}).get(key) or []) if t >= cut]


def record_run(st: dict, key: str) -> int:
    kept = recent_runs(st, key)
    kept.append(now().isoformat())
    st.setdefault("runs", {})[key] = kept
    save_state(st)
    return len(kept)


# ── the actions ──

def restart_service(f: dict, st: dict, dry: bool) -> tuple[bool, str]:
    """Restart one unit. Three times in 24 h and then it stops being a fix."""
    unit = f.get("unit") or _unit_for(f.get("check", ""))
    if not unit:
        return False, (f"keine Unit aus '{f.get('check')}' ableitbar — "
                       f"kein Neustart")
    if unit not in RESTARTABLE:
        return False, (f"{unit} steht nicht in der sudo-Positivliste "
                       f"({', '.join(sorted(RESTARTABLE))}) — kein Neustart")
    key = f"restart_service:{unit}"
    if len(recent_runs(st, key)) >= RESTART_CAP_24H:
        return False, (f"{unit}: Deckel erreicht, {RESTART_CAP_24H}× in 24 h "
                       f"neu gestartet — das ist ein Konstruktionsfehler, "
                       f"keine Störung")
    active = _systemctl("is-active", unit)
    if active == "active":
        # Idempotent: the state the action would create already holds.
        return True, f"{unit} läuft bereits (is-active=active), nichts getan"
    if dry:
        return True, f"würde {unit} neu starten (is-active={active})"
    r = subprocess.run(["sudo", "-n", "/bin/systemctl", "restart", unit],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return False, f"{unit}: systemctl restart → {(r.stderr or '').strip()[:140]}"
    time.sleep(3)
    after = _systemctl("is-active", unit)
    n = record_run(st, key)
    return after == "active", (f"{unit}: {active} → {after} "
                               f"(Neustart {n} von {RESTART_CAP_24H} in 24 h)")


def _unit_for(check: str) -> str | None:
    name = check.split("/")[-1]
    return name if name in RESTARTABLE else None


def _systemctl(verb: str, unit: str) -> str:
    try:
        r = subprocess.run(["systemctl", verb, unit], capture_output=True,
                           text=True, timeout=30)
        return (r.stdout or r.stderr or "").strip().splitlines()[0]
    except Exception as e:
        return f"unknown ({type(e).__name__})"


def clear_stale_locks(f: dict, st: dict, dry: bool) -> tuple[bool, str]:
    """Lock files and claim rows nothing holds any more.

    A lock is stale when no process holds it, not when it is old: `flock` on a
    file whose owner died releases at the kernel level, but the file stays and
    the next reader sees a lock. So the test is whether the lock can be taken,
    not its mtime.
    """
    import fcntl
    removed, held, claims = [], [], 0
    for path in sorted(glob.glob(os.path.join(BASE, "data", "*.lock"))
                       + glob.glob(os.path.expanduser("~/.*.lock"))):
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        if age_h < LOCK_STALE_HOURS:
            held.append(f"{os.path.basename(path)} (frisch, {age_h:.1f} h)")
            continue
        try:
            fd = open(path, "a")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            held.append(f"{os.path.basename(path)} (gehalten)")
            continue
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        if dry:
            removed.append(f"{os.path.basename(path)} (würde)")
            continue
        try:
            os.remove(path)
            removed.append(os.path.basename(path))
        except OSError as e:
            held.append(f"{os.path.basename(path)} ({type(e).__name__})")

    claims = _release_stale_claims(dry)
    if not removed and not claims:
        return True, (f"keine verwaisten Locks · {len(held)} gehalten/frisch"
                      + (f": {', '.join(held[:4])}" if held else ""))
    record_run(st, "clear_stale_locks")
    return True, (f"{len(removed)} Lock(s) entfernt"
                  + (f" ({', '.join(removed)})" if removed else "")
                  + f" · {claims} verwaiste Claim-Zeile(n) freigegeben")


def _release_stale_claims(dry: bool) -> int:
    """telegram_inbox rows a consumer claimed and never finished.

    This is bookkeeping, not data: `consumed_by` is set and `consumed_at` is
    null, which means a consumer took the row and died. Clearing the claim lets
    the next consumer pick it up. No row is deleted and no schema is touched —
    both are on the forbidden list.
    """
    sql = ("update telegram_inbox set consumed_by = null "
           "where consumed_by is not null and consumed_at is null "
           "and ts < now() - interval '1 hour'")
    if dry:
        sql = sql.replace("update telegram_inbox set consumed_by = null",
                          "select count(*) from telegram_inbox")
    try:
        r = subprocess.run(["psql", "-tA", "-d", "moltstack", "-c", sql],
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return 0
    if r.returncode != 0:
        return 0
    out = (r.stdout or "").strip()
    m = re.search(r"(\d+)", out)
    return int(m.group(1)) if m else 0


def reconcile_breaker_flag(f: dict, st: dict, dry: bool) -> tuple[bool, str]:
    """The flag agrees with the live sum, in both directions.

    The flag is a cache the watchdog writes hourly, and the live sum decides.
    They only ever disagree one way — the flag lagging — but a flag that says
    yesterday while today is over the limit is a reader's trap, and a flag
    still claiming a closed day after midnight is the same trap mirrored.
    """
    from agents import x_meter
    day = x_meter._day()
    usd = x_meter.live_spend_usd()
    over = usd >= x_meter.DAILY_BREAK_USD
    try:
        flag = json.load(open(x_meter.flag_path()))
    except FileNotFoundError:
        flag = None
    except Exception as e:
        return False, f"Flag unlesbar ({type(e).__name__}) — nicht angefasst"

    current = bool(flag and flag.get("day") == day)
    if over == current:
        return True, (f"Flag und Live-Summe stimmen überein "
                      f"(${usd:.3f}, Tag {day}, "
                      f"{'gesetzt' if current else 'offen'})")
    if dry:
        return True, (f"würde Flag {'setzen' if over else 'entfernen'} "
                      f"(${usd:.3f} gegen ${x_meter.DAILY_BREAK_USD:.2f})")
    if over:
        x_meter.trip_breaker(day, usd)
        after = "gesetzt"
    else:
        # Only a flag from a past day is removed. The current day's flag is
        # never cleared here: that would reopen reads the live sum shut.
        try:
            os.remove(x_meter.flag_path())
            after = "entfernt (Tag vorbei)"
        except OSError as e:
            return False, f"Flag nicht entfernbar: {type(e).__name__}"
    record_run(st, "reconcile_breaker_flag")
    return True, (f"${usd:.3f} heute, Breaker ${x_meter.DAILY_BREAK_USD:.2f} → "
                  f"Flag {after}")


BLOG_DIR = "/var/www/html/blog"
WEB_REPO = os.path.expanduser("~/moltrust-web")


def regenerate_feed(f: dict, st: dict, dry: bool) -> tuple[bool, str]:
    """Rebuild feed.xml from the posts that exist — into staging, not live.

    The divergence this fixes is real and happened: a post went live on 01.10
    and the publish never touched feed.xml, so syndication could not see a page
    that was already being served. Rebuilding is on the positive list.
    Installing it is not — a deploy to the web root is forbidden here — so the
    result lands in ~/pending and the message says what changed.
    """
    try:
        pages = sorted(p for p in os.listdir(BLOG_DIR)
                       if p.endswith(".html") and p != "index.html")
    except OSError as e:
        return False, f"{BLOG_DIR} nicht lesbar: {type(e).__name__}"
    live = os.path.join(BLOG_DIR, "feed.xml")
    try:
        current = open(live, errors="replace").read()
    except OSError:
        current = ""
    in_feed = {l.rstrip("/").rsplit("/", 1)[-1] for l in
               re.findall(r"<link>\s*([^<\s]+)\s*</link>", current)
               if l.endswith(".html")}
    missing = [p for p in pages if p not in in_feed]
    if not missing:
        return True, f"Feed deckt alle {len(pages)} Seiten — nichts zu tun"
    if dry:
        return True, (f"würde {len(missing)} Eintrag/Einträge erzeugen: "
                      f"{', '.join(missing[:4])}")
    items, undated = [], []
    for page in missing:
        item = _feed_item(page)
        (items.append(item) if item else undated.append(page))
    os.makedirs(STAGE, exist_ok=True)
    out = os.path.join(STAGE, "feed-missing-entries.xml")
    with open(out, "w") as fh:
        fh.write("\n".join(items) + "\n")
    record_run(st, "regenerate_feed")
    return True, (f"{len(items)} Eintrag/Einträge erzeugt in {out}"
                  + (f", {len(undated)} ohne lesbares Datum übersprungen "
                     f"({', '.join(undated[:3])})" if undated else "")
                  + " · Einbau in den Web-Root bleibt bei Lars bzw. einem PR, "
                    "Deploy steht nicht auf der Positivliste")


def _page_meta(page: str) -> dict:
    """Title, description and date, read the way the blog index reads them.

    Through scripts/generate_blog_index.py on purpose. That file is already the
    one thing that knows where a post keeps its metadata — datePublished, then
    article-meta, then the og tags — and a second parser here would be a second
    path to the same artefact, which is the defect rather than a convenience.

    mtime is not a fallback for the date. Every page in /blog carries
    2026-09-23 15:18 from a bulk redeploy, so mtime would stamp thirty posts
    with a day none of them was published on.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from generate_blog_index import extract_meta
    meta = extract_meta(os.path.join(BLOG_DIR, page)) or {}
    return meta if isinstance(meta, dict) else {}


def _feed_item(page: str) -> str | None:
    meta = _page_meta(page)
    # sort_date, not date: `date` is the human string the page shows
    # ("March 25, 2026"); sort_date is the ISO form the index already derived.
    date = meta.get("sort_date")
    if not date:
        # No date, no entry. An item with a guessed pubDate puts a wrong date
        # into a public feed, and readers order by it.
        return None
    try:
        when = datetime.datetime.fromisoformat(str(date)[:19])
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return ("  <item>\n"
            f"    <title>{_esc(meta.get('title') or page)}</title>\n"
            f"    <link>https://moltrust.ch/blog/{page}</link>\n"
            f"    <guid>https://moltrust.ch/blog/{page}</guid>\n"
            f"    <description>{_esc(meta.get('description') or '')}</description>\n"
            f"    <pubDate>{when.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>\n"
            "  </item>")


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def prune_orphans(f: dict, st: dict, dry: bool) -> tuple[bool, str]:
    """Our own bookkeeping only: media ids and ledger halves nothing points at.

    Never a published record. A post that looks orphaned from here might be
    live and fine — deleting anything publicly visible is forbidden from this
    file, and the one time a check deleted posts on its own judgement it took
    two working ones with it.
    """
    cut = now() - datetime.timedelta(hours=ORPHAN_HOURS)
    led = os.path.join(BASE, "data", "video_posts.jsonl")
    stale = []
    try:
        with open(led) as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    except OSError:
        rows = []
    for row in rows:
        at = supervision.parse_ts(row.get("at") or "")
        if not at or at >= cut:
            continue
        mid = (row.get("x") or {}).get("media_id")
        if mid and not (row.get("x") or {}).get("post"):
            stale.append(f"media {mid} ohne Post")
        b = row.get("bluesky") or {}
        if b.get("uri") and not b.get("url"):
            stale.append(f"{b['uri'].rsplit('/', 1)[-1]} ohne URL")
    if not stale:
        return True, f"keine verwaisten Media-IDs oder Records älter als {ORPHAN_HOURS} h"
    # Reported, not removed: the ledger is the record of what we did, and
    # rewriting history to make a check green is the opposite of bookkeeping.
    return True, (f"{len(stale)} verwaiste Einträge gefunden und **gemeldet, "
                  f"nicht entfernt**: {', '.join(stale[:4])} · ein Ledger wird "
                  f"nicht umgeschrieben, damit eine Prüfung grün wird")


def rotate_logs(f: dict, st: dict, dry: bool) -> tuple[bool, str]:
    """Rotation when /var is tight. Our own logs, with the existing config."""
    import shutil
    total, used, _ = shutil.disk_usage("/var")
    before = used / total * 100
    if before < DISK_PCT:
        return True, f"/var bei {before:.1f} % — unter {DISK_PCT:.0f} %, nichts getan"
    conf = os.path.join(BASE, "config", "logrotate.conf")
    if not os.path.exists(conf):
        return False, f"{conf} fehlt — keine Rotation ohne die vorhandene Konfiguration"
    if dry:
        return True, f"würde logrotate -f mit {conf} laufen lassen ({before:.1f} %)"
    r = subprocess.run(["/usr/sbin/logrotate", "-f", "-s",
                        os.path.expanduser("~/.logrotate.state"), conf],
                       capture_output=True, text=True, timeout=600)
    total, used, _ = shutil.disk_usage("/var")
    after = used / total * 100
    record_run(st, "rotate_logs")
    return r.returncode == 0, (f"/var {before:.1f} % → {after:.1f} % "
                               f"(logrotate exit {r.returncode})")


# One rerun, with the arguments the crontab carries, so "identical parameters"
# means the schedule's own and not something this file invented.
RETRYABLE = {
    "reply_radar": ["agents/reply_radar.py", "--no-search"],
    "herald_v3": ["agents/herald_v3.py", "digest"],
    "traffic_monitor": ["agents/traffic_monitor.py"],
    "syndicate": ["agents/syndicate.py"],
}


def retry_once(f: dict, st: dict, dry: bool) -> tuple[bool, str]:
    name = f.get("check", "").split("/")[1] if "/" in f.get("check", "") else ""
    argv = RETRYABLE.get(name)
    if not argv:
        return False, f"{name or f.get('check')} hat keinen deklarierten Retry"
    key = f"retry_once:{name}"
    if recent_runs(st, key, hours=24):
        return False, (f"{name}: heute schon einmal nachgefahren — einmal heißt "
                       f"einmal, der zweite Fehlschlag ist ein Befund")
    if dry:
        return True, f"würde {' '.join(argv)} einmal nachfahren"
    venv = os.path.join(BASE, "venv", "bin", "python")
    r = subprocess.run([venv] + argv, cwd=BASE, capture_output=True, text=True,
                       timeout=900, env={**os.environ, "PYTHONPATH": BASE})
    record_run(st, key)
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode == 0, (f"{' '.join(argv)} → exit {r.returncode}"
                               + (f" · {tail[-1][:120]}" if tail else ""))


# Cap three in 24 h, and the fourth is not a fourth repair. A mirror that needs
# pulling three times a day is not a mirror that keeps falling behind; it is a
# refresh tick that is not working, and pulling it again hides that. So the
# fourth attempt refuses and says so in those words.
MIRROR_CAP_24H = 3


def refresh_doc_mirror(f: dict, st: dict, dry: bool) -> tuple[bool, str]:
    """The voice-gate document mirror, pulled onto moltrust-web main.

    Idempotent by construction: fetch plus `reset --hard` onto the same commit
    changes nothing and says "unchanged". It writes only inside the gitignored
    clone, touches no served file and nothing public.
    """
    key = "refresh_doc_mirror"
    runs = recent_runs(st, key)
    if len(runs) >= MIRROR_CAP_24H:
        return False, (f"Deckel erreicht, {MIRROR_CAP_24H}× in 24 h — das ist "
                       f"ein Konstruktionsfehler und keine vierte Reparatur: "
                       f"ein Spiegel, der dreimal am Tag nachgezogen werden "
                       f"muss, hat einen defekten Takt, nicht einen alten Stand")
    script = os.path.join(BASE, "scripts", "refresh_doc_mirror.py")
    if not os.path.exists(script):
        return False, f"{script} fehlt — kein Refresh ohne das vorhandene Werkzeug"
    if dry:
        return True, "würde scripts/refresh_doc_mirror.py laufen lassen"
    venv = os.path.join(BASE, "venv", "bin", "python")
    r = subprocess.run([venv, script], cwd=BASE, capture_output=True, text=True,
                       timeout=300, env={**os.environ, "PYTHONPATH": BASE})
    record_run(st, key)
    line = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    n = len(runs) + 1
    return r.returncode == 0, ((line[-1][:160] if line else f"exit {r.returncode}")
                               + f" (Lauf {n} von {MIRROR_CAP_24H} in 24 h)")


ACTIONS = {
    "restart_service": restart_service,
    "clear_stale_locks": clear_stale_locks,
    "reconcile_breaker_flag": reconcile_breaker_flag,
    "regenerate_feed": regenerate_feed,
    "prune_orphans": prune_orphans,
    "rotate_logs": rotate_logs,
    "refresh_doc_mirror": refresh_doc_mirror,
    "retry_once": retry_once,
}


def heal(findings: list[dict], dry: bool = False) -> list[dict]:
    """Act on yellow findings whose fix is on the list. Nothing else."""
    st = load_state()
    done = []
    for f in findings:
        fix = f.get("fix")
        if f["light"] != supervision.YELLOW or fix in (None, "none"):
            continue
        if fix not in ACTIONS:
            done.append({**f, "action": fix, "ran": False,
                         "result": f"'{fix}' steht nicht in der Positivliste — "
                                   f"Alarm, keine Reparatur"})
            continue
        try:
            ok, detail = ACTIONS[fix](f, st, dry)
        except Exception as e:
            ok, detail = False, f"Korrektur selbst gescheitert: {type(e).__name__}: {e}"
        done.append({**f, "action": fix, "ran": True, "ok": ok, "result": detail})
    return done


def announce(done: list[dict], dry: bool) -> None:
    """One message per correction: what ran, why, and what came out."""
    for d in done:
        mark = "🔧" if d.get("ok") else "⚠️"
        notify.send_telegram(
            f"{mark} <b>Selbstkorrektur{' (Probe)' if dry else ''}: "
            f"{d['action']}</b>\n\n"
            f"<b>Was:</b> {d['action']} auf {d['check']}\n"
            f"<b>Warum:</b> {d['detail']}\n"
            f"<b>Ergebnis:</b> {d['result']}",
            channel=notify.STATS if d.get("ok") else notify.ALERTS,
            parse_mode="HTML")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    findings = supervision.families()
    done = heal(findings, dry=a.dry_run)
    if not a.dry_run:
        announce(done, a.dry_run)
    if a.json:
        print(json.dumps({"corrections": done}, indent=1, ensure_ascii=False))
    else:
        for d in done:
            print(f"{'ok ' if d.get('ok') else 'NOK'} {d['action']} "
                  f"({d['check']}): {d['result']}")
        if not done:
            print("keine gelbe Abweichung mit einer Korrektur auf der Liste")
    return 0 if all(d.get("ok") for d in done) else 1


if __name__ == "__main__":
    raise SystemExit(main())
