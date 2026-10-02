"""Bluesky video: the service, the gate, and proof that resolves."""
import importlib.util
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

    The assertion is about the request URL, not about the string: the token
    minted for uploadVideo has to name com.atproto.repo.uploadBlob as the
    method it authorises, because the video service performs that write on our
    behalf. A bare mention is correct; a slash in front of it is the old route.
    """
    import re
    code = _code_of("bsky_upload_video")
    assert not re.search(r"/com\.atproto\.repo\.uploadBlob", code), \
        "uploadBlob is back as the upload URL"
    assert "app.bsky.video.uploadVideo" in code
    assert "com.atproto.repo.uploadBlob" in code, \
        "the token must still authorise the PDS write"


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


def test_the_upload_token_is_minted_for_the_pds_not_the_video_service(monkeypatch):
    """uploadVideo: 'invalid token audience "did:web:video.bsky.app", should be
    the user's PDS DID'. The host differs per account, so it is resolved."""
    seen = {}

    def fake_get(url, **kw):
        if "plc.directory" in url:
            return R(200, {"service": [
                {"type": "AtprotoPersonalDataServer",
                 "serviceEndpoint": "https://brittlegill.us-west.host.bsky.network"}]})
        if "getServiceAuth" in url:
            seen["aud"] = (kw.get("params") or {}).get("aud")
            seen["lxm"] = (kw.get("params") or {}).get("lxm")
            return R(200, {"token": "t"})
        return R(200, {"jobStatus": {"state": "JOB_STATE_FAILED"}})

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    monkeypatch.setattr(pv.httpx, "post", lambda url, **kw: R(
        200, {"jobStatus": {"jobId": "j", "state": "JOB_STATE_FAILED"}}))
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)
    monkeypatch.setattr(pv, "open",
                        lambda p, m="rb": __import__("io").BytesIO(b"x"),
                        raising=False)
    pv.bsky_upload_video(SESS, "/tmp/x.mp4")
    assert seen["aud"] == "did:web:brittlegill.us-west.host.bsky.network"
    # The token authorises the PDS write the video service performs on our
    # behalf, not the video call itself.
    assert seen["lxm"] == "com.atproto.repo.uploadBlob"


def test_an_unresolvable_pds_stops_before_the_upload(monkeypatch):
    monkeypatch.setattr(pv.httpx, "get", lambda url, **kw: R(404, None, "nope"))

    def boom(*a, **k):
        raise AssertionError("uploaded without an audience")

    monkeypatch.setattr(pv.httpx, "post", boom)
    blob, _, route = pv.bsky_upload_video(SESS, "/tmp/x.mp4")
    assert blob is None and route == "none"


def test_getuploadlimits_still_uses_the_video_service_audience(monkeypatch):
    """The two calls want different audiences; only uploadVideo wants the PDS."""
    seen = {}

    def fake_get(url, **kw):
        if "getServiceAuth" in url:
            seen["aud"] = (kw.get("params") or {}).get("aud")
            return R(200, {"token": "t"})
        return R(200, {"canUpload": True})

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    pv.bsky_can_upload(SESS)
    assert seen["aud"] == "did:web:video.bsky.app"
