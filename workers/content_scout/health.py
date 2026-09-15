"""Content-Scout run health: discovery feed freshness, zero-intake alarm, alerts.

Why this exists: from 2026-08-11 to 2026-09-14 the upstream discovery job failed
every day (expired GitHub token, errors swallowed), and the scout reported
"0 lead(s)" for 70 runs. A normal evening run also reports 0, so nobody could
tell a dead upstream from a quiet day. This module makes that state loud:

  * start(): feed freshness check against discovery_health.json + the feed file.
    Stale or missing health -> ALERT (throttled), recovery message when fresh.
  * finish_run(): per-run intake stats in a bounded state file; zero intake in
    every run over a window that must contain a discovery refresh -> ALERT
    (throttled), recovery message when intake resumes.
  * feed_line(): explicit freshness line for the run summary, so a silent
    upstream is visible even when alerts are muted or missed.

All alerts go through telegram.send_message (same bot, same MOLTRUST_NOTIFY
gate as the persist-error alert) with a prefix that cannot be confused with
the normal "🔎 Content-Scout:" summary. Health bookkeeping must never itself
break a run: every file/Telegram operation here is best-effort.
"""
import datetime as dt
import json

from . import config, telegram

ALERT_PREFIX = "🚨 Content-Scout ALERT"
RECOVERY_PREFIX = "✅ Content-Scout RECOVERED"

# Cron start and run duration jitter by seconds to minutes; without slack a
# "once per 24h" re-alert would slip to every other scheduled run (~35h).
ALARM_REALERT_JITTER = dt.timedelta(minutes=30)

ALARM_STALE = "discovery_stale"
ALARM_ZERO_INTAKE = "zero_intake"

_UTC = dt.timezone.utc
_run = {"started_at": None, "recorded": False, "feed": None}


class FatalRunError(RuntimeError):
    """Raised after an alert was sent; the run cannot continue (exit code 2)."""


# --- time + logging helpers ---------------------------------------------------
def utcnow() -> dt.datetime:
    return dt.datetime.now(_UTC)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(_UTC).isoformat(timespec="seconds")


def _fmt(t) -> str:
    return t.astimezone(_UTC).strftime("%Y-%m-%d %H:%M UTC") if t else "never"


def parse_ts(value):
    """ISO timestamp or YYYY-MM-DD date -> aware UTC datetime (date = 00:00 UTC)."""
    if not value:
        return None
    s = str(value).strip()
    try:
        if len(s) == 10:
            d = dt.date.fromisoformat(s)
            return dt.datetime(d.year, d.month, d.day, tzinfo=_UTC)
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=_UTC)).astimezone(_UTC)
    except ValueError:
        return None


def log(msg: str) -> None:
    """Timestamped log line (the cron log has no timestamps of its own)."""
    print(f"{_iso(utcnow())} content_scout: {msg}", flush=True)


def alert(secrets: dict, text: str, label: str = "alert") -> None:
    msg = f"{ALERT_PREFIX}: {text}"
    log(msg)
    try:
        telegram.send_message(secrets, msg, label=label)
    except Exception as e:  # alerting must never itself break the run
        log(f"alert send failed: {type(e).__name__}")


def recovered(secrets: dict, text: str, label: str = "recovery") -> None:
    msg = f"{RECOVERY_PREFIX}: {text}"
    log(msg)
    try:
        telegram.send_message(secrets, msg, label=label)
    except Exception as e:
        log(f"recovery send failed: {type(e).__name__}")


# --- state file ---------------------------------------------------------------
def load_state() -> dict:
    try:
        data = json.loads(config.RUNS_STATE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("runs", [])
            data.setdefault("alarms", {})
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"run state unreadable, starting fresh: {type(e).__name__}: {e}")
    return {"runs": [], "alarms": {}}


def save_state(state: dict) -> None:
    try:
        config.RUNS_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = config.RUNS_STATE.with_suffix(config.RUNS_STATE.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(config.RUNS_STATE)
    except Exception as e:
        log(f"could not write run state {config.RUNS_STATE}: {type(e).__name__}: {e}")


def update_alarm(state: dict, key: str, active, now: dt.datetime, secrets: dict,
                 alert_text: str, recovery_text: str):
    """Throttled alarm. active: True (condition holds), False (cleared), None
    (unknown — leave the alarm as it is). Returns 'alert' | 'realert' |
    'recovery' | None."""
    alarms = state.setdefault("alarms", {})
    cur = alarms.get(key)
    if active is None:
        return None
    if active:
        if cur is None:
            alarms[key] = {"since": _iso(now), "last_alert": _iso(now)}
            alert(secrets, alert_text, label=key)
            return "alert"
        last = parse_ts(cur.get("last_alert"))
        due = dt.timedelta(hours=config.ALARM_REALERT_HOURS) - ALARM_REALERT_JITTER
        if last is None or now - last >= due:
            cur["last_alert"] = _iso(now)
            alert(secrets, f"{alert_text} (active since {_fmt(parse_ts(cur.get('since')))})",
                  label=key)
            return "realert"
        return None
    if cur is not None:
        del alarms[key]
        recovered(secrets, recovery_text, label=key)
        return "recovery"
    return None


# --- feed freshness -----------------------------------------------------------
def feed_status(now: dt.datetime = None) -> dict:
    """Freshness of the discovery feed. 'fresh' is False when the health file is
    missing/unreadable, discovery never succeeded, the last success is older than
    FEED_STALE_HOURS, or the feed file is missing / not rewritten since then."""
    now = now or utcnow()
    st = {"fresh": False, "problem": None, "last_ok_at": None, "age_hours": None,
          "consecutive_failures": 0, "last_error": None, "feed_mtime": None,
          "items": None, "health_missing": False}
    stale_after = dt.timedelta(hours=config.FEED_STALE_HOURS)

    try:
        st["feed_mtime"] = dt.datetime.fromtimestamp(config.DISCOVERY_FEED.stat().st_mtime, _UTC)
        d = json.loads(config.DISCOVERY_FEED.read_text(encoding="utf-8"))
        st["items"] = len(d.get("candidates", []))
    except FileNotFoundError:
        pass
    except Exception:
        pass  # a corrupt feed is reported by the ingest guard

    try:
        h = json.loads(config.DISCOVERY_HEALTH.read_text(encoding="utf-8"))
        if not isinstance(h, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        st["health_missing"] = True
        st["problem"] = f"{config.DISCOVERY_HEALTH.name} missing — discovery health unknown"
        return st
    except Exception as e:
        st["problem"] = f"{config.DISCOVERY_HEALTH.name} unreadable ({type(e).__name__})"
        return st

    try:
        st["consecutive_failures"] = int(h.get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        st["consecutive_failures"] = 0
    st["last_error"] = h.get("last_error")

    last_ok = parse_ts(h.get("last_ok_at"))
    if last_ok is None:
        last_ok = parse_ts(h.get("last_ok"))
        # Date-only health (pre-last_ok_at): the feed file is rewritten on every
        # successful run, so its mtime on that same day is the precise time.
        mt = st["feed_mtime"]
        if last_ok and mt and mt.date() == last_ok.date():
            last_ok = mt
    st["last_ok_at"] = last_ok

    if last_ok is None:
        st["problem"] = "discovery has no successful run on record"
        return st
    age = now - last_ok
    st["age_hours"] = age.total_seconds() / 3600
    if age > stale_after:
        st["problem"] = f"discovery stale since {_fmt(last_ok)} ({st['age_hours']:.0f}h ago)"
        return st
    if st["feed_mtime"] is None:
        st["problem"] = f"feed file {config.DISCOVERY_FEED.name} missing"
        return st
    if now - st["feed_mtime"] > stale_after:
        st["problem"] = f"feed file not rewritten since {_fmt(st['feed_mtime'])}"
        return st
    st["fresh"] = True
    return st


def feed_line(st: dict) -> str:
    """One explicit line for the run summary."""
    fails = st.get("consecutive_failures") or 0
    fail_note = ""
    if fails:
        err = str(st.get("last_error") or "")[:120]
        fail_note = f" · last discovery run FAILED ({fails}x in a row: {err})"
    if st.get("fresh"):
        items = st.get("items")
        extra = f", {items} in feed" if items is not None else ""
        return f"feed: fresh (discovery ok {_fmt(st.get('last_ok_at'))}{extra}){fail_note}"
    if st.get("health_missing"):
        return f"feed: UNKNOWN — {st['problem']}"
    return f"feed: STALE — {st['problem']}{fail_note}"


def start(secrets: dict, now: dt.datetime = None) -> dict:
    """Run start: freshness check + stale alarm. Returns the feed status."""
    now = now or utcnow()
    _run.update(started_at=now, recorded=False)
    st = feed_status(now)
    _run["feed"] = st
    log(f"run start · {feed_line(st)}")
    state = load_state()
    update_alarm(
        state, ALARM_STALE, not st["fresh"], now, secrets,
        alert_text=(f"discovery feed {feed_line(st)[len('feed: '):]}. Intake is blind: "
                    "'0 lead(s)' in the summary does NOT mean a quiet day. "
                    "Check the discovery cron, its log and discovery_health.json."),
        recovery_text=f"discovery feed is fresh again — {feed_line(st)}")
    save_state(state)
    return st


# --- zero-intake alarm --------------------------------------------------------
def zero_intake(runs: list, now: dt.datetime):
    """Return (active, detail). Active only if every run inside the window had
    candidates==0 and classified==0, there are at least ZERO_INTAKE_MIN_RUNS of
    them, and they span ZERO_INTAKE_MIN_SPAN_HOURS — which guarantees at least one
    daily discovery refresh fell between them, so a normal 0-candidate evening
    run (feed already consumed in the morning) can never trip it."""
    cutoff = now - dt.timedelta(hours=config.ZERO_INTAKE_WINDOW_HOURS)
    win = sorted(((parse_ts(r.get("at")), r) for r in runs), key=lambda x: x[0] or cutoff)
    win = [(t, r) for t, r in win if t is not None and cutoff <= t <= now]
    if len(win) < config.ZERO_INTAKE_MIN_RUNS:
        return False, f"{len(win)} run(s) in window"
    if any((r.get("candidates") or 0) > 0 or (r.get("classified") or 0) > 0 for _, r in win):
        return False, "intake present in window"
    span_h = (win[-1][0] - win[0][0]).total_seconds() / 3600
    if span_h < config.ZERO_INTAKE_MIN_SPAN_HOURS:
        return False, f"window spans only {span_h:.1f}h"
    # Report the whole streak, not just the window: "69 runs" reads differently from "3".
    streak, broke = [], False
    for t, r in sorted(((parse_ts(r.get("at")), r) for r in runs if parse_ts(r.get("at"))),
                       key=lambda x: x[0], reverse=True):
        if t > now:
            continue
        if (r.get("candidates") or 0) > 0 or (r.get("classified") or 0) > 0:
            broke = True
            break
        streak.append(t)
    count = f"{len(streak)}" if broke else f"at least {len(streak)}"  # history is bounded
    return True, (f"{count} consecutive runs since {_fmt(streak[-1])} "
                  f"had 0 candidates and 0 classified")


def finish_run(secrets: dict, candidates: int = 0, classified: int = 0, errors: int = 0,
               fatal: str = None, now: dt.datetime = None) -> dict:
    """Record this run's intake stats (once per run) and evaluate the zero-intake
    alarm. Returns {'zero_intake': (active, detail), 'action': ...}."""
    if _run.get("recorded"):
        return {"zero_intake": None, "action": None}
    now = now or _run.get("started_at") or utcnow()
    feed = _run.get("feed") or {}
    state = load_state()
    state["runs"].append({"at": _iso(now), "candidates": int(candidates),
                          "classified": int(classified), "errors": int(errors),
                          "feed_fresh": bool(feed.get("fresh")), "fatal": fatal})
    keep_after = now - dt.timedelta(days=30)
    state["runs"] = [r for r in state["runs"]
                     if (parse_ts(r.get("at")) or now) >= keep_after][-config.RUNS_HISTORY_MAX:]

    active, detail = zero_intake(state["runs"], now)
    if active:
        alarm = True
    elif candidates > 0 or classified > 0:
        alarm = False  # intake resumed -> recovery if an alarm was open
    else:
        alarm = None  # still zero but window not conclusive: keep alarm as is
    feed_note = feed_line(feed) if feed else "feed: status unknown"
    action = update_alarm(
        state, ALARM_ZERO_INTAKE, alarm, now, secrets,
        alert_text=(f"zero intake — {detail}. {feed_note}. "
                    "Upstream (discovery feed) is probably silent."),
        recovery_text=(f"intake resumed — {candidates} candidate(s), "
                       f"{classified} classified this run"))
    save_state(state)
    _run["recorded"] = True
    return {"zero_intake": (active, detail), "action": action}


def after_loop(secrets: dict, tally: dict, n_candidates: int) -> None:
    """Per-run error alerts after the candidate loop; sets tally['exit_code']."""
    errs = tally.get("classify_errors", 0)
    if n_candidates and errs >= n_candidates:
        alert(secrets, f"classify failed for ALL {n_candidates} candidate(s) — nothing "
                       f"classified, items left for retry next run. Last error: "
                       f"{tally.get('last_classify_error', '?')}", label="classify-error")
        tally["exit_code"] = 1
    elif errs:
        log(f"classify failed for {errs}/{n_candidates} candidate(s); left for retry")
    pulls = tally.get("pull_errors") or []
    if pulls:
        shown = "\n".join(f"• {p}" for p in pulls[:5])
        more = f"\n+ {len(pulls) - 5} more" if len(pulls) > 5 else ""
        alert(secrets, f"content pull failed for {len(pulls)} PASS lead(s) — not queued, "
                       f"retried next run:\n{shown}{more}", label="pull-error")


def summary_lines(tally: dict) -> str:
    """Lines appended to the Telegram run summary."""
    lines = [feed_line(_run.get("feed") or feed_status())]
    errs = tally.get("classify_errors", 0)
    pulls = len(tally.get("pull_errors") or [])
    if errs or pulls:
        lines.append(f"errors: {errs} classify, {pulls} pull (left for retry)")
    return "\n".join(lines)
