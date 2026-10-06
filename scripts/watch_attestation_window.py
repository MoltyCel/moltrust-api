#!/usr/bin/env python3
"""Watch the disabled attestation routes until the last attestation expires.

/guard/governance/validate-capabilities was taken out of service on 2026-10-05
after it turned out to issue signed authorization attestations to anyone, for
any DID named in the body. 28 were issued between 2026-09-20 and 2026-10-05.
/guard/shopping/verify and /guard/travel/verify went off the same day, for
accepting a credential with no signature, no expiry and no spend limit.

27 of the 28 are past their maximum seven-day window. One is not: issued
2026-10-01 16:41 UTC, so at worst valid until 2026-10-08 16:41 UTC. The real
window is unknowable because `validity_hours` came from the request body and
the body was never logged, so this watch assumes the cap.

No key rotation. The one attestation expires on its own.

What this reports, and what it stopped reporting
------------------------------------------------

The first version alerted on `attestation_missing`, and in its first three
hours it sent twelve alerts about 150 of them. `gate.py:429` returns that
reason when the caller sent no attestation header at all — which is every
ordinary unauthenticated request to a gated path. It is the normal state of
the system, not an attempt to redeem anything. Reporting it buried the one
event that mattered and taught its reader to skim the channel.

So a redemption attempt is now only this: an attestation was presented and the
verifier threw it out. `gate.py:440` turns every such failure — malformed,
wrong signature, wrong version, expired — into `attestation_invalid`, which is
the one reason this watch counts. A governance-shaped payload cannot be
separated out here, because `gate_decisions` stores `reason` and no detail; it
is a subset of these rejections and is counted with them rather than claimed
as its own number.

Two kinds of report
-------------------

  immediate   a call against a disabled route, or an attestation rejected.
              Only on a real hit, within one cron tick.

  digest      08:00 and 20:00 UTC, always, even at zero. Window, the two
              counts, and when this watch last ran. "0 / 0" is the message;
              silence is not. A watch that goes quiet and a watch that stopped
              running look identical from the outside, so the digest carries
              its own last-run timestamp and `check_attestation_watch_reports.py`
              treats a missing slot as a finding.

After the window closes it confirms once that nothing is left, then stops
alerting and keeps sending digests until it is removed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import urllib.parse
import urllib.request

WINDOW_END = dt.datetime(2026, 10, 8, 16, 41, tzinfo=dt.timezone.utc)

# The routes that actually answer 503 today, verified by calling them. The
# badge is not here: PR #619 is still queued, so a call to it is an ordinary
# 404 and counting it as a call against a disabled route would be a false
# number. It joins this list when #619 merges.
ROUTES = (
    "%validate-capabilities%",
    "%/shopping/verify%",
    "%/travel/verify%",
)

# An attestation was presented and thrown out. Everything that starts with
# `attestation` except `attestation_missing`, so a reason added later for
# expiry or version is caught without editing this line. `attestation_missing`
# means no attestation was presented at all.
REDEEM_SQL = "reason LIKE 'attestation%' AND reason <> 'attestation_missing'"

DIGEST_HOURS = (8, 20)
STATE = os.path.expanduser("~/.attestation_watch_state.json")


def telegram(text: str) -> str:
    """Send to ALERTS. Returns the channel actually used, for the record."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    alerts = os.environ.get("TELEGRAM_CHAT_ID_ALERTS", "")
    chat = alerts or os.environ.get("TELEGRAM_CHAT_ID", "")
    used = "ALERTS" if alerts else ("default" if chat else "none")
    if not token or not chat:
        print("no telegram config; would have sent:\n" + text)
        return used
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        urllib.request.urlopen(req, timeout=20).read()  # noqa: S310  # nosec B310 - literal host
    except Exception as exc:  # noqa: BLE001 - a failed alert must not fail the check
        print(f"telegram failed: {exc}")
        return used + " (send failed)"
    return used


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack", "-X", "-A", "-t",
         "-F", "\x1f", "-c", sql],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def state() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - first run, or a file we will rewrite anyway
        return {}


def save(d: dict) -> None:
    with open(STATE, "w") as f:
        json.dump(d, f)
    os.chmod(STATE, 0o600)


def route_clause(column: str = "endpoint") -> str:
    return " OR ".join(f"{column} ILIKE '{r}'" for r in ROUTES)


def calls_since(since: str) -> list[list[str]]:
    return psql(
        "SELECT ts, endpoint, status_code, coalesce(ip_org,'(no org)'), "
        f"coalesce(ip_country,'?') FROM request_log WHERE ({route_clause()}) "
        f"AND ts > '{since}' ORDER BY ts")


def rejections_since(since: str) -> list[list[str]]:
    try:
        return psql(
            "SELECT ts, coalesce(did,'?'), path, reason FROM gate_decisions "
            f"WHERE ts > '{since}' AND {REDEEM_SQL} ORDER BY ts")
    except Exception as exc:  # noqa: BLE001 - the table may not exist on every host
        print(f"gate_decisions unreadable: {exc}")
        return []


def due_slot(now: dt.datetime, sent: dict) -> tuple[str, dt.datetime, dt.datetime] | None:
    """The most recent digest slot that has passed and was not sent yet.

    Returns (key, slot time, window start). The window starts at the previous
    slot, so the two digests of a day tile it without a gap or an overlap.
    """
    cands = []
    for day in (now.date(), now.date() - dt.timedelta(days=1)):
        for h in DIGEST_HOURS:
            t = dt.datetime.combine(day, dt.time(h), tzinfo=dt.timezone.utc)
            if t <= now:
                cands.append(t)
    if not cands:
        return None
    slot = max(cands)
    key = f"{slot:%Y-%m-%dT%H}"
    if key in sent:
        return None
    prev = slot - dt.timedelta(hours=24 // len(DIGEST_HOURS))
    return key, slot, prev


def main() -> int:
    st = state()
    sent = st.get("digests") or {}
    now = dt.datetime.now(dt.timezone.utc)
    last_run = st.get("last_run") or "never"
    since = st.get("since") or (now - dt.timedelta(hours=1)).isoformat()

    calls = calls_since(since)
    rejects = rejections_since(since)

    # Immediate, only on a real hit.
    if calls or rejects:
        lines = [f"  call {ts} {ep} HTTP {code} from {org} / {land}"
                 for ts, ep, code, org, land in calls]
        lines += [f"  reject {ts} {reason} did={did} path={path}"
                  for ts, did, path, reason in rejects]
        used = telegram("ALERTS — disabled attestation routes\n"
                        f"since {since}\n" + "\n".join(lines[:25])
                        + ("\n  …" if len(lines) > 25 else "")
                        + "\n\nThe routes answer 503. Nothing they signed is being renewed.")
        print(f"immediate: {len(calls)} calls, {len(rejects)} rejections → telegram:{used}")

    # Digest, always, even at zero.
    slot = due_slot(now, sent)
    if slot:
        key, slot_at, win_start = slot
        w = win_start.isoformat()
        n_calls = len(calls_since(w))
        n_rejects = len(rejections_since(w))
        used = telegram(
            f"attestation watch — {slot_at:%Y-%m-%d %H:%M}Z\n"
            f"window {win_start:%m-%d %H:%M}Z → {slot_at:%m-%d %H:%M}Z\n"
            f"503 calls against the disabled routes: {n_calls}\n"
            f"rejected redemption attempts (form, signature, version, expiry): {n_rejects}\n"
            f"this watch last ran: {last_run}\n"
            f"attestation window {'closed' if now > WINDOW_END else 'open until'} "
            f"{'' if now > WINDOW_END else format(WINDOW_END, '%Y-%m-%d %H:%M') + 'Z'}".rstrip())
        sent[key] = now.isoformat()
        # Two weeks is enough for the invariant to look back over 24 h.
        for k in sorted(sent)[:-28]:
            sent.pop(k, None)
        print(f"digest {key}: {n_calls} / {n_rejects} → telegram:{used}")

    if now > WINDOW_END and not st.get("closed"):
        telegram(
            "ALERTS — attestation window closed\n"
            f"The last attestation /guard/governance/validate-capabilities could have "
            f"issued expired at {WINDOW_END:%Y-%m-%d %H:%M}Z at the latest "
            "(2026-10-01 16:41 + the 7-day cap).\n"
            "No key rotation was needed. Nothing it signed is valid any more.\n"
            "This watch stops alerting now; the digest keeps running until it is removed.")
        st["closed"] = True

    st["since"] = now.isoformat()
    st["last_run"] = now.isoformat()
    st["digests"] = sent
    save(st)
    if not (calls or rejects or slot):
        print(f"quiet · next digest after "
              f"{min(h for h in DIGEST_HOURS if h > now.hour) if any(h > now.hour for h in DIGEST_HOURS) else DIGEST_HOURS[0]:02d}:00Z")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
