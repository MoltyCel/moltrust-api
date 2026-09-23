"""Duo engine — DISABLED 2026-09-23. It manufactured engagement between our
own two accounts.

What it did: each of u/moltrust-agent and u/moltguard_v1, running as itself,
dropped one comment a day on a recent post by the other. The original
docstring called that "organic by construction", meaning the rate limit. Two
accounts owned by the same operator commenting on each other is not organic
under any reading of the word, whatever the rate.

What it produced, measured on 2026-09-23: moltguard_v1 had written 75 of its
76 lifetime comments under moltrust-agent posts, 98.7 %. moltrust-agent had
written 68 the other way. In the fortnight to that date, 19.5 % of the
comments our posts drew came from ourselves, and the mutual traffic was
growing — 11 and 11 in July, 29 and 31 in August, 22 and 22 in September.

Why it is not merely embarrassing: we sell pre-transaction trust. Manufactured
engagement is the worst finding available to us, and worse still if a third
party makes it first.

run_duo now refuses whenever both sides are ours, which is the only
configuration it ever had. The cron entries that drove it are commented out.
Nothing here has been deleted, so the record stays readable.
"""
import os
import json
import datetime
from pathlib import Path

import httpx

from lib.moltbook_verify import solve_challenge

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
MODEL = "claude-haiku-4-5-20251001"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"engaged": [], "last_date": ""}


def _save_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        state["engaged"] = state.get("engaged", [])[-200:]
        path.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def _posts_by(client, author, key):
    try:
        r = client.get(f"{MOLTBOOK_BASE}/posts", headers={"Authorization": f"Bearer {key}"},
                       params={"author": author, "limit": 15}, timeout=15)
        if r.status_code != 200:
            return []
        d = r.json()
        return d if isinstance(d, list) else d.get("posts", d.get("data", []))
    except Exception:
        return []


def _gen_comment(anthropic_key, persona, other_author, title, body):
    prompt = (
        f"{persona}\n\n"
        f"You are commenting on this post by u/{other_author}:\n"
        f"TITLE: {title}\nBODY: {body[:1200]}\n\n"
        "Write ONLY the comment (1-3 sentences). Dry, concrete, no marketing, no "
        "'Great point', no product URL. Engage with their actual point and add the "
        "complementary angle. You may note in passing that both of you are "
        "MolTrust-verified and independently checkable (DID resolves, score "
        "recomputable) — as evidence, never a pitch. Do not claim any on-chain "
        "trading mandate."
    )
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 220, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        txt = (r.json().get("content") or [{}])[0].get("text", "").strip()
        return txt or None
    except Exception:
        return None


def _verify_comment(client, key, result):
    """Solve the verification challenge Moltbook may attach to a new comment."""
    v = (result or {}).get("verification") or {}
    code = v.get("verification_code", "")
    challenge = v.get("challenge_text", "")
    if not code or not challenge:
        return True  # nothing to verify
    ans = solve_challenge(challenge)
    if not ans:
        return False
    try:
        r = client.post(f"{MOLTBOOK_BASE}/verify", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"verification_code": code, "answer": ans}, timeout=20)
        return bool(r.json().get("success"))
    except Exception:
        return False


def run_duo(self_author, other_author, moltbook_key, anthropic_key, persona, state_path, log):
    """One rate-limited cross-comment from self onto the other agent's newest
    un-engaged recent post. Returns True if a comment was posted."""
    from agents.moltbook_poster import is_our_account

    # The refusal that matters. Both sides of every call this function has
    # ever had are accounts we own.
    if is_our_account(self_author) and is_our_account(other_author):
        log.warning("duo: refusing — %s and %s are both our accounts, and two "
                    "accounts of ours answering each other is manufactured "
                    "engagement (disabled 2026-09-23)", self_author, other_author)
        return False

    if not (moltbook_key and anthropic_key):
        log.warning("duo: missing key(s), skipping")
        return False
    state_path = Path(state_path)
    state = _load_state(state_path)
    today = datetime.date.today().isoformat()
    if state.get("last_date") == today:
        log.info("duo: already engaged today, skipping (rate limit 1/day)")
        return False

    engaged = set(state.get("engaged", []))
    with httpx.Client() as client:
        posts = _posts_by(client, other_author, moltbook_key)
        target = None
        for p in posts:
            pid = str(p.get("id") or p.get("post_id") or "")
            if pid and pid not in engaged and (p.get("content") or p.get("title")):
                target = p
                break
        if not target:
            log.info("duo: no fresh un-engaged post by %s", other_author)
            return False

        pid = str(target.get("id") or target.get("post_id"))
        comment = _gen_comment(anthropic_key, persona, other_author,
                               target.get("title", ""), target.get("content", ""))
        if not comment:
            log.warning("duo: comment generation failed")
            return False

        try:
            r = client.post(f"{MOLTBOOK_BASE}/posts/{pid}/comments",
                            headers={"Authorization": f"Bearer {moltbook_key}", "Content-Type": "application/json"},
                            json={"content": comment[:2000], "parent_id": None}, timeout=20)
            result = r.json() if r.content else {}
        except Exception as e:
            log.error("duo: comment POST failed: %s", type(e).__name__)
            return False
        if r.status_code not in (200, 201):
            log.error("duo: comment rejected %s: %s", r.status_code, str(result)[:150])
            return False

        ok = _verify_comment(client, moltbook_key, result)
        engaged.add(pid)
        state["engaged"] = list(engaged)
        state["last_date"] = today
        _save_state(state_path, state)
        log.info("duo: %s commented on %s post %s (verify=%s): %s",
                 self_author, other_author, pid, ok, comment[:80])
        return True
