"""Shared X (Twitter) client for the posting agents.

One place for OAuth1 auth, single posts, threads and image upload, so herald_v3
and syndicate do not each carry their own copy.

Endpoints (both verified live 2026-09-21 against the @moltrust app credentials):
  POST https://api.twitter.com/2/tweets          — post, optionally as a reply
  POST https://api.x.com/2/media/upload          — image upload, needs media_category
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import time

import requests
from requests_oauthlib import OAuth1

from agents import x_meter

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


def upload_video(path: str, auth: OAuth1 | None = None,
                 chunk_bytes: int = 4 * 1024 * 1024,
                 timeout_s: int = 300) -> tuple[str | None, float]:
    """Upload a video in chunks and wait for X to finish transcoding.

    Returns (media id, seconds taken). An image goes up in one request; a video
    does not — it has to be announced, sent in pieces, finalized, and then
    waited for, because the id is useless until X says `succeeded`. Attaching
    it earlier is how a post ends up with an empty player.

    `media_category=tweet_video` is required. Without it X accepts the bytes
    and then refuses the post.
    """
    auth = auth or get_auth()
    if not auth:
        log.error("X credentials not available")
        return None, 0.0
    try:
        data = open(path, "rb").read()
    except OSError as e:
        log.error(f"cannot read {path}: {e}")
        return None, 0.0

    started = time.monotonic()

    def elapsed() -> float:
        return round(time.monotonic() - started, 1)

    r = requests.post(MEDIA_UPLOAD_URL, auth=auth, timeout=60, data={
        "command": "INIT", "media_type": "video/mp4",
        "media_category": "tweet_video", "total_bytes": len(data)})
    if r.status_code not in (200, 201, 202):
        log.error(f"media INIT {r.status_code}: {r.text[:300]}")
        return None, elapsed()
    mid = (r.json().get("data") or r.json()).get("id") or r.json().get("media_id_string")
    if not mid:
        log.error(f"media INIT returned no id: {r.text[:300]}")
        return None, elapsed()
    log.info(f"media INIT ok, id {mid}, {len(data)} bytes")

    for index, start in enumerate(range(0, len(data), chunk_bytes)):
        piece = data[start:start + chunk_bytes]
        r = requests.post(MEDIA_UPLOAD_URL, auth=auth, timeout=180,
                          data={"command": "APPEND", "media_id": mid,
                                "segment_index": index},
                          files={"media": ("chunk", piece,
                                           "application/octet-stream")})
        if r.status_code not in (200, 201, 202, 204):
            log.error(f"media APPEND {index} {r.status_code}: {r.text[:300]}")
            return None, elapsed()
        log.info(f"  APPEND {index}: {len(piece)} bytes")

    r = requests.post(MEDIA_UPLOAD_URL, auth=auth, timeout=120,
                      data={"command": "FINALIZE", "media_id": mid})
    if r.status_code not in (200, 201, 202):
        log.error(f"media FINALIZE {r.status_code}: {r.text[:300]}")
        return None, elapsed()
    body = r.json()
    info = (body.get("data") or body).get("processing_info") or {}
    state = info.get("state", "succeeded")
    log.info(f"media FINALIZE ok, state {state}")

    # Poll until the transcode finishes. X tells us how long to wait; believing
    # it costs one request per step instead of a fixed sleep.
    while state in ("pending", "in_progress"):
        if time.monotonic() - started > timeout_s:
            log.error(f"media {mid} still {state} after {timeout_s}s")
            return None, elapsed()
        time.sleep(max(1, int(info.get("check_after_secs", 2))))
        r = requests.get(MEDIA_UPLOAD_URL, auth=auth, timeout=60,
                         params={"command": "STATUS", "media_id": mid})
        if r.status_code != 200:
            log.error(f"media STATUS {r.status_code}: {r.text[:300]}")
            return None, elapsed()
        body = r.json()
        info = (body.get("data") or body).get("processing_info") or {}
        state = info.get("state", "succeeded")
        log.info(f"  STATUS {state} ({info.get('progress_percent', '—')}%)")

    if state != "succeeded":
        log.error(f"media {mid} ended in state {state}: {info}")
        return None, elapsed()
    log.info(f"media {mid} succeeded after {elapsed()}s")
    # Booked at the write rate. X's pricing page prices post creation per
    # request and does not price a media upload separately, so this is the
    # nearest documented figure rather than a known one — it is in the ledger
    # under its own source so the assumption stays visible.
    x_meter.record_write(mid, "", source="media-upload")
    return mid, elapsed()


LEDGER = os.path.join(os.path.expanduser("~/moltstack/data"), "x_posts.jsonl")


def record(tweet_id: str, kind: str, reply_to: str | None) -> None:
    """One line per posted tweet, so the weekly count can be true.

    Every path to X goes through this module, so this is the one place that
    knows what was posted and which agent asked for it. The Sunday report used
    to count the timeline, which cannot tell a digest from a syndication thread
    part and counts each reply as a post.

    Best-effort: a ledger that fails must never cost a post that succeeded.
    """
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a") as f:
            f.write(json.dumps({
                "id": tweet_id, "kind": kind, "reply_to": reply_to,
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, sort_keys=True) + "\n")
        os.chmod(LEDGER, 0o640)
    except Exception as e:
        log.warning(f"ledger write failed: {type(e).__name__}: {e}")


def post(text: str, reply_to: str | None = None, media_ids: list[str] | None = None,
         auth: OAuth1 | None = None, kind: str = "unlabelled") -> str | None:
    """Post one tweet. Returns its id, or None on failure.

    `kind` is what this post is — digest, proof, syndication, reply. It is the
    only thing the timeline cannot tell us afterwards.
    """
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
        record(tid, kind, reply_to)
        # A post carrying a URL costs $0.20 against $0.015 — forty times — so
        # the meter has to see the text, not just that something was posted.
        x_meter.record_write(tid, text, source=kind)
        return tid
    log.error(f"X API {r.status_code}: {r.text[:300]}")
    return None


def post_thread(parts: list[str], media_ids_first: list[str] | None = None,
                auth: OAuth1 | None = None, kind: str = "unlabelled") -> list[str]:
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
                   media_ids=media_ids_first if i == 0 else None, auth=auth,
                   kind=kind if i == 0 else f"{kind}-part")
        if not tid:
            log.error(f"Thread stopped at part {i + 1}/{len(parts)}")
            break
        ids.append(tid)
        reply_to = tid
    return ids
