"""Bluesky video: the service, the gate, and proof that resolves."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "post_video", ROOT / "scripts" / "post_video.py")
pv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pv)

SESS = {"accessJwt": "jwt", "did": "did:plc:x", "handle": "moltrust.ch"}


class R:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._p = payload
        self.text = text or ""

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


def _code_of(func_name: str) -> str:
    """The function's code with its docstring removed.

    Read through ast rather than by filtering lines: the docstring explains why
    uploadBlob is gone, and a line filter counts that explanation as code.
    """
    import ast
    tree = ast.parse((ROOT / "scripts" / "post_video.py").read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == func_name)
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(n) for n in body)


def test_uploadblob_is_not_a_video_route():
    """It returns 200 for a video the pipeline never sees, and the embed dies.

    The name is still in the function, as the lexicon method the upload token is
    minted for — that is a token scope, not an endpoint. What must never come
    back is a request *sent* to com.atproto.repo.uploadBlob, so the assertion is
    on the URLs the function calls, not on the string appearing anywhere in it.
    """
    import re
    code = _code_of("bsky_upload_video")
    called = re.findall(r"httpx\.(?:get|post)\(f?[\'\"]([^\'\"]+)", code)
    assert called, "no HTTP call found — the regex stopped matching, not the code"
    assert not [u for u in called if "uploadBlob" in u], \
        f"uploadBlob is back as an endpoint: {called}"
    assert any("app.bsky.video.uploadVideo" in u for u in called)


def test_each_call_gets_its_own_service_token(monkeypatch):
    """A token minted for uploadVideo is refused by getUploadLimits."""
    asked = []

    def fake_get(url, **kw):
        if "getServiceAuth" in url:
            asked.append((kw.get("params") or {}).get("lxm"))
            return R(200, {"token": "t"})
        return R(200, {"canUpload": True, "remainingDailyVideos": 5})

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    assert pv.bsky_can_upload(SESS)[0] is True
    assert asked == ["app.bsky.video.getUploadLimits"]


def test_an_account_that_may_not_upload_is_refused(monkeypatch):
    def fake_get(url, **kw):
        if "getServiceAuth" in url:
            return R(200, {"token": "t"})
        return R(401, {"canUpload": False, "error": "unconfirmed_email",
                       "message": "Confirm your email address to upload videos"})

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    ok, why = pv.bsky_can_upload(SESS)
    assert not ok
    assert "unconfirmed_email" in why and "Confirm your email" in why


def test_a_failed_job_yields_no_blob(monkeypatch):
    monkeypatch.setattr(pv.httpx, "get", lambda url, **kw: R(
        200, {"token": "t"} if "getServiceAuth" in url
        else {"jobStatus": {"state": "JOB_STATE_FAILED", "error": "bad codec"}}))
    monkeypatch.setattr(pv.httpx, "post", lambda url, **kw: R(
        200, {"jobStatus": {"jobId": "j", "state": "JOB_STATE_FAILED",
                            "error": "bad codec"}}))
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)
    monkeypatch.setattr(pv, "open", lambda p, m="rb": __import__("io").BytesIO(b"x"),
                        raising=False)
    blob, _, route = pv.bsky_upload_video(SESS, "/tmp/x.mp4")
    assert blob is None and route == "none"


# ── a field that exists is not proof ──

def test_a_present_playlist_field_that_404s_is_not_verified(monkeypatch):
    """The failure of 02.10: the field was there and the video was not."""
    def fake_get(url, **kw):
        if "getPosts" in url:
            return R(200, {"posts": [{"embed": {
                "$type": "app.bsky.embed.video#view",
                "playlist": "https://video.bsky.app/watch/x/playlist.m3u8"}}]})
        return R(404, None, "video not found")

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    ok, detail = pv.playlist_resolves("at://did:plc:x/app.bsky.feed.post/a")
    assert not ok
    assert "404" in detail


def test_a_playlist_that_serves_m3u8_is_verified(monkeypatch):
    def fake_get(url, **kw):
        if "getPosts" in url:
            return R(200, {"posts": [{"embed": {
                "$type": "app.bsky.embed.video#view",
                "playlist": "https://video.bsky.app/watch/x/playlist.m3u8"}}]})
        return R(200, None, "#EXTM3U\n#EXT-X-VERSION:3\n")

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    ok, detail = pv.playlist_resolves("at://did:plc:x/app.bsky.feed.post/a")
    assert ok and "200" in detail


def test_a_200_that_is_not_m3u8_is_not_verified(monkeypatch):
    def fake_get(url, **kw):
        if "getPosts" in url:
            return R(200, {"posts": [{"embed": {
                "$type": "app.bsky.embed.video#view", "playlist": "https://x/p"}}]})
        return R(200, None, "<html>an error page</html>")

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    assert pv.playlist_resolves("at://did:plc:x/app.bsky.feed.post/a")[0] is False


def test_a_record_the_appview_has_not_seen_is_not_verified(monkeypatch):
    monkeypatch.setattr(pv.httpx, "get", lambda url, **kw: R(200, {"posts": []}))
    ok, detail = pv.playlist_resolves("at://did:plc:x/app.bsky.feed.post/a")
    assert not ok and "does not have the record" in detail


# ── the final gate: (a) and (b) decide, (c) is watched ──

def _wire(monkeypatch, *, embed_type="app.bsky.embed.video#view",
          playlist_status=200, playlist_body="#EXTM3U\n", thumb_status=200,
          in_feed=False):
    created, deleted, sent = [], [], []

    def fake_get(url, **kw):
        if "getPosts" in url:
            return R(200, {"posts": [{"embed": {
                "$type": embed_type,
                "playlist": "https://v/playlist.m3u8",
                "thumbnail": "https://v/thumb.jpg"}}]})
        if "getAuthorFeed" in url:
            feed = [{"post": {"uri": f"at://d/app.bsky.feed.post/{created[0]}"}}] \
                if (in_feed and created) else []
            return R(200, {"feed": feed})
        if "playlist.m3u8" in url:
            return R(playlist_status, None, playlist_body)
        return R(thumb_status, None, "")

    def fake_post(url, **kw):
        if "createRecord" in url:
            rk = f"rk{len(created) + 1}"
            created.append(rk)
            return R(200, {"uri": f"at://d/app.bsky.feed.post/{rk}", "cid": "c"})
        if "deleteRecord" in url:
            deleted.append((kw.get("json") or {}).get("rkey"))
            return R(200, {})
        return R(200, {})

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    monkeypatch.setattr(pv.httpx, "post", fake_post)
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)
    monkeypatch.setattr(pv.notify, "send_telegram",
                        lambda t, **k: sent.append(t) or True)
    monkeypatch.setattr(pv, "watch_feed",
                        lambda rkey, minutes=10, interval=30:
                        (in_feed, "im Feed nach 30s" if in_feed
                         else "nach 10 min nicht im Feed"))
    return created, deleted, sent


BLOB = {"$type": "blob", "ref": {"$link": "bafy"}, "size": 3065434}


def test_the_link_reply_comes_after_the_video_is_proven(monkeypatch):
    """A reply under a post about to be deleted is the half state we avoid."""
    created, deleted, _ = _wire(monkeypatch, in_feed=True)
    out = pv.post_to_bluesky(SESS, BLOB, 8.8, "videoService")
    assert out["ok"] and len(created) == 2, "post then reply"
    assert deleted == []


def test_a_red_playlist_deletes_the_post_and_never_creates_a_reply(monkeypatch):
    created, deleted, sent = _wire(monkeypatch, playlist_status=404,
                                   playlist_body="video not found")
    out = pv.post_to_bluesky(SESS, BLOB, 8.8, "videoService")
    assert not out["ok"]
    assert len(created) == 1, "a reply was created under a doomed post"
    assert deleted == ["rk1"]
    assert any("(a)/(b) rot" in t for t in sent)


def test_a_200_that_is_not_m3u8_is_red(monkeypatch):
    _wire(monkeypatch, playlist_body="<html>error</html>")
    assert pv.post_to_bluesky(SESS, BLOB, 8.8, "videoService")["ok"] is False


def test_a_wrong_embed_type_is_red(monkeypatch):
    _, deleted, _ = _wire(monkeypatch, embed_type="app.bsky.embed.images#view")
    assert pv.post_to_bluesky(SESS, BLOB, 8.8, "videoService")["ok"] is False
    assert deleted == ["rk1"]


def test_an_absent_feed_warns_and_keeps_the_post(monkeypatch):
    """(c) is no gate: playability is proven, so the post stays."""
    created, deleted, sent = _wire(monkeypatch, in_feed=False)
    out = pv.post_to_bluesky(SESS, BLOB, 8.8, "videoService")
    assert out["ok"] is True and out["in_feed"] is False
    assert deleted == [], "a post was deleted over feed presence"
    assert len(created) == 2, "the reply was still created"
    assert any("nicht im Feed" in t and "bleibt stehen" in t for t in sent)


def test_no_blob_posts_nothing(monkeypatch):
    created, _, _ = _wire(monkeypatch)
    assert pv.post_to_bluesky(SESS, None, 0.0, "none")["ok"] is False
    assert created == []


# ── the ledger: one clip stays one clip ──

NULLED = {
    "at": "2026-10-02T09:47:02+00:00", "kind": "video",
    "clip": "lobster-clip1.mp4",
    "x": {"post": "2105957698328052069", "upload_s": 5.5},
    "bluesky": {"uri": None, "url": None, "reply": None, "route": None,
                "upload_s": 1.6,
                "retracted": {"reason": "video embed unplayable, uploadBlob "
                                        "route, account not video-enabled"}},
    "linkedin": {"channel": "linkedin", "posted_by": "hand"},
}


def test_the_refill_patches_the_row_and_keeps_the_retraction(monkeypatch, tmp_path):
    led = tmp_path / "video_posts.jsonl"
    led.write_text(json.dumps(NULLED) + "\n")
    monkeypatch.setattr(pv.os.path, "expanduser", lambda p: str(led))

    assert pv.patch_bluesky_half("/x/lobster-clip1.mp4", {
        "uri": "at://d/app.bsky.feed.post/new", "reply": "at://d/.../r",
        "url": "https://bsky.app/profile/moltrust.ch/post/new",
        "upload_s": 9.1, "route": "videoService"}) is True

    rows = [json.loads(l) for l in led.read_text().splitlines() if l.strip()]
    assert len(rows) == 1, "a second row would make clip 1 into two clips"
    b = rows[0]["bluesky"]
    assert b["uri"] == "at://d/app.bsky.feed.post/new" and b["route"] == "videoService"
    assert "uploadBlob route" in b["retracted"]["reason"], "the reason was erased"
    assert rows[0]["x"]["post"] == "2105957698328052069", "X was touched"


def test_the_refill_declines_a_row_that_already_has_a_post(monkeypatch, tmp_path):
    led = tmp_path / "video_posts.jsonl"
    done = {**NULLED, "bluesky": {"uri": "at://d/app.bsky.feed.post/live"}}
    led.write_text(json.dumps(done) + "\n")
    monkeypatch.setattr(pv.os.path, "expanduser", lambda p: str(led))
    assert pv.patch_bluesky_half("/x/lobster-clip1.mp4", {"uri": "at://d/second"}) is False
    rows = [json.loads(l) for l in led.read_text().splitlines() if l.strip()]
    assert rows[0]["bluesky"]["uri"] == "at://d/app.bsky.feed.post/live"


def test_the_x_breaker_does_not_stop_a_bluesky_only_run(monkeypatch, tmp_path):
    """A day on X is not a reason to withhold a post on another network."""
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"0" * 10)
    monkeypatch.setattr(pv.x_meter, "reads_paused", lambda: "X reads paused: $1.60")
    monkeypatch.setattr(pv.x_meter, "spend", lambda: {"usd": 1.6})
    monkeypatch.setattr(pv, "gate_report", lambda t: "")
    monkeypatch.setattr(pv, "bsky_login", lambda: SESS)
    monkeypatch.setattr(pv, "bsky_can_upload", lambda s: (True, "ok"))
    monkeypatch.setattr(pv, "bsky_upload_video", lambda s, p: ({"ref": 1}, 9.0, "videoService"))
    monkeypatch.setattr(pv, "post_to_bluesky",
                        lambda s, b, u, r: {"ok": True, "uri": "at://u", "url": "https://u"})
    monkeypatch.setattr(pv, "patch_bluesky_half", lambda p, b: True)
    monkeypatch.setattr(pv.notify, "send_telegram", lambda t, **k: True)
    assert pv.main([str(clip), "--i-will-publish", "--bluesky-only"]) == 0


def test_the_breaker_still_stops_a_run_that_posts_to_x(monkeypatch, tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"0" * 10)
    sent = []
    monkeypatch.setattr(pv.x_meter, "reads_paused", lambda: "X reads paused: $1.60")
    monkeypatch.setattr(pv.x_meter, "spend", lambda: {"usd": 1.6})
    monkeypatch.setattr(pv.notify, "send_telegram", lambda t, **k: sent.append(t) or True)
    monkeypatch.setattr(pv, "post_to_x", lambda p: pytest.fail("X was called"))
    assert pv.main([str(clip), "--i-will-publish"]) == 1
    assert sent and "abgebrochen" in sent[0]


# ── service auth: one audience per call, and 409 is success ──

def _auth_wire(monkeypatch, upload_status=200, upload_body=None):
    asked, up = [], {}

    def fake_get(url, **kw):
        p = kw.get("params") or {}
        if "plc.directory" in url:
            return R(200, {"service": [
                {"type": "AtprotoLabeler", "serviceEndpoint": "https://nope"},
                {"type": "AtprotoPersonalDataServer",
                 "serviceEndpoint": "https://brittlegill.us-west.host.bsky.network"}]})
        if "getServiceAuth" in url:
            asked.append((p.get("lxm"), p.get("aud")))
            return R(200, {"token": f"tok:{p.get('lxm')}"})
        if "getJobStatus" in url:
            return R(200, {"jobStatus": {"jobId": "j", "state": "JOB_STATE_COMPLETED",
                                         "blob": {"$type": "blob", "ref": {"$link": "bafy"}}}})
        return R(404)

    def fake_post(url, **kw):
        if "uploadVideo" in url:
            up["auth"] = (kw.get("headers") or {}).get("Authorization")
            return R(upload_status, upload_body if upload_body is not None
                     else {"jobId": "j", "state": "JOB_STATE_ENCODING"})
        return R(404)

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    monkeypatch.setattr(pv.httpx, "post", fake_post)
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)
    return asked, up


def test_the_upload_token_is_minted_for_the_pds_not_the_video_service(monkeypatch, tmp_path):
    """video.bsky.app relays; the PDS holds the blob and signs for it."""
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"0" * 64)
    asked, up = _auth_wire(monkeypatch)
    blob, _, route = pv.bsky_upload_video(SESS, str(clip))
    assert blob and route == "videoService"
    assert ("com.atproto.repo.uploadBlob",
            "did:web:brittlegill.us-west.host.bsky.network") in asked
    assert up["auth"] == "Bearer tok:com.atproto.repo.uploadBlob"
    assert ("app.bsky.video.getJobStatus", pv.VIDEO_DID) in asked


def test_the_pds_did_comes_from_the_did_document(monkeypatch):
    _auth_wire(monkeypatch)
    assert pv.pds_did("did:plc:x") == "did:web:brittlegill.us-west.host.bsky.network"


def test_409_already_exists_is_success(monkeypatch, tmp_path):
    """Re-uploading a clip after a deleted post is the normal case."""
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"0" * 64)
    _auth_wire(monkeypatch, upload_status=409,
               upload_body={"error": "already_exists", "jobId": "j",
                            "state": "JOB_STATE_COMPLETED",
                            "blob": {"$type": "blob", "ref": {"$link": "bafy"}}})
    blob, _, route = pv.bsky_upload_video(SESS, str(clip))
    assert blob == {"$type": "blob", "ref": {"$link": "bafy"}} and route == "videoService"


def test_an_upload_answering_neither_a_job_nor_a_blob_stops(monkeypatch, tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"0" * 64)
    _auth_wire(monkeypatch, upload_status=409, upload_body={"error": "already_exists"})
    blob, _, route = pv.bsky_upload_video(SESS, str(clip))
    assert blob is None and route == "none"
