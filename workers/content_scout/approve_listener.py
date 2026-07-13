"""Two-way Telegram approval for Content-Scout gh_comment drafts.

Lars taps `approve <id>` or `discard <id>` in the @moltrust_stats_bot chat; nothing
posts autonomously. This module is the COMMAND HANDLER only — it is driven by the
ThreadWatch poller (scripts/threadwatch.py), which owns the single getUpdates loop on
this bot and already restricts to the authorised chat. A standalone webhook is NOT
used: setting a Telegram webhook disables getUpdates and would break ThreadWatch's
/pin·/ack commands on the same bot.

approve is allowed ONLY when the row is verify-confirmed for its current version
(primary-source checked). Unverified → refused, no post. On approve of a verified row:
post as MoltyCel, mark published, add the thread to ThreadWatch, reply the live URL.
"""
import datetime as _dt
import json
import re

import httpx

from . import config, db
from .cli import _pin_threadwatch  # reuse the idempotent ThreadWatch pin

_CMD = re.compile(r"^\s*(approve|discard)\s+#?(\d+)\s*$", re.IGNORECASE)


def is_command(text: str) -> bool:
    return bool(_CMD.match(text or ""))


async def handle_command(conn, secrets: dict, text: str, sender_chat_id=None) -> str | None:
    """Parse and execute one approve/discard command. Returns a reply string, or
    None if `text` is not one of our commands / the sender is not allow-listed.

    HOLE #2 — allowlist: approve/discard is accepted ONLY from Lars's chat_id
    (secrets['TELEGRAM_CHAT_ID'], the single-entry allowlist). Any other sender is
    logged and dropped, independently of the ThreadWatch poller's own chat filter."""
    m = _CMD.match(text or "")
    if not m:
        return None
    allowed = str(secrets.get("TELEGRAM_CHAT_ID", "")).strip()
    if not allowed or str(sender_chat_id).strip() != allowed:
        print(f"approve_listener: DROPPED '{(text or '').strip()}' from unauthorised "
              f"chat {sender_chat_id!r} (allowlist={allowed!r})")
        return None
    action, rid = m.group(1).lower(), int(m.group(2))
    r = await db.get_row(conn, rid)
    if not r:
        return f"#{rid}: no such row"
    if r["draft_type"] != "gh_comment":
        return f"#{rid}: draft_type={r['draft_type']} — approve/discard is for gh_comment"

    if action == "discard":
        await db.set_state(conn, rid, "discarded")
        return f"🗑 #{rid} discarded ({r['target']})"

    # --- approve ---
    if r["state"] == "published":
        return f"#{rid}: already published (no-op)"
    ver = r["redraft_version"]
    if r["verify_confirmed_version"] != ver:
        return (f"⛔ #{rid}: needs primary-source verify first "
                f"(verify_confirmed_version={r['verify_confirmed_version']}, current v{ver}). "
                f"Not posted.")
    if r["code_flag"] == "needs-code-verification":
        return (f"⛔ #{rid}: draft holds an unverified code block (needs-code-verification). "
                f"Clear it before approve. Not posted.")
    target = r["target"] or ""
    if "#" not in target:
        return f"⛔ #{rid}: target {target!r} is not repo#num — cannot post"
    tok = secrets.get("GH_TOKEN", "")
    if not tok:
        return f"⛔ #{rid}: GH_TOKEN missing — cannot post"

    repo, _, num = target.rpartition("#")
    try:
        resp = httpx.post(
            f"https://api.github.com/repos/{repo}/issues/{num}/comments",
            json={"body": r["draft_md"]}, timeout=30,
            headers={"Authorization": f"Bearer {tok}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": config.USER_AGENT})
    except Exception as e:
        return f"⛔ #{rid}: post errored ({type(e).__name__}); not marked published"
    if resp.status_code != 201:
        return f"⛔ #{rid}: post failed HTTP {resp.status_code}; not marked published"

    url = resp.json().get("html_url", "(no url)")
    # record approval for this version, then mark published (the schema's done-state)
    await conn.execute("UPDATE content_review_queue SET approved_version=$2 WHERE id=$1", rid, ver)
    await db.set_state(conn, rid, "published")
    tw = _pin_threadwatch(target, _dt.date.today().isoformat())
    return f"✅ #{rid} POSTED → {url}\n{tw}"


def handle_sync(text: str, sender_chat_id=None):
    """Synchronous entry point for the ThreadWatch poller (which is not async).
    Returns a reply string, or None if `text` is not an approve/discard command
    from the allow-listed chat."""
    import asyncio
    if not is_command(text):
        return None

    async def _run():
        secrets = config.load_secrets()
        conn = await db.connect(secrets)
        try:
            return await handle_command(conn, secrets, text, sender_chat_id)
        finally:
            await conn.close()

    return asyncio.run(_run())
