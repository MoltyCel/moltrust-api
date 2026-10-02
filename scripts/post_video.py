"""Post one native video to X and Bluesky, in that order.

Native, not a link: a video X hosts plays in the timeline, a link to one does
not, and the whole point of the clip is that it is watched without a click.

Two things make this its own script rather than a mode of syndicate.py. The
upload is chunked and asynchronous on both networks — X transcodes and has to be
polled until it says `succeeded`, Bluesky runs its own job queue — and the text
is approved copy, so the voice gate runs in report mode and decides nothing.

    python scripts/post_video.py --probe          # Bluesky upload only, posts nothing
    python scripts/post_video.py --dry-run        # gates and checks, no upload
    python scripts/post_video.py --i-will-publish # the real thing

The breaker is honoured even though posting is exempt from it. A day that has
already overrun is not a day to add two writes and a media upload to by
reflex — the instruction was to abort and say so, not to route around it.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from agents import voice_gate, x_meter, x_post
from app import notify

TEXT = ("AI agents need a mandate. But mandates aren't a guarantee that nothing "
        "happens. They're the record of what was allowed — checkable by anybody "
        "outside the kitchen.")
LINK_REPLY = "moltrust.ch"
ALT = ("Two lobsters on a conveyor belt in a French restaurant kitchen argue "
       "about whether the cook needs a mandate to cook them.")

# Clip 1 of 3. LinkedIn carries the same clip, posted by hand on 02.10.2026 at
# 11:30 CEST; it is recorded as the third channel and never re-syndicated.
LINKEDIN_NOTE = {"channel": "linkedin", "posted_by": "hand",
                 "at": "2026-10-02T09:30:00+00:00", "url": None}

BSKY = "https://bsky.social/xrpc"
VIDEO_SERVICE = "https://video.bsky.app/xrpc"


def gate_report(text: str) -> str:
    """Both gates over approved copy. Report only — it decides nothing here."""
    try:
        result = voice_gate.scan([text], mode="post")
    except Exception as e:
        return f"voice gate unavailable: {type(e).__name__}: {e}"
    return voice_gate.format_report(result)


# ── X ──

def post_to_x(path: str) -> dict:
    mid, seconds = x_post.upload_video(path)
    if not mid:
        return {"ok": False, "detail": "media upload failed", "upload_s": seconds}
    hook = x_post.post(TEXT, media_ids=[mid], kind="video")
    if not hook:
        return {"ok": False, "detail": "the post failed", "upload_s": seconds,
                "media_id": mid}
    reply = x_post.post(LINK_REPLY, reply_to=hook, kind="video-link")
    return {"ok": True, "upload_s": seconds, "media_id": mid,
            "post": hook, "reply": reply,
            "url": f"https://x.com/MolTrust/status/{hook}"}


# ── Bluesky ──

def bsky_login() -> dict | None:
    handle = os.getenv("BLUESKY_HANDLE", "")
    app_pw = os.getenv("BLUESKY_APP_PASSWORD", "")
    if not (handle and app_pw):
        print("Bluesky skipped: BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not set")
        return None
    r = httpx.post(f"{BSKY}/com.atproto.server.createSession",
                   json={"identifier": handle, "password": app_pw}, timeout=30)
    if r.status_code != 200:
        print(f"Bluesky login {r.status_code}: {r.text[:200]}")
        return None
    return r.json()


def bsky_upload_video(sess: dict, path: str) -> tuple[dict | None, float, str]:
    """(blob ref, seconds, which route worked).

    Two routes exist and only one of them is documented for video. uploadBlob is
    tried first because it needs no second token and no job queue; the video
    service is the fallback, and the one that matters if the PDS refuses the
    blob outright.
    """
    started = time.monotonic()
    data = open(path, "rb").read()
    headers = {"Authorization": f"Bearer {sess['accessJwt']}",
               "Content-Type": "video/mp4"}
    r = httpx.post(f"{BSKY}/com.atproto.repo.uploadBlob", headers=headers,
                   content=data, timeout=300)
    if r.status_code == 200:
        blob = r.json().get("blob")
        print(f"  uploadBlob ok, {len(data)} bytes")
        return blob, round(time.monotonic() - started, 1), "uploadBlob"
    print(f"  uploadBlob {r.status_code}: {r.text[:200]}")

    # The video service: a service-auth token for video.bsky.app, then a job to
    # poll. This is the documented path and the one that survives size limits.
    tok = httpx.get(f"{BSKY}/com.atproto.server.getServiceAuth",
                    headers={"Authorization": f"Bearer {sess['accessJwt']}"},
                    params={"aud": f"did:web:{VIDEO_SERVICE.split('//')[1].split('/')[0]}",
                            "lxm": "com.atproto.repo.uploadBlob",
                            "exp": int(time.time()) + 1800}, timeout=30)
    if tok.status_code != 200:
        print(f"  getServiceAuth {tok.status_code}: {tok.text[:200]}")
        return None, round(time.monotonic() - started, 1), "none"
    jwt = tok.json()["token"]
    r = httpx.post(f"{VIDEO_SERVICE}/app.bsky.video.uploadVideo",
                   headers={"Authorization": f"Bearer {jwt}",
                            "Content-Type": "video/mp4"},
                   params={"did": sess["did"], "name": os.path.basename(path)},
                   content=data, timeout=300)
    if r.status_code not in (200, 202):
        print(f"  uploadVideo {r.status_code}: {r.text[:300]}")
        return None, round(time.monotonic() - started, 1), "none"
    job = r.json().get("jobStatus", {})
    job_id = job.get("jobId")
    state = job.get("state", "")
    while state not in ("JOB_STATE_COMPLETED", "JOB_STATE_FAILED"):
        if time.monotonic() - started > 300:
            print("  video job timed out")
            return None, round(time.monotonic() - started, 1), "none"
        time.sleep(3)
        q = httpx.get(f"{VIDEO_SERVICE}/app.bsky.video.getJobStatus",
                      headers={"Authorization": f"Bearer {jwt}"},
                      params={"jobId": job_id}, timeout=30)
        job = q.json().get("jobStatus", {}) if q.status_code == 200 else {}
        state = job.get("state", "JOB_STATE_FAILED")
        print(f"  video job {state} ({job.get('progress', '—')}%)")
    if state != "JOB_STATE_COMPLETED":
        print(f"  video job failed: {job.get('error')} {job.get('message')}")
        return None, round(time.monotonic() - started, 1), "none"
    return job.get("blob"), round(time.monotonic() - started, 1), "videoService"


def bsky_post(sess: dict, blob: dict) -> dict:
    headers = {"Authorization": f"Bearer {sess['accessJwt']}"}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "$type": "app.bsky.feed.post", "text": TEXT, "createdAt": now,
        "embed": {"$type": "app.bsky.embed.video", "video": blob, "alt": ALT,
                  "aspectRatio": {"width": 16, "height": 9}},
    }
    r = httpx.post(f"{BSKY}/com.atproto.repo.createRecord", headers=headers,
                   json={"repo": sess["did"], "collection": "app.bsky.feed.post",
                         "record": record}, timeout=60)
    if r.status_code != 200:
        return {"ok": False, "detail": f"createRecord {r.status_code}: {r.text[:300]}"}
    hook = {"uri": r.json()["uri"], "cid": r.json()["cid"]}

    reply_record = {
        "$type": "app.bsky.feed.post", "text": LINK_REPLY, "createdAt": now,
        "facets": [{"index": {"byteStart": 0, "byteEnd": len(LINK_REPLY)},
                    "features": [{"$type": "app.bsky.richtext.facet#link",
                                  "uri": "https://moltrust.ch"}]}],
        "reply": {"root": hook, "parent": hook},
    }
    r2 = httpx.post(f"{BSKY}/com.atproto.repo.createRecord", headers=headers,
                    json={"repo": sess["did"], "collection": "app.bsky.feed.post",
                          "record": reply_record}, timeout=60)
    reply_uri = r2.json()["uri"] if r2.status_code == 200 else None
    if not reply_uri:
        print(f"  Bluesky link reply {r2.status_code}: {r2.text[:200]}")
    rkey = hook["uri"].rsplit("/", 1)[-1]
    handle = sess.get("handle") or os.getenv("BLUESKY_HANDLE", "moltrust.ch")
    return {"ok": True, "uri": hook["uri"], "reply": reply_uri,
            "url": f"https://bsky.app/profile/{handle}/post/{rkey}"}


# ── the ledger entry that names all three channels ──

def record_video(x: dict, bsky: dict, path: str) -> None:
    row = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "kind": "video", "clip": os.path.basename(path),
        "x": {"post": x.get("post"), "reply": x.get("reply"), "url": x.get("url"),
              "media_id": x.get("media_id"), "upload_s": x.get("upload_s")},
        "bluesky": {"uri": bsky.get("uri"), "reply": bsky.get("reply"),
                    "url": bsky.get("url"), "upload_s": bsky.get("upload_s"),
                    "route": bsky.get("route")},
        "linkedin": LINKEDIN_NOTE,
    }
    out = os.path.expanduser("~/moltstack/data/video_posts.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    os.chmod(out, 0o640)
    print(f"ledger: {out}")


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--i-will-publish", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="Bluesky upload only, to learn which route works")
    a = ap.parse_args(argv)

    if not os.path.exists(a.path):
        print(f"no such file: {a.path}")
        return 2
    size = os.path.getsize(a.path)
    print(f"clip: {a.path}  {size} bytes")

    paused = x_meter.reads_paused()
    before = x_meter.spend()
    print(f"spend today: ${before['usd']:.3f}  ·  breaker: {paused or 'open'}")
    if paused:
        notify.send_telegram(
            f"\U0001f6d1 Video-Post abgebrochen\n\n{paused}\n\n"
            f"Zwei Writes und ein Media-Upload kommen nicht obendrauf. "
            f"Nichts gepostet, nichts umgangen.", channel=notify.ALERTS)
        print("breaker closed — aborting, as instructed")
        return 1

    print("\n--- voice gate, report only (text approved by Lars) ---")
    print(gate_report(TEXT))

    if a.probe:
        sess = bsky_login()
        if not sess:
            return 1
        blob, secs, route = bsky_upload_video(sess, a.path)
        print(f"\nroute: {route}  ·  {secs}s  ·  blob: "
              f"{json.dumps(blob)[:160] if blob else 'none'}")
        return 0 if blob else 1

    if a.dry_run or not a.i_will_publish:
        print("\nDRY RUN — nothing uploaded, nothing posted. "
              "Re-run with --i-will-publish.")
        return 0

    print("\n--- X ---")
    x = post_to_x(a.path)
    print(json.dumps(x, indent=1))
    if not x.get("ok"):
        notify.send_telegram(f"⚠️ Video-Post auf X fehlgeschlagen\n"
                             f"{x.get('detail')}", channel=notify.ALERTS)
        return 1

    print("\n--- Bluesky ---")
    bsky = {"ok": False}
    sess = bsky_login()
    if sess:
        blob, secs, route = bsky_upload_video(sess, a.path)
        if blob:
            bsky = bsky_post(sess, blob)
            bsky.update({"upload_s": secs, "route": route})
        else:
            bsky = {"ok": False, "detail": "no blob", "upload_s": secs,
                    "route": route}
    print(json.dumps(bsky, indent=1))

    record_video(x, bsky, a.path)
    after = x_meter.spend()
    print(f"\nX cost booked: ${after['usd'] - before['usd']:.3f}  ·  "
          f"day total ${after['usd']:.3f}")
    notify.send_telegram(
        f"\U0001f3a5 Lobster-Clip 1/3 ist raus\n\n"
        f"X: {x.get('url')}\nBluesky: {bsky.get('url') or '—'}\n"
        f"LinkedIn: von Hand, 02.10. 11:30 CEST\n\n"
        f"Upload X {x.get('upload_s')}s · Bluesky {bsky.get('upload_s')}s "
        f"({bsky.get('route')})\n"
        f"X-Kosten {after['usd'] - before['usd']:.3f} $ · Tag "
        f"{after['usd']:.3f} $", channel=notify.STATS)
    return 0 if bsky.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
