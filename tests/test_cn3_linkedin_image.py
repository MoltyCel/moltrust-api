"""CN3: one fixed image for every LinkedIn post.

Herkunft: Lars, Chat 03.11.2026 (ops CN3). Two files Lars supplied on
04.11.2026, kept unchanged in docs/assets-src/social/; every LinkedIn draft
carries one of them, and no image is made per post.

  K2  both files with exactly these names, sizes and sha256
  K3  every draft carries one of the two; the share posts it
"""
import hashlib
import pathlib
import struct

import pytest

from agents import linkedin_post as lp
from agents import reply_radar as rr

SOCIAL = pathlib.Path(__file__).resolve().parents[1] / "docs" / "assets-src" / "social"
# sha256 of the files Lars handed over (ops CN3, Stand 2026-11-04).
EXPECTED = {
    "moltrust-linkedin-1200x627.png": (
        (1200, 627), "db3f2a6c905d2675cffd670bd56ca3c604c487b6b6a70586a312d5b3d14e2502"),
    "moltrust-linkedin-1200x1200.png": (
        (1200, 1200), "8961fdf98ee4da2c7930f8f122d0fdce430a3def87e54b623773db0d24de27d8"),
}


def png_size(path: pathlib.Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR"
    return struct.unpack(">II", head[16:24])


# ── K2 ──

@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixed_image_is_unchanged(name):
    size, digest = EXPECTED[name]
    path = SOCIAL / name
    assert png_size(path) == size
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_only_the_two_fixed_images_exist():
    assert set(lp.IMAGES) == set(EXPECTED)
    assert {p.name for p in SOCIAL.glob("*.png")} == set(EXPECTED)
    with pytest.raises(ValueError):
        lp.image_path("linkedin-cover-b.png")


# ── K3: every draft carries one ──

def test_a_draft_carries_the_text_post_image(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    lp.remember("k1", "text", {"title": "T", "link": "https://moltrust.ch/blog/a.html"})
    assert lp.load_pending()["k1"]["image"] == "moltrust-linkedin-1200x627.png"


def test_a_square_draft_carries_the_square_image(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    lp.remember("k2", "text", {"title": "T", "link": "x", "image_format": "square"})
    assert lp.load_pending()["k2"]["image"] == "moltrust-linkedin-1200x1200.png"


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = ""

    def json(self):
        return self._body


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    monkeypatch.setattr(lp.oauth, "load_tokens", lambda: {
        "access_token": "t", "author_urn": "urn:li:person:XYZ"})
    calls = []

    def fake_post(url, json=None, **k):
        calls.append(("POST", url, json))
        if url == lp.REGISTER_UPLOAD:
            return Resp(200, {"value": {
                "uploadMechanism": {lp.UPLOAD_MECHANISM: {
                    "headers": {}, "uploadUrl": "https://api.linkedin.com/mediaUpload/abc"}},
                "asset": "urn:li:digitalmediaAsset:ABC"}})
        return Resp(201, headers={"X-RestLi-Id": "urn:li:share:9"})

    def fake_put(url, content=None, **k):
        calls.append(("PUT", url, content))
        return Resp(201)

    monkeypatch.setattr(lp.httpx, "post", fake_post)
    monkeypatch.setattr(lp.httpx, "put", fake_put)
    return calls


def test_the_share_uploads_the_fixed_file_and_attaches_it(api):
    out = lp.post_share("Hello", image="moltrust-linkedin-1200x627.png")
    assert [c[0] for c in api] == ["POST", "PUT", "POST"]
    reg = api[0][2]["registerUploadRequest"]
    assert reg["recipes"] == ["urn:li:digitalmediaRecipe:feedshare-image"]
    assert reg["owner"] == "urn:li:person:XYZ"
    assert api[1][2] == (SOCIAL / "moltrust-linkedin-1200x627.png").read_bytes()
    content = api[2][2]["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert content["shareMediaCategory"] == "IMAGE"
    assert content["media"] == [{"status": "READY", "media": "urn:li:digitalmediaAsset:ABC"}]
    assert out["image"] == "moltrust-linkedin-1200x627.png"


def test_a_failed_upload_posts_nothing(api, monkeypatch):
    monkeypatch.setattr(lp.httpx, "put", lambda *a, **k: Resp(500))
    with pytest.raises(RuntimeError, match="Bild-Upload"):
        lp.post_share("Hello", image="moltrust-linkedin-1200x627.png")
    assert all(c[1] != lp.UGC_POSTS for c in api)


@pytest.mark.parametrize("entry_image, expected", [
    ("moltrust-linkedin-1200x1200.png", "moltrust-linkedin-1200x1200.png"),
    (None, "moltrust-linkedin-1200x627.png"),   # parked before CN3
])
def test_the_button_posts_with_the_drafts_image(monkeypatch, tmp_path, entry_image, expected):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    entry = {"text": "t", "title": "T"}
    if entry_image:
        entry["image"] = entry_image
    lp.save_pending({"k1": entry})
    seen = {}

    def fake_share(text, author_urn=None, image=None):
        seen["image"] = image
        return {"urn": "urn:li:share:1", "url": "u", "author": "a",
                "visibility": "PUBLIC", "image": image}

    monkeypatch.setattr(lp, "post_share", fake_share)
    monkeypatch.setattr(lp, "record", lambda *a, **k: None)
    monkeypatch.setattr(rr, "edit_message", lambda *a, **k: None)
    rr.handle_linkedin({"message": {"chat": {"id": 1}, "message_id": 2}}, "li|post|k1", False)
    assert seen["image"] == expected
