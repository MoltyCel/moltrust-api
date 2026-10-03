"""Read-only state of the reply radar and the X budget, as markdown.

Written for the `diagnose` job in .github/workflows/deploy.yml: Lars runs it
from his phone and reads the answer in the job summary, without SSH.

Two properties this file has to keep, because a diagnostic that changes what it
measures is worse than none:

**It writes nothing.** No state file, no flag, no counter, no Telegram. It never
calls x_meter.trip_breaker, and it never asks X anything — every number comes
out of the ledger, the state file and the log that are already on disk. A run
costs nothing and moves no boundary.

**It needs no secrets.** Nothing here is sourced from ~/.moltrust_secrets, so
nothing can leak into a job summary that GitHub keeps for 90 days. Postgres is
reached over the local socket with peer authentication, which is the same
reason.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "moltstack")
RADAR_LOG = os.path.join(BASE, "logs", "reply_radar.log")
RADAR_STATE = os.path.join(BASE, "data", "reply_radar_state.json")
LEDGER = os.path.join(BASE, "data", "x_meter.jsonl")
FLAG = os.path.join(BASE, "data", "x_reads_paused")

# The log is long (370k lines after two weeks) and every question here is about
# the last day or two, so only the tail is parsed.
TAIL_LINES = 4000
RUN_RE = re.compile(r"^\[(\S+)\] INFO: REPLY RADAR — ")
LINE_RE = re.compile(r"^\[(\S+)\] (\w+): (.*)$")


def both(ts: str) -> str:
    """UTC and CEST side by side. Lars reads the second one."""
    try:
        d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    utc = d.astimezone(datetime.timezone.utc)
    # Europe/Zurich is UTC+2 from the last Sunday in March to the last in
    # October. Computed rather than hardcoded so the report does not quietly
    # shift by an hour at the end of the month.
    cest = utc + datetime.timedelta(hours=2 if _summer(utc) else 1)
    return (f"{utc.strftime('%Y-%m-%d %H:%M:%S')} UTC / "
            f"{cest.strftime('%H:%M:%S')} {'CEST' if _summer(utc) else 'CET'}")


def _summer(d: datetime.datetime) -> bool:
    def last_sunday(year: int, month: int) -> datetime.datetime:
        n = datetime.datetime(year, month, 31, 1, tzinfo=datetime.timezone.utc)
        while n.month != month:
            n -= datetime.timedelta(days=1)
        while n.weekday() != 6:
            n -= datetime.timedelta(days=1)
        return n
    return last_sunday(d.year, 3) <= d < last_sunday(d.year, 10)


def tail(path: str, n: int = TAIL_LINES) -> list[str]:
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError as e:
        return [f"__unreadable__ {type(e).__name__}: {e}"]


def runs_from_log(lines: list[str]) -> list[dict]:
    """One dict per radar run, in order, with what that run did."""
    runs: list[dict] = []
    for line in lines:
        m = RUN_RE.match(line)
        if m:
            runs.append({"at": m.group(1), "paused": None, "candidates": None,
                         "drafts": None, "errors": [], "skips": 0})
            continue
        if not runs:
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        at, level, msg = m.groups()
        r = runs[-1]
        if msg.startswith("Reads paused:"):
            r["paused"] = msg.split("Reads paused:", 1)[1].strip()
        elif "Candidates after filtering:" in msg:
            g = re.search(r"filtering: (\d+)", msg)
            r["candidates"] = int(g.group(1)) if g else None
        elif msg.startswith("Done:"):
            g = re.search(r"Done: (\d+)", msg)
            r["drafts"] = int(g.group(1)) if g else None
        elif re.match(r"GET .* -> \d{3}", msg):
            g = re.search(r"GET (\S+) -> (\d{3})", msg)
            if g:
                r["errors"].append((g.group(1).rsplit("/", 1)[-1], int(g.group(2)), at))
        elif msg.lstrip().startswith("skip "):
            r["skips"] += 1
    return runs


def last_draft_at(lines: list[str]) -> str | None:
    """The newest line that put a draft in front of Lars.

    A blocked draft counts: it is delivered to Telegram and read there. What
    does not count is a skip — nothing was sent.
    """
    for line in reversed(lines):
        m = LINE_RE.match(line)
        if m and re.match(r"\s*draft \d+ for ", m.group(3)):
            return m.group(1)
    return None


def section_runs(lines: list[str]) -> str:
    runs = runs_from_log(lines)
    out = ["## 1 · Läufe", ""]
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                              timeout=20).stdout.splitlines()
        # Schedule plus the radar's own flags, and nothing in between: the
        # middle of a cron line is `source ~/.moltrust_secrets`, which has no
        # business in a job summary GitHub keeps for 90 days.
        sched = []
        for c in cron:
            if "reply_radar.py" not in c:
                continue
            fields = c.split()
            flags = [f for f in fields if f.startswith("--")] or ["(Suche + Liste)"]
            sched.append(" ".join(fields[:5]) + "   reply_radar.py " + " ".join(flags))
        out += ["```", *sched, "```", ""]
    except Exception as e:
        out += [f"Crontab nicht lesbar ({type(e).__name__}).", ""]
    if not runs:
        return "\n".join(out + [f"Keine Lauf-Kopfzeile in den letzten "
                                f"{TAIL_LINES} Logzeilen."])
    out += ["| Lauf (UTC) | Kandidaten | Entwürfe | verworfen | Breaker | Fehler |",
            "|---|---:|---:|---:|---|---|"]
    for r in runs[-8:]:
        out.append(f"| {r['at']} | {_n(r['candidates'])} | {_n(r['drafts'])} | "
                   f"{r['skips']} | {'ja' if r['paused'] else '—'} | "
                   f"{', '.join(f'{n} {s}' for n, s, _ in r['errors']) or '—'} |")
    last = runs[-1]
    with_cand = [r for r in runs if (r["candidates"] or 0) > 0]
    with_draft = [r for r in runs if (r["drafts"] or 0) > 0]
    d = last_draft_at(lines)
    out += ["",
            f"- letzter Lauf überhaupt: **{both(last['at'])}**",
            f"- letzter Lauf mit Kandidaten: "
            f"**{both(with_cand[-1]['at']) if with_cand else 'keiner im Fenster'}**",
            f"- letzter Lauf mit Entwürfen: "
            f"**{both(with_draft[-1]['at']) if with_draft else 'keiner im Fenster'}**",
            f"- letzter zugestellter Entwurf: "
            f"**{both(d) if d else 'keiner im Fenster'}**",
            f"- vom Breaker abgebrochen (letzte {len(runs)} Läufe): "
            f"**{sum(1 for r in runs if r['paused'])}**"]
    return "\n".join(out)


def _n(v) -> str:
    return "—" if v is None else str(v)


def section_breaker() -> str:
    out = ["## 2 · Breaker, live gerechnet", ""]
    try:
        from agents import x_meter
    except Exception as e:
        return "\n".join(out + [f"x_meter nicht importierbar: {type(e).__name__}: {e}"])
    now = datetime.datetime.now(datetime.timezone.utc)
    paused = x_meter.reads_paused()
    out += [f"- jetzt: **{both(now.isoformat())}**",
            f"- gerechneter UTC-Tag: **{x_meter._day()}**",
            f"- Live-Summe: **${x_meter.live_spend_usd():.3f}**",
            f"- Soll ${x_meter.DAILY_TARGET_USD:.2f} · Alarm "
            f"${x_meter.DAILY_ALARM_USD:.2f} · Breaker "
            f"${x_meter.DAILY_BREAK_USD:.2f}",
            f"- `reads_paused()`: **{paused or 'offen'}**", ""]
    try:
        flag = json.load(open(FLAG))
        age = now - datetime.datetime.fromtimestamp(os.path.getmtime(FLAG),
                                                    datetime.timezone.utc)
        stale = flag.get("day") != x_meter._day()
        out += [f"- Watchdog-Flag: Tag **{flag.get('day')}**, "
                f"${float(flag.get('usd', 0)):.2f}, geschrieben "
                f"{both(flag.get('at', ''))} (vor {age.total_seconds() / 3600:.1f} h)"
                f"{' — **veraltet, ohne Wirkung**' if stale else ''}"]
    except FileNotFoundError:
        out += ["- Watchdog-Flag: nicht vorhanden (Reads sind offen)"]
    except Exception as e:
        out += [f"- Watchdog-Flag: nicht lesbar ({type(e).__name__})"]
    return "\n".join(out)


def ledger_rows(days: int = 3) -> list[dict]:
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []
    try:
        with open(LEDGER) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("at") or "")[:10] >= cutoff:
                    rows.append(r)
    except OSError:
        pass
    return rows


def section_spend() -> str:
    out = ["## 3 · x_meter je UTC-Tag", ""]
    try:
        from agents import x_meter
    except Exception as e:
        return "\n".join(out + [f"x_meter nicht importierbar: {type(e).__name__}"])
    today = datetime.datetime.now(datetime.timezone.utc)
    out += ["| Tag | Summe | Posts | Profile | Writes |", "|---|---:|---:|---:|---:|"]
    for i in (2, 1, 0):
        d = (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        s = x_meter.spend(d)
        out.append(f"| {d} | ${s['usd']:.3f} | {s['posts']} | {s['users']} | "
                   f"{s['writes']} |")
    rows = [r for r in ledger_rows(1)
            if (r.get("at") or "")[:10] == today.strftime("%Y-%m-%d")]
    out += ["", f"Seit 00:00 UTC: **{len(rows)} Ledger-Zeilen**"
            f"{' — keine, heute lief kein Request' if not rows else ''}", ""]
    if rows:
        # Per source and hour, because the day's shape is the finding: one
        # search run can spend most of the budget before the later runs start.
        by: dict[tuple, list[int]] = {}
        for r in rows:
            k = ((r.get("at") or "")[11:16], r.get("source") or "?")
            p, u = by.setdefault(k, [0, 0])
            by[k] = [p + len(r.get("posts") or []), u + len(r.get("users") or [])]
        out += ["| Zeit | Quelle | Posts | Profile | Kosten |",
                "|---|---|---:|---:|---:|"]
        for (hm, src), (p, u) in sorted(by.items()):
            usd = p * x_meter.USD_PER_POST_READ + u * x_meter.USD_PER_USER_READ
            out.append(f"| {hm} | {src} | {p} | {u} | ${usd:.3f} |")
    return "\n".join(out)


def section_sources(lines: list[str]) -> str:
    out = ["## 4 · Quellen", ""]
    rows = ledger_rows(3)
    last: dict[str, str] = {}
    for r in rows:
        if r.get("kind") != "read":
            continue
        s = r.get("source") or "?"
        if (r.get("at") or "") > last.get(s, ""):
            last[s] = r["at"]
    # The meter names a source by the last path segment, and /2/lists/<id>/tweets
    # and /2/users/<id>/tweets both end in `tweets`. Said here rather than
    # silently folded, because the two legs have very different costs.
    out += ["| Quelle (Ledger-Name) | letzter Abruf mit Ergebnis |", "|---|---|"]
    for s in sorted(last):
        out.append(f"| `{s}` | {both(last[s])} |")
    out += ["", "`tweets` ist Liste **und** eigene Timeline — der Meter benennt "
            "Quellen nach dem letzten Pfadsegment.", ""]
    errs = [(n, st, at) for r in runs_from_log(lines) for n, st, at in r["errors"]]
    if errs:
        out += ["| Fehler | Status | wann |", "|---|---:|---|"]
        for n, st, at in errs[-6:]:
            out.append(f"| `{n}` | {st} | {both(at)} |")
    else:
        out += [f"Kein Non-200 in den letzten {TAIL_LINES} Logzeilen."]
    try:
        st = json.load(open(RADAR_STATE))
        rep = st.get("source_failures_reported") or {}
        out += ["", f"`source_failures_reported`: "
                f"**{json.dumps(rep) if rep else 'leer — keine Quelle gemeldet'}**"]
    except Exception as e:
        out += ["", f"State nicht lesbar ({type(e).__name__})"]
    return "\n".join(out)


def section_delivery() -> str:
    out = ["## 5 · Zustellung", ""]
    try:
        st = json.load(open(RADAR_STATE))
        out += [f"- `last_run_at`: **{both(st.get('last_run_at') or '')}**",
                f"- `manual_checked_at`: {both(st.get('manual_checked_at') or '')}",
                f"- `drafts_sent` gesamt: **{st.get('drafts_sent')}** · "
                f"davon manuell gepostet {st.get('drafts_manual_posted')}",
                f"- `drafts_by_source`: `{json.dumps(st.get('drafts_by_source'))}`",
                f"- `per_day`: `{json.dumps(st.get('per_day'))}`",
                f"- `posted_per_day`: `{json.dumps(st.get('posted_per_day'))}`",
                f"- `manual_pending`: "
                f"**{len(st.get('manual_pending') or {})}** offen"]
    except Exception as e:
        out += [f"- State nicht lesbar ({type(e).__name__}: {e})"]
    q = ("select count(*)::text || ' | ' || "
         "count(*) filter (where consumed_by is null)::text || ' | ' || "
         "coalesce(to_char(max(ts) at time zone 'UTC','YYYY-MM-DD HH24:MI:SS'),'—') "
         "|| ' | ' || coalesce(to_char(max(consumed_at) at time zone 'UTC',"
         "'YYYY-MM-DD HH24:MI:SS'),'—') from telegram_inbox;")
    try:
        r = subprocess.run(["psql", "-tA", "-d", "moltstack", "-c", q],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            total, unconsumed, last_row, last_consumed = [
                x.strip() for x in r.stdout.strip().split("|")]
            out += ["", "`telegram_inbox` (Eingang, Button-Klicks):",
                    f"- {total} Zeilen, **{unconsumed} unverarbeitet**",
                    f"- letzte Zeile {last_row} UTC · letzte Verarbeitung "
                    f"{last_consumed} UTC"]
        else:
            out += ["", f"`telegram_inbox` nicht abfragbar: "
                    f"{(r.stderr or '').strip()[:160]}"]
    except Exception as e:
        out += ["", f"`telegram_inbox` nicht abfragbar ({type(e).__name__})"]
    return "\n".join(out)


def main() -> int:
    lines = tail(RADAR_LOG)
    if lines and lines[0].startswith("__unreadable__"):
        print(f"# Reply-Radar Diagnose\n\nLog nicht lesbar: {lines[0]}")
        return 1
    print("# Reply-Radar Diagnose\n")
    print(f"Erhoben {both(datetime.datetime.now(datetime.timezone.utc).isoformat())} "
          f"· read-only, nichts geändert\n")
    for part in (section_runs(lines), section_breaker(), section_spend(),
                 section_sources(lines), section_delivery()):
        print(part)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
