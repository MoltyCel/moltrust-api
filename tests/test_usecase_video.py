"""scripts/usecase_video (ops UC-VID): the spec keeps its limits, the payloads
are what HeyGen and YouTube take, and nothing secret is printed. No network."""
import base64
import json
import pathlib
import sys

import pytest

DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "usecase_video"
sys.path.insert(0, str(DIR))

import heygen_make  # noqa: E402
import scenes  # noqa: E402
import spec_tools  # noqa: E402
import youtube_auth  # noqa: E402
import youtube_upload  # noqa: E402

SPEC = spec_tools.load()
CFG = heygen_make.load_config()


def test_the_spec_has_no_problems_and_sixteen_items():
    assert spec_tools.problems(SPEC) == []
    assert len(spec_tools.items(SPEC)) == 16


@pytest.mark.parametrize("uc,fmt,lang", spec_tools.items(SPEC),
                         ids=lambda x: x if isinstance(x, str) else x["slug"])
def test_every_item_fits_its_length(uc, fmt, lang):
    est = spec_tools.total_estimate(spec_tools.timeline(uc, fmt, lang))
    if fmt == "video":
        assert spec_tools.VIDEO_MIN <= est <= spec_tools.VIDEO_MAX
    else:
        assert est <= spec_tools.SHORT_TARGET_MAX


def test_tags_count_commas_and_quotes_like_youtube():
    assert spec_tools.tags_length(["a", "b c"]) == 1 + 1 + 3 + 2


def test_a_too_long_description_is_a_problem():
    spec = json.loads(json.dumps(SPEC))
    spec["usecases"][0]["youtube"]["de"]["description"] += "x" * 5000
    assert any("description over" in p for p in spec_tools.problems(spec))


@pytest.mark.parametrize("uc,fmt,lang", spec_tools.items(SPEC),
                         ids=lambda x: x if isinstance(x, str) else x["slug"])
def test_youtube_metadata_stays_within_limits(uc, fmt, lang):
    m = spec_tools.youtube_metadata(uc, fmt, lang)
    assert len(m["snippet"]["title"]) <= 100
    assert len(m["snippet"]["description"].encode()) <= 5000
    assert spec_tools.tags_length(m["snippet"]["tags"]) <= 500
    assert m["status"]["privacyStatus"] == "public"
    assert m["snippet"]["defaultAudioLanguage"] == lang
    assert (fmt == "short") == m["snippet"]["title"].endswith("#Shorts")
    assert uc["page"] in m["snippet"]["description"]


# --------------------------------------------------------------- HeyGen

def item(name):
    return next(t for t in spec_tools.items(SPEC) if spec_tools.item_name(*t) == name)


def test_no_avatar_payload_is_a_studio_video_of_image_scenes():
    body = heygen_make.payload(*item("einkauf-video-de"), CFG, {})
    assert body["type"] == "studio" and body["aspect_ratio"] == "16:9"
    assert 1 <= len(body["scenes"]) <= 50
    assert {s["type"] for s in body["scenes"]} == {"image"}
    narrated = [s for s in body["scenes"] if "script" in s]
    silent = [s for s in body["scenes"] if "duration" in s]
    assert narrated and silent
    assert all(s["voice_id"] == CFG["voices"]["de"] for s in narrated)
    assert not any("duration" in s for s in narrated)
    assert body["scenes"][0]["duration"] == spec_tools.TITLE_HOLD
    assert "style" not in body["caption"]          # sidecar only, nothing burned in


def test_a_short_is_vertical_and_has_no_holds():
    body = heygen_make.payload(*item("a2a-short-en"), CFG, {})
    assert body["aspect_ratio"] == "9:16"
    assert all("script" in s for s in body["scenes"])


def test_photo_avatar_mode_puts_the_avatar_in_front_of_the_scene():
    cfg = {**CFG, "mode": "photo-avatar", "avatar_id": "look_lobster"}
    body = heygen_make.payload(*item("rechnungen-video-en"), cfg, {"rechnungen-video-en-s02": "a2"})
    av = [s for s in body["scenes"] if s["type"] == "avatar_video"]
    assert av and all(s["input"]["avatar_id"] == "look_lobster" for s in av)
    assert all(s["input"]["remove_background"] for s in av)
    assert any(s["input"]["background"]["source"]["asset_id"] == "a2" for s in av)
    assert any(s["type"] == "image" and "duration" in s for s in body["scenes"])


def test_missing_voice_or_avatar_is_refused():
    with pytest.raises(ValueError):
        heygen_make.payload(*item("einkauf-video-en"), {**CFG, "voices": {"de": "x"}}, {})
    with pytest.raises(ValueError):
        heygen_make.payload(*item("einkauf-video-en"), {**CFG, "mode": "photo-avatar",
                                                        "avatar_id": None}, {})


def test_uploaded_assets_replace_the_placeholders():
    name = "kundenservice-short-de"
    assets = {f"{name}-s0{i}": f"asset{i}" for i in range(1, 4)}
    body = heygen_make.payload(*item(name), CFG, assets)
    assert "<asset:" not in json.dumps(body)


@pytest.mark.parametrize("name,secs,bad", [
    ("einkauf-video-de", 59.9, True), ("einkauf-video-de", 60.0, False),
    ("einkauf-video-de", 90.1, True), ("einkauf-short-de", 30.0, False),
    ("einkauf-short-de", 30.5, True), ("einkauf-short-de", None, True)])
def test_the_rendered_length_is_checked(name, secs, bad):
    assert (heygen_make.duration_problem(name, secs) is not None) == bad


def test_render_needs_an_item_or_all(monkeypatch):
    with pytest.raises(SystemExit):
        heygen_make.main(["render"])


# --------------------------------------------------------------- scenes

def test_scene_svgs_carry_the_meaning_in_colour():
    stop = scenes.svg("stop", "x", "de", "video")
    allow = scenes.svg("allow", "x", "en", "short")
    assert scenes.RED in stop and scenes.ORANGE in stop        # red mark, MolTrust shield
    assert scenes.GREEN in allow and 'width="1080" height="1920"' in allow
    assert "Fiktives Beispiel" in stop and "Fictional example" in allow


def test_labels_are_escaped():
    assert "&amp;" in scenes.svg("agent", "A & B", "en", "video")


def test_every_narrated_scene_and_card_gets_one_image():
    files = scenes.scene_files(SPEC)
    stems = [f[0] for f in files]
    assert len(stems) == len(set(stems))
    for uc, fmt, lang in spec_tools.items(SPEC):
        name = spec_tools.item_name(uc, fmt, lang)
        body = heygen_make.payload(uc, fmt, lang, CFG, {})
        needed = {s["source"]["asset_id"][7:-1] for s in body["scenes"]}
        assert needed <= set(stems), name


# --------------------------------------------------------------- YouTube

def test_auth_url_asks_for_offline_access_with_pkce():
    url = youtube_auth.auth_url("cid", "http://127.0.0.1:5", "chal", "st")
    assert "access_type=offline" in url and "code_challenge_method=S256" in url
    assert "youtube.upload" in url and "auth%2Fyoutube" in url


def test_a_web_client_json_is_refused(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}))
    with pytest.raises(SystemExit):
        youtube_auth.client_from(p)


def test_the_remote_command_carries_no_quotes_or_spaces_of_its_own():
    cmd = youtube_auth.remote_command()
    b64 = cmd.split('b64decode("')[1].split('")')[0]
    assert " " not in b64 and "'" not in b64
    assert base64.b64decode(b64).decode() == youtube_auth.REMOTE


def test_the_remote_script_replaces_lines_and_keeps_the_rest(tmp_path, monkeypatch, capsys):
    secrets_file = tmp_path / ".moltrust_secrets"
    secrets_file.write_text("A=1\nYOUTUBE_REFRESH_TOKEN=old\nB=2\n")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(
        "YOUTUBE_CLIENT_ID=c\nYOUTUBE_CLIENT_SECRET=s\nYOUTUBE_REFRESH_TOKEN=new\n"))
    exec(youtube_auth.REMOTE, {})  # nosec B102 - the script under test
    lines = secrets_file.read_text().splitlines()
    assert lines[:2] == ["A=1", "B=2"]
    assert "YOUTUBE_REFRESH_TOKEN=new" in lines and "YOUTUBE_REFRESH_TOKEN=old" not in lines
    assert oct(secrets_file.stat().st_mode)[-3:] == "600"
    out = capsys.readouterr().out
    assert out.strip() == "3 values written" and "new" not in out


def test_upload_plan_skips_what_is_done_and_dry_run_calls_nothing(tmp_path, capsys):
    (tmp_path / "videos.json").write_text(json.dumps(
        {"einkauf-video-de": {"file": str(tmp_path / "x.mp4"), "duration_s": 66}}))
    (tmp_path / "results.json").write_text(json.dumps(
        {"einkauf-video-en": {"video_id": "v", "url": "u"}}))
    todo = youtube_upload.plan(SPEC, json.loads((tmp_path / "videos.json").read_text()),
                               json.loads((tmp_path / "results.json").read_text()))
    assert len(todo) == 15 and todo[0]["name"] == "einkauf-video-de"
    assert youtube_upload.main(["--work", str(tmp_path), "--dry-run"]) == 0
    assert "15 upload(s) planned, 1 already done" in capsys.readouterr().out


def test_upload_refuses_unrendered_items(tmp_path):
    with pytest.raises(SystemExit):
        youtube_upload.main(["--work", str(tmp_path)])


def test_playlist_item_body_names_the_video():
    b = youtube_upload.playlist_item_body("PL1", "vid1")
    assert b["snippet"]["resourceId"] == {"kind": "youtube#video", "videoId": "vid1"}


# ------------------------------------------------------------------ motion (review 25.10.)

def test_motion_scenes_follow_the_sentence_without_holds():
    import motion
    uc, fmt, lang = motion.find_item(spec_tools.load(), "einkauf-video-de")
    scenes = motion.narrated(uc, fmt, lang)
    assert all(x["narration"] for x in scenes)
    layout = motion.lay_out(scenes, [4.0] * len(scenes), fmt)
    for a, b in zip(layout, layout[1:]):
        assert b["start"] == a["end"]  # the next scene starts when the sentence ends
    assert motion.FADE <= 0.3
    assert layout[-1]["kind"] == "outro"


def test_motion_page_uses_the_logo_and_the_robot_in_every_narrated_scene():
    import motion
    uc, fmt, lang = motion.find_item(spec_tools.load(), "einkauf-video-de")
    layout = motion.lay_out(motion.narrated(uc, fmt, lang), [4.0] * 5, fmt)
    doc = motion.page(layout, fmt, lang, uc)
    assert doc.count(">MT</text>") >= 1 and "rb-eyes" in doc
    assert "watermark" not in doc and "wasserzeichen" not in doc.lower()


def test_no_mensch_in_german_narration():
    for uc in spec_tools.load()["usecases"]:
        for fmt in spec_tools.FORMATS:
            for sc in uc[fmt]["scenes"]:
                assert "mensch" not in sc["narration"]["de"].lower()


def test_motion_voice_starts_after_one_second_and_robot_has_no_antenna():
    import motion
    uc, fmt, lang = motion.find_item(spec_tools.load(), "einkauf-video-de")
    layout = motion.lay_out(motion.narrated(uc, fmt, lang), [4.0] * 5, fmt)
    assert layout[0]["start"] == 0.0 and layout[0]["lead"] == 1.0
    assert all(x["lead"] == motion.LEAD for x in layout[1:-1])
    assert "rb-ant" not in motion.robot() and "rb-eyes" in motion.robot()
