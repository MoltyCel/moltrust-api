"""Weekly Proof Post — Sunday 08:00 UTC.

One post a week carrying what the last seven days actually produced: new agent
registrations, the platforms they came from, x402 receipts, credential anchors
on Base, and ClawHub installs. Every number comes from the database or from a
live API, never from a running total kept in this file.

Registrations exclude `ownify` and `test`. Ownify's own agents are permanently
free by agreement and test rows are ours, so counting either would be padding
the number with traffic we generated.

Registrations from `taskmarket` are counted separately and declared, for the
same reason one step further out: we paid for them. Two 5-USDC bounties were
created on 2026-09-20 at 12:28 UTC and the first taskmarket agent registered at
12:30:35, two and a half minutes later; there had never been one before. Of the
first 112, none has a wallet or an ERC-8004 id, thirteen made any request at
all, and eleven of those verified their own DID once and stopped — which is a
step in the task text. A proof post that reports that as growth is not a proof
post.

The post goes through both gates in agents/voice_gate.py, and the numbers go
through gate 2 (g) against the same figures that produced them, so a drafted
figure that does not appear in the measurement blocks the post.

    python agents/proof_post.py --dry-run    # measure, draft, scan, post nothing
    python agents/proof_post.py              # the scheduled run
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import psycopg2

from app import notify
from agents import digest_card, voice_gate, x_post

DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")
STATE_FILE = os.path.join(DATA_DIR, "proof_post_state.json")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "proof_post_heartbeat.json")

DB_URL = os.environ.get("DATABASE_URL", "dbname=moltstack user=moltstack")
CLAWHUB_URL = "https://clawhub.ai/api/v1/skills/moltrust-vet?owner=moltycel"
LANDING = "https://moltrust.ch"
EXCLUDED_PLATFORMS = ("ownify", "test")

# Registrations that arrive because we paid for them are not adoption, and a
# proof post that counts them is not proof.
#
# Our two taskmarket bounties (TSK-9YFR1YF7, TSK-KEYKZGQF, 5 USDC each) were
# created 2026-09-20 12:28:05 and 12:28:27 UTC. The first agent with
# platform='taskmarket' registered at 12:30:35 — two and a half minutes later,
# and there had never been one before. The platform value is not self-reported:
# our own task text says "POST /identity/register-pop with platform set to
# taskmarket". Of the 114, three made no request at all and 111 did the task
# unauthenticated, which the task text expressly allowed; 14 made an
# authenticated call and 12 of those verified their own DID once and stopped,
# which is step 3 of the task. An earlier note here said "of the first 112,
# thirteen made any request at all" — that was the authenticated count wearing
# the label of the total, and it understated what the cohort did by two orders
# of magnitude. Figures come from app/sql/taskmarket_cohort.sql.
#
# So they are counted, and counted as what they are. BOUNTY_EPOCH matches
# scripts/taskmarket_measure.py.
BOUNTY_PLATFORMS = ("taskmarket",)
BOUNTY_EPOCH = "2026-09-20"

# An agent counts as activated when it registered AND made at least one
# authenticated call to something the task text did not tell it to call.
#
# Every weaker test failed against the round-1 data (see the reconciliation of
# 2026-09-21): 111 of 114 made a public call, because the task says
# "Registration is free and needs no API key"; 68 bound an API key, because
# binding one is step 2 of the verify task, and 54 of those never used it; 14
# made an authenticated call, and 12 of those called only the one endpoint the
# task named. Two agents looked at anything else.
#
# So the endpoints below are excluded from the activation test — not because
# they are unimportant, but because we paid for them to be called.
# Round 2 (TSK-J3R0MDGA, opened 2026-09-23) asks the agent to bind a wallet and
# prove control of it, so the binding path joined the list. Leaving it out put
# 132 bounty agents in the activated column on 2026-09-26, of which 122 had
# called nothing but /identity/bind — the step the task text spells out.
SCRIPTED_ENDPOINTS = ("/identity/verify/", "/skill/trust-score/",
                      "/identity/erc8004/register", "/identity/bind",
                      "/identity/nonce", "/auth/signup-did",
                      "/credentials/track-record")
MODEL = "claude-opus-5"

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("proof_post")
notify.silence_http_request_logs()
os.makedirs(DATA_DIR, exist_ok=True)


SYSTEM_PROMPT = """You write the weekly post for @moltrust on X.

It reports what the last seven days produced. The post goes out with an image
that already shows every figure as a tile, so do not list them again. The text
picks the one number that carries the week and says why it is worth knowing.

Rules:
- One tweet. Max 200 characters, because a link is appended afterwards.
- Use only figures from the data below. Inventing or rounding to a rounder
  number will be caught and the post will be blocked.
- Open on the figure or on what produced it, never on "We", "Our", "MolTrust",
  "This week" or an emoji.
- Dry and factual. No hype, no hashtags, no rhetorical questions.
- Do not write "not X but Y" or any other contrast-pair construction.
- No link and no call to action: both are appended for you.
- If a number is zero, you may say so plainly. A quiet week is a fact.

About the bounty, and this rule is absolute:
- Registrations we paid for through a taskmarket bounty are not adoption. Never
  present the bounty figure as growth, uptake, interest or demand.
- You may mention it only while saying in the same sentence that we paid for it.
  "109 of them registered to claim a bounty we posted" is fine. "109 new agents
  this week" is not, and neither is any total that silently folds the two
  together.
- The number that carries weight is "activated": registered, then made an
  authenticated call to an endpoint no task text named. Prefer it. An agent
  that only did what a paid task told it to do has not adopted anything.

Write the tweet text only. No preamble, no quotes around it."""


# ── State ──

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"State write failed: {e}")


def write_heartbeat(status: str, detail: str = "") -> None:
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump({"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "status": status, "detail": detail}, f)
    except Exception:
        pass


def send_telegram(message: str, *, channel: str = notify.STATS) -> bool:
    """The weekly figures are stats; anything that went wrong is an alert."""
    return notify.send_telegram(message, channel=channel)


# ── Measurement ──

def measure() -> dict:
    """Everything the post can claim, read fresh. Missing pieces stay None so a
    failed source is visible rather than silently reported as zero."""
    m: dict = {"errors": []}
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        # psycopg2 adapts a tuple straight into the IN list, so the statement
        # stays a constant and no SQL is assembled from strings.
        cur.execute(
            """SELECT count(*), count(DISTINCT platform)
                 FROM agents
                WHERE created_at > now() - interval '7 days'
                  AND coalesce(platform,'') NOT IN %s
                  AND coalesce(platform,'') NOT IN %s""",
            (EXCLUDED_PLATFORMS, BOUNTY_PLATFORMS))
        m["organic_agents"], m["platforms_week"] = cur.fetchone()

        cur.execute(
            """SELECT count(*) FROM agents
                WHERE created_at > now() - interval '7 days'
                  AND coalesce(platform,'') IN %s
                  AND created_at >= %s::date""",
            (BOUNTY_PLATFORMS, BOUNTY_EPOCH))
        m["bounty_agents"] = cur.fetchone()[0]

        # Authenticated calls, and of those the ones the task script did not
        # dictate. The second number is the activation figure.
        cur.execute(
            """SELECT count(DISTINCT r.agent_did),
                      count(DISTINCT r.agent_did) FILTER (
                          WHERE NOT (r.endpoint LIKE ANY (%s))),
                      count(DISTINCT r.agent_did) FILTER (
                          WHERE coalesce(a.platform,'') IN %s),
                      count(DISTINCT r.agent_did) FILTER (
                          WHERE NOT (r.endpoint LIKE ANY (%s))
                            AND coalesce(a.platform,'') IN %s)
                 FROM agents a
                 JOIN request_log r ON r.agent_did = a.did
                WHERE a.created_at > now() - interval '7 days'
                  AND coalesce(a.platform,'') NOT IN %s""",
            ([p + "%" for p in SCRIPTED_ENDPOINTS], BOUNTY_PLATFORMS,
             [p + "%" for p in SCRIPTED_ENDPOINTS], BOUNTY_PLATFORMS,
             EXCLUDED_PLATFORMS))
        (m["made_a_call"], m["activated"], m["calls_from_bounty"],
         m["activated_from_bounty"]) = cur.fetchone()

        cur.execute(
            """SELECT platform, count(*)
                 FROM agents
                WHERE created_at > now() - interval '7 days'
                  AND coalesce(platform,'') NOT IN %s
                  AND coalesce(platform,'') NOT IN %s
                GROUP BY 1 ORDER BY 2 DESC LIMIT 3""",
            (EXCLUDED_PLATFORMS, BOUNTY_PLATFORMS))
        m["top_platforms"] = [(p, n) for p, n in cur.fetchall()]

        cur.execute(
            """SELECT count(DISTINCT platform) FROM agents
                WHERE coalesce(platform,'') NOT IN %s""",
            (EXCLUDED_PLATFORMS,))
        m["platforms_total"] = cur.fetchone()[0]

        cur.execute("""SELECT count(*), coalesce(sum(amount_usdc), 0)
                         FROM x402_receipts
                        WHERE seen_at > now() - interval '7 days'""")
        m["x402_count"], usdc = cur.fetchone()
        m["x402_usdc"] = float(usdc)

        cur.execute("""SELECT count(*), count(DISTINCT tx_hash)
                         FROM credential_anchors
                        WHERE anchored_at > now() - interval '7 days'""")
        m["anchors"], m["anchor_txs"] = cur.fetchone()

        cur.close()
        conn.close()
    except Exception as e:
        log.error(f"DB measurement failed: {e}")
        m["errors"].append(f"database: {e}")

    try:
        r = httpx.get(CLAWHUB_URL, timeout=20, follow_redirects=True)
        stats = r.json().get("skill", {}).get("stats", {}) if r.status_code == 200 else {}
        m["clawhub_installs"] = stats.get("installs")
        m["clawhub_downloads"] = stats.get("downloads")
        if not stats:
            m["errors"].append(f"clawhub: HTTP {r.status_code}")
    except Exception as e:
        log.error(f"ClawHub fetch failed: {e}")
        m["errors"].append(f"clawhub: {e}")

    return m


def tiles_for(m: dict) -> list[dict]:
    # Two platforms plus a remainder, so the sub-line never wraps a name away
    # from its count ("… base" / "2" on the next line).
    named = (m.get("top_platforms") or [])[:2]
    top = ", ".join(f"{p} {n}" for p, n in named) or "none"
    rest = (m.get("platforms_week") or 0) - len(named)
    if rest > 0:
        top += f", +{rest} more"
    bounty = m.get("bounty_agents")
    activated, calls = m.get("activated"), m.get("made_a_call")
    from_bounty = m.get("activated_from_bounty") or 0
    if activated and from_bounty == activated:
        act_sub = f"of {calls} that authenticated — all from the bounty cohort"
    elif activated:
        act_sub = f"of {calls} that authenticated · {activated - from_bounty} outside the bounty"
    elif calls:
        act_sub = f"{calls} authenticated, all of them only where the task said to"
    else:
        act_sub = "nobody got past signing up"
    return [
        {"value": f"{m.get('organic_agents', '—')}",
         "label": "registrations",
         "sub": f"{top} · excludes {bounty} paid for by our own bounty"},
        {"value": f"{activated if activated is not None else '—'}",
         "label": "activated",
         "sub": act_sub},
        {"value": f"{m.get('anchors', '—')}",
         "label": "credential anchors",
         "sub": f"in {m.get('anchor_txs', '—')} Base transactions"},
        {"value": f"{m.get('clawhub_installs', '—')}",
         "label": "ClawHub installs",
         "sub": f"{m.get('clawhub_downloads', '—')} downloads of moltrust-vet"},
    ]


def facts_block(m: dict) -> str:
    """The figures the drafter may use, and what gate 2 (g) checks against."""
    lines = [
        f"Registrations in the last 7 days, excluding the bounty: {m.get('organic_agents')}",
        f"Registrations driven by our own taskmarket bounty: {m.get('bounty_agents')}",
        f"Made an authenticated call: {m.get('made_a_call')}"
        f" (of those, from the bounty cohort: {m.get('calls_from_bounty')})",
        f"Activated — authenticated call to an endpoint no task text named: "
        f"{m.get('activated')} (of those, from the bounty cohort: "
        f"{m.get('activated_from_bounty')}). This is the figure the 90-day goal counts.",
        f"Platforms the non-bounty registrations came from: {m.get('platforms_week')}"
        + (" — " + ", ".join(f"{p} {n}" for p, n in (m.get('top_platforms') or []))
           if m.get('top_platforms') else ""),
        f"Distinct platforms ever seen: {m.get('platforms_total')}",
        f"x402 receipts this week: {m.get('x402_count')}, {m.get('x402_usdc')} USDC settled",
        f"Credential anchors written to Base: {m.get('anchors')} "
        f"in {m.get('anchor_txs')} transactions",
        f"ClawHub installs of moltrust-vet: {m.get('clawhub_installs')}, "
        f"downloads: {m.get('clawhub_downloads')}",
        "Registrations exclude the platforms ownify and test.",
        "The taskmarket figure is bounty-driven: we posted two paid tasks on "
        "2026-09-20 and those agents registered in order to claim them.",
    ]
    return "\n".join(f"- {line}" for line in lines)


# ── Drafting ──

def load_anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            with open(os.path.expanduser("~/.anthropic_key")) as f:
                key = f.read().strip()
        except Exception:
            pass
    return key


def draft_text(m: dict) -> str | None:
    key = load_anthropic_key()
    if not key:
        log.error("No Anthropic API key available")
        return None
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 1200, "system": SYSTEM_PROMPT,
                  "messages": [{"role": "user", "content":
                                f"Last seven days:\n\n{facts_block(m)}\n\n"
                                "Write the tweet text (max 200 characters, no link)."}]},
            timeout=90,
        )
    except Exception as e:
        log.error(f"Claude call failed: {e}")
        return None
    if r.status_code != 200:
        log.error(f"Claude API {r.status_code}: {r.text[:300]}")
        return None
    blocks = r.json().get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    return text.strip('"') or None


def fallback_text(m: dict) -> str:
    """Deterministic copy when Claude is unavailable. It leads on the call
    count for the same reason the prompt does, and names the bounty outright."""
    return (f"{m.get('activated', 0)} agents activated this week: registered, then "
            f"called something no task told them to call. "
            f"{m.get('bounty_agents', 0)} of the registrations came from a bounty "
            f"we paid for. {m.get('anchors', 0)} credential anchors went to Base.")


# ── Main ──

def run(dry_run: bool = False) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    week = now.strftime("%G-W%V")
    log.info("=" * 60)
    log.info(f"MOLTRUST WEEKLY PROOF POST — {now:%Y-%m-%d %H:%M UTC} ({week})")
    if dry_run:
        log.info("*** DRY RUN — will NOT post ***")

    state = load_state()
    if not dry_run and state.get("last_week") == week:
        log.info(f"Proof post for {week} already sent. Skipping.")
        write_heartbeat("skipped", f"already posted {week}")
        return

    m = measure()
    log.info(f"Measured: {json.dumps({k: v for k, v in m.items() if k != 'errors'})}")
    for err in m["errors"]:
        log.warning(f"Source failed: {err}")
    if m.get("organic_agents") is None:
        msg = "Database measurement failed — no post this week"
        log.error(msg)
        write_heartbeat("error", msg)
        send_telegram(f"⚠️ Weekly Proof Post\n{msg}\n" + "\n".join(m["errors"]),
                      channel=notify.ALERTS)
        return

    text = draft_text(m)
    if not text:
        log.warning("Claude unavailable — deterministic text")
        text = fallback_text(m)

    tweet = f"{text}\n\n{LANDING}"
    if len(tweet) > 280:
        keep = 280 - len(LANDING) - 5
        tweet = f"{text[:keep].rstrip()}…\n\n{LANDING}"

    scan = voice_gate.scan([tweet], source_text=facts_block(m), mode="post")
    log.info(voice_gate.format_report(scan))
    if not scan["ok"]:
        log.error("Pre-send scan BLOCKED the proof post — not posting")
        write_heartbeat("blocked", "; ".join(scan["violations"])[:200])
        send_telegram("⚠️ Weekly Proof Post blocked\n\n"
                      f"{tweet}\n\n{voice_gate.format_report(scan)}", channel=notify.ALERTS)
        return

    try:
        png = digest_card.render_metrics(
            tiles_for(m),
            foot_left=f"week {week} · excludes ownify and test · bounty cohort counted separately",
            foot_right="moltrust.ch", when=now)
        log.info(f"Card rendered: {len(png)} bytes")
    except Exception as e:
        log.error(f"Card render failed: {e}")
        png = None

    log.info(f"Tweet ({len(tweet)}/280):\n{tweet}")

    if dry_run:
        out = os.path.join(LOG_DIR, f"proof_{now:%Y%m%d}_preview.png")
        if png:
            with open(out, "wb") as f:
                f.write(png)
            log.info(f"DRY RUN — card written to {out}")
        print(f"\n{'=' * 60}\nWEEKLY PROOF ({len(tweet)} chars)\n\n{tweet}\n\n"
              f"card: {out if png else 'render failed'}\n{'=' * 60}")
        return

    media_ids = []
    if png:
        mid = x_post.upload_image(png)
        if mid:
            media_ids.append(mid)
        else:
            log.warning("Image upload failed — posting text only")

    tweet_id = x_post.post(tweet, media_ids=media_ids or None)
    if not tweet_id:
        msg = "Weekly proof post failed to send"
        log.error(msg)
        write_heartbeat("error", msg)
        send_telegram(f"⚠️ Weekly Proof Post\n{msg}", channel=notify.ALERTS)
        return

    state["last_week"] = week
    state["last_tweet_id"] = tweet_id
    state["last_measurement"] = {k: v for k, v in m.items() if k != "errors"}
    save_state(state)

    url = f"https://x.com/MolTrust/status/{tweet_id}"
    report = os.path.join(LOG_DIR, f"proof_{now:%Y%m%d}.md")
    try:
        with open(report, "w") as f:
            f.write(f"# Weekly Proof Post — {week}\n\n**Date:** {now:%Y-%m-%d %H:%M UTC}\n")
            f.write(f"**Tweet:** {url}\n\n{tweet}\n\n## Measurement\n\n{facts_block(m)}\n\n")
            f.write(f"## Pre-send scan\n```\n{voice_gate.format_report(scan)}\n```\n")
        log.info(f"Report: {report}")
    except Exception as e:
        log.warning(f"Report write failed: {e}")

    send_telegram(f"\U0001f4ca Weekly Proof Post sent\n{url}\n\n{tweet}")
    write_heartbeat("ok", f"posted {tweet_id}")


if __name__ == "__main__":
    try:
        run(dry_run="--dry-run" in sys.argv)
    except Exception as e:
        log.error(f"FATAL: {e}\n{traceback.format_exc()}")
        write_heartbeat("crash", str(e))
        send_telegram(f"\U0001f6a8 Weekly Proof Post CRASHED\n{str(e)[:300]}",
                      channel=notify.ALERTS)
        sys.exit(1)
