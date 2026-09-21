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
# taskmarket". Of the first 112, thirteen made any request at all, eleven of
# those verified their own DID once and stopped, which is step 3 of the task.
#
# So they are counted, and counted as what they are. BOUNTY_EPOCH matches
# scripts/taskmarket_measure.py.
BOUNTY_PLATFORMS = ("taskmarket",)
BOUNTY_EPOCH = "2026-09-20"
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
- The number that carries weight is how many got past signing up and made an
  authenticated call. Prefer it.

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

        # The only registration figure that is evidence of anything: an agent
        # that got past signing up and actually called the API.
        cur.execute(
            """SELECT count(DISTINCT k.did),
                      count(DISTINCT k.did) FILTER (
                          WHERE coalesce(a.platform,'') IN %s)
                 FROM agents a
                 JOIN usage_daily_keys k ON k.did = a.did
                WHERE a.created_at > now() - interval '7 days'
                  AND coalesce(a.platform,'') NOT IN %s
                  AND k.day >= (now() - interval '7 days')::date""",
            (BOUNTY_PLATFORMS, EXCLUDED_PLATFORMS))
        m["made_a_call"], m["calls_from_bounty"] = cur.fetchone()

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
    calls, from_bounty = m.get("made_a_call"), m.get("calls_from_bounty")
    if calls and from_bounty == calls:
        call_sub = f"all {calls} from the bounty cohort"
    elif calls:
        call_sub = f"{calls - (from_bounty or 0)} of them outside the bounty"
    else:
        call_sub = "none got past signing up"
    return [
        {"value": f"{m.get('organic_agents', '—')}",
         "label": "registrations",
         "sub": f"{top} · excludes {bounty} paid for by our own bounty"},
        {"value": f"{calls if calls is not None else '—'}",
         "label": "made an authenticated call",
         "sub": call_sub},
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
        f"Of all registrations, how many made an authenticated call: {m.get('made_a_call')}"
        f" (of those, from the bounty cohort: {m.get('calls_from_bounty')})",
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
    return (f"{m.get('made_a_call', 0)} of the agents that registered this week "
            f"made an authenticated call. {m.get('bounty_agents', 0)} of the "
            f"registrations came from a bounty we paid for. "
            f"{m.get('anchors', 0)} credential anchors went to Base.")


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
