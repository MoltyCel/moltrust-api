"""MolTrust Agent Watchdog - Monitors all cron agents and alerts on failure."""

import os, sys, json, datetime, glob, hashlib, httpx, logging, re

from app import notify

DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- Discovery-surface reconciliation ---------------------------------------
# Two surfaces agents discover us through: the MCP tool catalog (Smithery
# listing) and the A2A Agent-Card. When the origin gains/loses a tool but the
# Smithery listing hasn't re-scanned, discovery goes stale SILENTLY. With Option
# B (Smithery lists remote-at-origin, api.moltrust.ch/mcp), the steady state is
# origin == listing → Δ0. Any Δ = the Smithery remote needs a re-scan. This
# reconciles what we serve against each listing — "did the listing keep up", not
# "did a run error". The Smithery registry is queryable (registry.smithery.ai).
MCP_LOCAL_URL = "http://127.0.0.1:8002/mcp"
SMITHERY_REGISTRY_URL = "https://registry.smithery.ai/servers/@moltrust/moltrust-mcp-server"
GLAMA_LISTING_URL = "https://glama.ai/mcp/servers/MoltyCel/moltrust-mcp-server"
# Glama re-crawls on its own schedule and has no API we can read without a key,
# so the listing is scraped and checked weekly rather than hourly: a fresh
# origin change takes days to show up there, and an hourly alarm about it would
# be noise for six days out of seven.
GLAMA_CHECK_WEEKDAY = 0  # Monday

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
EXPECTED_AGENT_CARD_SKILLS = 13

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


# Agent definitions: name, max_hours without activity, check method
# Moltbook Poster: DISABLED 2026-03-30 — Moltbook API down post Meta acquisition (500 errors since 2026-03-27)
AGENTS = [
    {
        "name": "Herald",
        "heartbeat_file": os.path.join(DATA_DIR, "herald_heartbeat.json"),
        "max_hours": 12,  # runs 4x/day = every 6h, give 2h grace
        "fallback_glob": "herald_*.md",
    },
    {
        "name": "Scout",
        "heartbeat_file": None,
        "max_hours": 15,  # runs 2x/day = every 12h, 3h grace
        "fallback_glob": "scout_*.md",
    },
    {
        "name": "Ambassador",
        "heartbeat_file": None,
        "max_hours": 1.5,  # runs every 30min, give 1.5h grace
        "fallback_log": "ambassador.log",
    },
    {
        "name": "News Scout",
        "heartbeat_file": os.path.join(DATA_DIR, "news_scout_heartbeat.json"),
        "max_hours": 26,  # runs 1x/day, give 2h grace
        "fallback_glob": None,
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
            # stale-while-revalidate 24h) — the plain URL can lag a real change by
            # hours (verified 2026-07-18: cached 39 vs fresh 53). Force a fresh
            # read so a legit re-scan doesn't trigger a day of false drift alarms.
            sm = httpx.get(SMITHERY_REGISTRY_URL, params={"_cb": int(now.timestamp())},
                           headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
                           timeout=12.0).json()
            listed = len(sm.get("tools") or [])
            # Steady state (Option B: Smithery lists remote-at-origin) is
            # origin == listing → Δ0 → silent. Any Δ is real drift: the origin
            # gained/lost a tool and the Smithery remote hasn't re-scanned.
            if live != listed:
                out.append({"surface": "MCP↔Smithery", "ok": False,
                            "detail": f"origin exposes {live} tools, Smithery lists {listed} "
                                      f"(Δ{live - listed}) — Smithery remote out of sync with the "
                                      f"origin; re-scan/redeploy the Smithery listing"})
            else:
                out.append({"surface": "MCP↔Smithery", "ok": True,
                            "detail": f"{live} tools in sync (origin == listing)"})
        except Exception as e:
            # A Smithery registry outage must not masquerade as our drift.
            out.append({"surface": "MCP↔Smithery", "ok": True,
                        "detail": f"Smithery registry unreachable ({type(e).__name__}), skipped"})
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


def _package_mcp_tool_count() -> "int | None":
    """What the published moltrust-mcp-server package declares.

    Not the same number as the running server. `services/mcp_http.py` composes
    the hosted endpoint from the package plus `register_moltproof_tools`, which
    lives in this repository and ships to nobody. The package is what a user
    installs and what the GitHub repository declares.
    """
    import asyncio

    try:
        from moltrust_mcp_server.server import mcp as package_mcp
        return len(asyncio.run(package_mcp.list_tools()))
    except Exception:
        return None


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
        return {"surface": "MCP↔Glama", "ok": True,
                "detail": f"Glama unreachable ({type(e).__name__}), skipped"}

    indexed = {m.group(1) for m in _GLAMA_INDEXED_TOOL.finditer(html)}
    if not indexed:
        return {"surface": "MCP↔Glama", "ok": True,
                "detail": "no indexed tools found on the listing page, skipped"}

    packaged = _package_mcp_tool_count()
    if packaged is None:
        return {"surface": "MCP↔Glama", "ok": True,
                "detail": f"Glama has indexed {len(indexed)} tools; the package "
                          "could not be imported, so nothing to compare, skipped"}

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
        return {"surface": "x402-discovery", "ok": True,
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
BASE_RPC = "https://mainnet.base.org"


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
        resp = httpx.post(BASE_RPC, timeout=20.0,
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
        out.append({"surface": "Smithery/TXT", "ok": True,
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
            out.append({"surface": f"Smithery/{label}", "ok": True,
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
        out.append({"surface": "Smithery/listing", "ok": True,
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
        return [{"surface": "Smithery/record", "ok": True,
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
        return [{"surface": "Smithery/migration", "ok": True,
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


def run():
    now = datetime.datetime.now(datetime.UTC)
    log.info(f"Watchdog run at {now.strftime('%Y-%m-%d %H:%M UTC')}")

    alerts = []
    for agent in AGENTS:
        result = check_heartbeat(agent, now)
        status = "✅" if result["ok"] else "❌"
        log.info(f"  {status} {agent['name']}: {result['detail']}")
        if not result["ok"]:
            alerts.append(f"❌ <b>{agent['name']}</b>: {result['detail']}")

    # CONFORMANCE.md drift check
    drift = check_conformance_drift()
    status = "✅" if drift["ok"] else "❌"
    log.info(f"  {status} CONFORMANCE Drift: {drift['detail']}")
    if not drift["ok"]:
        alerts.append(f"❌ <b>CONFORMANCE Drift</b>: {drift['detail']}")

    # Discovery-surface reconciliation (MCP↔Smithery, Agent-Card)
    for r in check_discovery_drift(now):
        status = "✅" if r["ok"] else "❌"
        log.info(f"  {status} Discovery/{r['surface']}: {r['detail']}")
        if not r["ok"]:
            alerts.append(f"❌ <b>Discovery/{r['surface']}</b>: {r['detail']}")

    # Platform agent id on the machine-readable surfaces
    for r in check_platform_id_drift():
        status = "✅" if r["ok"] else "❌"
        log.info(f"  {status} PlatformId/{r['surface']}: {r['detail']}")
        if not r["ok"]:
            alerts.append(f"❌ <b>PlatformId</b> {r['surface']}: {r['detail']}")

    # Weekly: our own published proof, recomputed the way a stranger would.
    if _is_weekly_slot(now, PROOF_CHECK_WEEKDAY):
        pr = check_anchor_proof_replay()
        status = "✅" if pr["ok"] else "❌"
        log.info(f"  {status} AnchorProof: {pr['detail']}")
        if not pr["ok"]:
            alerts.append(f"❌ <b>AnchorProof</b>: {pr['detail']}")

    # Hourly: would the Bazaar crawler still accept our paid endpoints?
    # Coinbase's validator is the only authority on that — its rules are not
    # published in full, and our manifest has already looked right to us while
    # being wrong in ways only the crawler could see.
    try:
        from scripts.x402_validator_check import run_checks as x402_validator_checks

        for vr in x402_validator_checks():
            status = "❔" if vr.get("unverifiable") else ("✅" if vr["ok"] else "❌")
            log.info(f"  {status} x402Validator/{vr['name']}: {vr['detail']}")
            if not vr["ok"]:
                alerts.append(f"❌ <b>x402Validator</b> {vr['name']}: {vr['detail']}")
            elif vr.get("changed"):
                # A state change that is not a failure is still news — this is
                # how the Bazaar listing going live gets reported.
                alerts.append(f"ℹ️ <b>x402Validator</b> {vr['name']}: {vr['detail']}")
    except Exception as e:
        log.warning(f"  ❔ x402Validator: check did not run ({type(e).__name__})")

    # Weekly: the three things Smithery verification rests on.
    if _is_weekly_slot(now, SMITHERY_CHECK_WEEKDAY):
        for sr in check_smithery_verification():
            status = "✅" if sr["ok"] else "❌"
            log.info(f"  {status} {sr['surface']}: {sr['detail']}")
            if not sr["ok"]:
                alerts.append(f"❌ <b>{sr['surface']}</b>: {sr['detail']}")

    # Weekly: the published card, verified the way a stranger would.
    if _is_weekly_slot(now, CARD_CHECK_WEEKDAY):
        for cr in check_agent_card_signature():
            status = "✅" if cr["ok"] else "❌"
            log.info(f"  {status} CardSignature/{cr['surface']}: {cr['detail']}")
            if not cr["ok"]:
                alerts.append(f"❌ <b>CardSignature</b> {cr['surface']}: {cr['detail']}")

    if alerts:
        msg = "🐕 <b>Watchdog Alert</b>\n\n" + "\n".join(alerts)
        log.warning(f"Sending alert for {len(alerts)} agent(s)")
        send_telegram(msg)
    else:
        log.info("All agents healthy")


if __name__ == "__main__":
    run()
