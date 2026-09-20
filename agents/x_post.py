"""Shared X (Twitter) client for the posting agents.

One place for OAuth1 auth, single posts, threads and image upload, so herald_v3
and syndicate do not each carry their own copy.

Endpoints (both verified live 2026-09-21 against the @moltrust app credentials):
  POST https://api.twitter.com/2/tweets          — post, optionally as a reply
  POST https://api.x.com/2/media/upload          — image upload, needs media_category
"""
from __future__ import annotations

import logging
import os

import requests
from requests_oauthlib import OAuth1

TWEET_URL = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_URL = "https://api.x.com/2/media/upload"
TWEET_LIMIT = 280

log = logging.getLogger("x_post")


def get_auth() -> OAuth1 | None:
    """OAuth1 from the X_* env vars, or None when any of the four is missing."""
    ck = os.getenv("X_CONSUMER_KEY", "")
    cs = os.getenv("X_CONSUMER_SECRET", "")
    at = os.getenv("X_ACCESS_TOKEN", "")
    asec = os.getenv("X_ACCESS_SECRET", "")
    if not all([ck, cs, at, asec]):
        return None
    return OAuth1(ck, cs, at, asec)


def upload_image(png_bytes: bytes, auth: OAuth1 | None = None) -> str | None:
    """Upload a PNG and return its media id, or None on failure.

    The media id is unattached until a post references it and expires after
    24h, so a failed post leaves nothing behind.
    """
    auth = auth or get_auth()
    if not auth:
        log.error("X credentials not available")
        return None
    try:
        r = requests.post(
            MEDIA_UPLOAD_URL,
            files={"media": ("card.png", png_bytes, "image/png")},
            data={"media_category": "tweet_image"},
            auth=auth,
            timeout=60,
        )
    except Exception as e:
        log.error(f"Media upload failed: {e}")
        return None
    if r.status_code in (200, 201):
        mid = r.json().get("data", {}).get("id")
        log.info(f"Uploaded image, media id {mid}")
        return mid
    log.error(f"Media upload {r.status_code}: {r.text[:300]}")
    return None


def post(text: str, reply_to: str | None = None, media_ids: list[str] | None = None,
         auth: OAuth1 | None = None) -> str | None:
    """Post one tweet. Returns its id, or None on failure."""
    auth = auth or get_auth()
    if not auth:
        log.error("X credentials not available")
        return None

    if len(text) > TWEET_LIMIT:
        text = text[: TWEET_LIMIT - 3] + "..."

    payload: dict = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}
    if media_ids:
        payload["media"] = {"media_ids": media_ids}

    try:
        r = requests.post(TWEET_URL, json=payload, auth=auth, timeout=20)
    except Exception as e:
        log.error(f"Post failed: {e}")
        return None
    if r.status_code in (200, 201):
        tid = r.json()["data"]["id"]
        log.info(f"POSTED to X! Tweet ID: {tid}")
        return tid
    log.error(f"X API {r.status_code}: {r.text[:300]}")
    return None


def post_thread(parts: list[str], media_ids_first: list[str] | None = None,
                auth: OAuth1 | None = None) -> list[str]:
    """Post parts as a reply chain. Returns the ids actually posted.

    A failure stops the chain: a half-posted thread is visible and is reported
    by the caller rather than retried, because a retry would duplicate the
    tweets already up.
    """
    auth = auth or get_auth()
    ids: list[str] = []
    reply_to = None
    for i, part in enumerate(parts):
        tid = post(part, reply_to=reply_to,
                   media_ids=media_ids_first if i == 0 else None, auth=auth)
        if not tid:
            log.error(f"Thread stopped at part {i + 1}/{len(parts)}")
            break
        ids.append(tid)
        reply_to = tid
    return ids
