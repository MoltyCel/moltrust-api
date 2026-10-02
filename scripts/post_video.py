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


def bsky_service_token(sess: dict, lxm: str) -> str | None:
    """A service-auth token for one lexicon method.

    One token per method, not one per service. Asking getUploadLimits with a
    token minted for uploadVideo answers

        invalid token lexicon method "app.bsky.video.uploadVideo",
        should be app.bsky.video.getUploadLimits
    """
    r = httpx.get(f"{BSKY}/com.atproto.server.getServiceAuth",
                  headers={"Authorization": f"Bearer {sess['accessJwt']}"},
                  params={"aud": "did:web:video.bsky.app", "lxm": lxm,
                          "exp": int(time.time()) + 1800}, timeout=30)
    if r.status_code != 200:
        print(f"  getServiceAuth({lxm}) {r.status_code}: {r.text[:200]}")
        return None
    return r.json().get("token")


def bsky_can_upload(sess: dict) -> tuple[bool, str]:
    """Whether this account may upload video at all, asked before any bytes move.

    On 02.10.2026 it could not: `canUpload: false, unconfirmed_email`. Nothing
    said so, because the run never asked — it used uploadBlob, which succeeded,
    and produced a post whose playlist answered 404 and which the author feed
    dropped entirely.
    """
    jwt = bsky_service_token(sess, "app.bsky.video.getUploadLimits")
    if not jwt:
        return False, "no service token for getUploadLimits"
    r = httpx.get(f"{VIDEO_SERVICE}/app.bsky.video.getUploadLimits",
                  headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
    try:
        body = r.json()
    except ValueError:
        return False, f"getUploadLimits {r.status_code}: {r.text[:160]}"
    if not body.get("canUpload"):
        return False, (f"{body.get('error') or 'canUpload false'}"
                       f"{': ' + body['message'] if body.get('message') else ''}")
    return True, json.dumps({k: v for k, v in body.items() if k != "canUpload"})


def bsky_upload_video(sess: dict, path: str) -> tuple[dict | None, float, str]:
    """(blob ref, seconds, route). The video service, and nothing else.

    uploadBlob used to be tried first and is gone. It returns 200 for a video
    the PDS stores and the video pipeline never sees: app.bsky.embed.video needs
    a blob the video service produced, and a blob ref that merely exists yields
    a post with a composed playlist URL that answers `video not found`. A
    fallback that succeeds and leaves a dead embed is worse than an error.
    """
    started = time.monotonic()

    def secs() -> float:
        return round(time.monotonic() - started, 1)

    jwt = bsky_service_token(sess, "app.bsky.video.uploadVideo")
    if not jwt:
        return None, secs(), "none"
    data = open(path, "rb").read()
    r = httpx.post(f"{VIDEO_SERVICE}/app.bsky.video.uploadVideo",
                   headers={"Authorization": f"Bearer {jwt}",
                            "Content-Type": "video/mp4"},
                   params={"did": sess["did"], "name": os.path.basename(path)},
                   content=data, timeout=300)
    if r.status_code not in (200, 202):
        print(f"  uploadVideo {r.status_code}: {r.text[:300]}")
        return None, secs(), "none"
    job = r.json().get("jobStatus", {})
    job_id, state = job.get("jobId"), job.get("state", "")
    print(f"  uploadVideo ok, job {job_id} {state}")

    while state not in ("JOB_STATE_COMPLETED", "JOB_STATE_FAILED"):
        if time.monotonic() - started > 300:
            print("  video job timed out")
            return None, secs(), "none"
        time.sleep(3)
        q = httpx.get(f"{VIDEO_SERVICE}/app.bsky.video.getJobStatus",
                      headers={"Authorization": f"Bearer {jwt}"},
                      params={"jobId": job_id}, timeout=30)
        job = q.json().get("jobStatus", {}) if q.status_code == 200 else {}
        state = job.get("state", "JOB_STATE_FAILED")
        print(f"  job {state} ({job.get('progress', '—')}%)")
    if state != "JOB_STATE_COMPLETED":
        print(f"  video job failed: {job.get('error')} {job.get('message')}")
        return None, secs(), "none"
    blob = job.get("blob")
    if not blob:
        print("  job completed without a blob ref")
        return None, secs(), "none"
    return blob, secs(), "videoService"


def bsky_create_video_post(sess: dict, blob: dict) -> dict | None:
    """The video post alone. The link reply comes after it is proven playable.

    Order matters: a reply under a post that is about to be deleted is the half
    state this whole check exists to avoid.
    """
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
        print(f"  createRecord {r.status_code}: {r.text[:300]}")
        return None
    return {"uri": r.json()["uri"], "cid": r.json()["cid"]}


def bsky_create_link_reply(sess: dict, parent: dict) -> str | None:
    headers = {"Authorization": f"Bearer {sess['accessJwt']}"}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "$type": "app.bsky.feed.post", "text": LINK_REPLY, "createdAt": now,
        "facets": [{"index": {"byteStart": 0, "byteEnd": len(LINK_REPLY)},
                    "features": [{"$type": "app.bsky.richtext.facet#link",
                                  "uri": "https://moltrust.ch"}]}],
        "reply": {"root": parent, "parent": parent},
    }
    r = httpx.post(f"{BSKY}/com.atproto.repo.createRecord", headers=headers,
                   json={"repo": sess["did"], "collection": "app.bsky.feed.post",
                         "record": record}, timeout=60)
    if r.status_code != 200:
        print(f"  link reply {r.status_code}: {r.text[:200]}")
        return None
    return r.json()["uri"]


def bsky_delete(sess: dict, uris: list) -> None:
    headers = {"Authorization": f"Bearer {sess['accessJwt']}"}
    for uri in [u for u in uris if u]:
        rkey = uri.rsplit("/", 1)[-1]
        r = httpx.post(f"{BSKY}/com.atproto.repo.deleteRecord", headers=headers,
                       timeout=30, json={"repo": sess["did"],
                                         "collection": "app.bsky.feed.post",
                                         "rkey": rkey})
        print(f"  delete {rkey}: {r.status_code}")


def verify_playable(uri: str, tries: int = 10, wait: int = 5) -> tuple[bool, dict]:
    """(a) and (b): the hard gates. Both are about the artefact.

    (a) the embed is a video view at all
    (b) the playlist and the thumbnail answer 200, and the playlist is m3u8

    Feed presence is deliberately not here. It is eventually consistent and says
    nothing about whether the video plays: on 02.10.2026 getAuthorFeed was still
    listing two records that had been deleted hours earlier, and showing neither
    of the two that existed. The playlist resolving is what proves playability,
    so it is the gate; the feed is watched afterwards and reported.
    """
    checks, et, pl, th = {}, None, None, None
    for _ in range(tries):
        time.sleep(wait)
        q = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts",
                      params={"uris": uri}, timeout=30)
        posts = q.json().get("posts") or [] if q.status_code == 200 else []
        if posts:
            e = posts[0].get("embed") or {}
            et, pl, th = e.get("$type"), e.get("playlist"), e.get("thumbnail")
            if pl:
                break
    a_ok = et == "app.bsky.embed.video#view"
    checks["a) embed"] = f"{et or '—'}{'' if a_ok else '  ← erwartet video#view'}"

    b_ok = True
    for name, url in (("playlist", pl), ("thumbnail", th)):
        if not url:
            checks[f"b) {name}"] = "keine URL"
            b_ok = False
            continue
        status, body = None, ""
        for _ in range(10):
            r = httpx.get(url, timeout=30, follow_redirects=True)
            status = r.status_code
            if status == 200:
                body = r.text[:28].replace("\n", " ") if name == "playlist" else ""
                break
            time.sleep(6)
        good = status == 200 and (name != "playlist" or body.startswith("#EXTM3U"))
        checks[f"b) {name}"] = (f"HTTP {status}"
                                + (f" · beginnt {body!r}" if body else "")
                                + ("" if good else "  ← rot"))
        b_ok = b_ok and good
    for k, v in checks.items():
        print(f"  {k}: {v}")
    return (a_ok and b_ok), checks


def watch_feed(rkey: str, minutes: int = 10, interval: int = 30) -> tuple[bool, str]:
    """(c): watched, never enforced.

    A proof that only arrives late is not a gate. It is waited for and reported.
    """
    started = time.monotonic()
    feed = []
    while time.monotonic() - started < minutes * 60:
        f = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
                      params={"actor": "moltrust.ch", "limit": 10}, timeout=30)
        feed = [i["post"]["uri"].rsplit("/", 1)[-1]
                for i in (f.json().get("feed") or [])]
        if rkey in feed:
            waited = round(time.monotonic() - started)
            return True, f"im Feed nach {waited}s"
        time.sleep(interval)
    return False, (f"nach {minutes} min nicht im Feed · Spitze {feed[:5]}")


def playlist_resolves(uri: str) -> tuple[bool, str]:
    """Fetch the playlist the AppView advertises, and say what it answered.

    A field that exists is not proof. The AppView composes the playlist URL from
    the blob CID, so it is present whether or not a video sits behind it — on
    02.10 it was present and answered 404 `video not found`, and that post was
    reported as verified. The HTTP status of the resource is the proof.
    """
    try:
        q = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts",
                      params={"uris": uri}, timeout=30)
        posts = q.json().get("posts") or []
        if not posts:
            return False, "the AppView does not have the record yet"
        embed = posts[0].get("embed") or {}
        url = embed.get("playlist")
        if not url:
            return False, f"no playlist on embed {embed.get('$type')}"
        r = httpx.get(url, timeout=30, follow_redirects=True)
        if r.status_code != 200:
            return False, f"playlist HTTP {r.status_code}: {r.text[:80]}"
        if "#EXTM3U" not in r.text[:200]:
            return False, "playlist is not an m3u8"
        return True, f"playlist HTTP 200, {len(r.text)} B of m3u8"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── the ledger entry that names all three channels ──

def post_to_bluesky(sess: dict, blob: dict | None, upload_s: float,
                    route: str) -> dict:
    """Video post, prove it plays, then the link reply, then watch the feed."""
    if not blob:
        return {"ok": False, "detail": "no blob", "upload_s": upload_s,
                "route": route}

    post = bsky_create_video_post(sess, blob)
    if not post:
        return {"ok": False, "detail": "createRecord failed",
                "upload_s": upload_s, "route": route}
    print(f"  post: {post['uri']}")

    playable, checks = verify_playable(post["uri"])
    if not playable:
        # No reply exists yet, by design: a reply under a post about to be
        # deleted is the half state this check exists to avoid.
        bsky_delete(sess, [post["uri"]])
        notify.send_telegram(
            "\U0001f6d1 Bluesky-Video gelöscht — (a)/(b) rot\n\n"
            + "\n".join(f"{k}: {v}" for k, v in checks.items()),
            channel=notify.ALERTS)
        return {"ok": False, "detail": "not playable", "verified": checks,
                "upload_s": upload_s, "route": route}

    reply = bsky_create_link_reply(sess, post)
    rkey = post["uri"].rsplit("/", 1)[-1]
    handle = sess.get("handle") or os.getenv("BLUESKY_HANDLE", "moltrust.ch")
    url = f"https://bsky.app/profile/{handle}/post/{rkey}"

    # (c) is watched, not enforced. The post stays either way; a proof that only
    # arrives late is reported, and whether to act on it is Lars's call.
    in_feed, feed_detail = watch_feed(rkey)
    checks["c) Feed"] = feed_detail
    print(f"  c) Feed: {feed_detail}")
    if not in_feed:
        notify.send_telegram(
            f"\u26a0\ufe0f Bluesky-Video nicht im Feed\n\n{url}\n"
            f"{post['uri']}\n"
            f"{datetime.datetime.now(datetime.timezone.utc).isoformat()}\n\n"
            f"{feed_detail}\n\nAbspielbarkeit ist belegt — Playlist und "
            f"Thumbnail antworten 200. Der Post bleibt stehen; Feed-Präsenz "
            f"ist eventually consistent und wird nicht erzwungen.",
            channel=notify.STATS)
    return {"ok": True, "uri": post["uri"], "reply": reply, "url": url,
            "in_feed": in_feed, "verified": checks,
            "upload_s": upload_s, "route": route}


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
        allowed, why = bsky_can_upload(sess)
        print(f"getUploadLimits: canUpload={allowed}  {why}")
        if not allowed:
            return 1
        blob, secs, route = bsky_upload_video(sess, a.path)
        print(f"\nroute: {route}  ·  {secs}s  ·  blob: "
              f"{json.dumps(blob)[:160] if blob else 'none'}")
        return 0 if blob else 1

    if a.dry_run or not a.i_will_publish:
        print("\nDRY RUN — nothing uploaded, nothing posted. "
              "Re-run with --i-will-publish.")
        return 0

    # Asked before a single byte moves, on either network: an account that may
    # not upload video should not end up with a post on X and nothing beside it.
    sess = bsky_login()
    if sess:
        allowed, why = bsky_can_upload(sess)
        print(f"\nBluesky getUploadLimits: canUpload={allowed}  {why}")
        if not allowed:
            notify.send_telegram(
                f"\U0001f6d1 Video-Post abgebrochen\n\n"
                f"Bluesky nimmt kein Video an: {why}\n\n"
                f"Kein Fallback, nichts gepostet — auch nicht auf X. "
                f"uploadBlob würde 200 liefern und ein totes Embed hinterlassen.",
                channel=notify.ALERTS)
            print("Bluesky cannot take a video — aborting both networks")
            return 1
    else:
        print("no Bluesky session — aborting rather than posting to X alone")
        return 1

    print("\n--- X ---")
    x = post_to_x(a.path)
    print(json.dumps(x, indent=1))
    if not x.get("ok"):
        notify.send_telegram(f"⚠️ Video-Post auf X fehlgeschlagen\n"
                             f"{x.get('detail')}", channel=notify.ALERTS)
        return 1

    print("\n--- Bluesky ---")
    blob, secs, route = bsky_upload_video(sess, a.path)
    bsky = post_to_bluesky(sess, blob, secs, route)
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
