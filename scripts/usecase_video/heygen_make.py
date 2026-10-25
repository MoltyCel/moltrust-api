"""Render the use-case videos and Shorts with the HeyGen v3 API (ops UC-VID).

One studio video per item (4 use cases x video/short x de/en = 16), built
from the scene images of scenes.py and the narration in spec/usecases.json:

- mode "no-avatar" (default): every scene is a narrated `image` scene, plus
  silent image holds (title card, beats, end card). HeyGen speaks the text
  with the configured voice; no avatar appears.
- mode "photo-avatar": narrated scenes become `avatar_video` scenes with the
  approved photo avatar in front of the scene image (remove_background), the
  holds stay image scenes.

API (developers.heygen.com, read 2026-10-25): POST /v3/assets (multipart
`file`, returns data.asset_id), POST /v3/videos with "type": "studio" (1-50
scenes, aspect_ratio 16:9 or 9:16, resolution 1080p), GET /v3/videos/{id}
until data.status is completed, then data.video_url. v2 is sunset on
2026-10-31 and not used here.

The key comes from HEYGEN_API_KEY in the environment and is never printed.
Rendering costs credits; nothing is rendered without --render, and --dry-run
prints the exact payloads and calls nothing.

  python heygen_make.py plan  [--item einkauf-video-de]        payloads, no network
  python heygen_make.py voices --lang en                       free read: voice list
  python heygen_make.py upload --work DIR                      scene PNGs -> asset ids
  python heygen_make.py render --work DIR --item ... [--all]   POST /v3/videos (costs)
  python heygen_make.py fetch  --work DIR                      poll and download MP4s
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import spec_tools  # noqa: E402

API = "https://api.heygen.com"
HERE = pathlib.Path(__file__).resolve().parent
CONFIG = HERE / "spec" / "heygen.json"
ASPECT = {"video": "16:9", "short": "9:16"}
DEFAULT_WORK = pathlib.Path(os.path.expanduser("~/Downloads/ucvid-work"))


def load_config(path: pathlib.Path | None = None) -> dict:
    return json.loads((path or CONFIG).read_text(encoding="utf-8"))


def scene_stem(name: str, scene: int) -> str:
    return f"{name}-s{scene:02d}"


def payload(uc: dict, fmt: str, lang: str, cfg: dict, assets: dict) -> dict:
    """The POST /v3/videos body for one item. `assets` maps a scene stem to
    its asset_id; a stem without one gets a visible placeholder."""
    name = spec_tools.item_name(uc, fmt, lang)
    voice = cfg["voices"].get(lang)
    if not voice:
        raise ValueError(f"no voice configured for {lang} in spec/heygen.json")
    mode = cfg.get("mode", "no-avatar")
    if mode == "photo-avatar" and not cfg.get("avatar_id"):
        raise ValueError("mode photo-avatar needs avatar_id in spec/heygen.json")
    speed = spec_tools.speed_for(fmt)
    scenes = []
    for x in spec_tools.timeline(uc, fmt, lang):
        stem = scene_stem(name, x["scene"])
        source = {"type": "asset_id", "asset_id": assets.get(stem, f"<asset:{stem}>")}
        if x["narration"] is None:
            scenes.append({"type": "image", "source": source, "duration": x["hold"]})
        elif mode == "photo-avatar":
            scenes.append({"type": "avatar_video", "input": {
                "type": "avatar", "avatar_id": cfg["avatar_id"],
                "script": x["narration"], "voice_id": voice,
                "voice_settings": {"speed": speed},
                "remove_background": True,
                "background": {"type": "image", "source": source}}})
        else:
            scenes.append({"type": "image", "source": source, "script": x["narration"],
                           "voice_id": voice, "voice_settings": {"speed": speed}})
    if len(scenes) > 50:
        raise ValueError(f"{name}: {len(scenes)} scenes, HeyGen takes at most 50")
    return {
        "type": "studio",
        "title": f"MolTrust use case {name}",
        "aspect_ratio": ASPECT[fmt],
        "resolution": cfg.get("resolution", "1080p"),
        # sidecar subtitles only; nothing is burned into the picture
        "caption": {"file_format": "srt"},
        "scenes": scenes,
    }


def selected(spec: dict, item: str | None, all_: bool) -> list[tuple[dict, str, str]]:
    out = [t for t in spec_tools.items(spec)
           if all_ or item is None or spec_tools.item_name(*t) == item]
    if item and not out:
        raise SystemExit(f"unknown item {item!r}")
    return out


# ------------------------------------------------------------------ network

def _key() -> str:
    key = os.environ.get("HEYGEN_API_KEY", "").strip()
    if not key:
        raise SystemExit("HEYGEN_API_KEY is not set (source ~/.moltrust_secrets on the server)")
    return key


def _session():
    import requests
    s = requests.Session()
    s.headers["X-Api-Key"] = _key()
    return s


def _state(work: pathlib.Path, name: str) -> dict:
    p = work / name
    return json.loads(p.read_text()) if p.exists() else {}


def _save(work: pathlib.Path, name: str, data: dict) -> None:
    work.mkdir(parents=True, exist_ok=True)
    tmp = work / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, work / name)


def cmd_voices(lang: str) -> int:
    names = {"de": "German", "en": "English"}
    r = _session().get(f"{API}/v3/voices", params={"language": names[lang], "limit": 50}, timeout=30)
    r.raise_for_status()
    for v in r.json().get("data", []):
        print(f"{v['voice_id']}  {v.get('name', '')}  {v.get('gender', '')}  {v.get('preview_audio_url', '')}")
    return 0


def cmd_upload(work: pathlib.Path, scenes_dir: pathlib.Path) -> int:
    assets = _state(work, "assets.json")
    s = _session()
    pngs = sorted(scenes_dir.glob("*.png"))
    if not pngs:
        raise SystemExit(f"no PNGs in {scenes_dir}; run scenes.py first")
    for png in pngs:
        if png.stem in assets:
            continue
        with png.open("rb") as fh:
            r = s.post(f"{API}/v3/assets", files={"file": (png.name, fh, "image/png")}, timeout=120)
        r.raise_for_status()
        assets[png.stem] = r.json()["data"]["asset_id"]
        _save(work, "assets.json", assets)
        time.sleep(2.1)  # heavy endpoints: 30 per minute
    print(f"{len(assets)} asset(s) recorded in {work / 'assets.json'}")
    return 0


def cmd_render(work: pathlib.Path, todo, cfg: dict) -> int:
    assets = _state(work, "assets.json")
    videos = _state(work, "videos.json")
    s = _session()
    for uc, fmt, lang in todo:
        name = spec_tools.item_name(uc, fmt, lang)
        if name in videos and videos[name].get("status") != "failed":
            print(f"{name}: already {videos[name].get('status')} ({videos[name]['video_id']})")
            continue
        body = payload(uc, fmt, lang, cfg, assets)
        missing = [sc for sc in body["scenes"]
                   if "<asset:" in json.dumps(sc)]
        if missing:
            raise SystemExit(f"{name}: {len(missing)} scene(s) without an uploaded asset; run upload")
        r = s.post(f"{API}/v3/videos", json=body, timeout=60)
        r.raise_for_status()
        videos[name] = {"video_id": r.json()["data"]["video_id"], "status": "pending",
                        "estimate_s": spec_tools.total_estimate(spec_tools.timeline(uc, fmt, lang))}
        _save(work, "videos.json", videos)
        print(f"{name}: submitted {videos[name]['video_id']}")
    return 0


def duration_problem(name: str, seconds: float | None) -> str | None:
    if seconds is None:
        return f"{name}: no duration reported"
    if "-video-" in name and not spec_tools.VIDEO_MIN <= seconds <= spec_tools.VIDEO_MAX:
        return f"{name}: {seconds:.1f} s, not {spec_tools.VIDEO_MIN:.0f}-{spec_tools.VIDEO_MAX:.0f}"
    if "-short-" in name and seconds > spec_tools.SHORT_MAX:
        return f"{name}: {seconds:.1f} s, over {spec_tools.SHORT_MAX:.0f}"
    return None


def cmd_fetch(work: pathlib.Path, wait: bool) -> int:
    import requests
    videos = _state(work, "videos.json")
    s = _session()
    problems = []
    while True:
        open_ = 0
        for name, v in videos.items():
            if v.get("status") in ("completed", "failed") and (v.get("file") or v["status"] == "failed"):
                continue
            r = s.get(f"{API}/v3/videos/{v['video_id']}", timeout=30)
            r.raise_for_status()
            d = r.json()["data"]
            v["status"] = d.get("status")
            v["duration_s"] = d.get("duration")
            if v["status"] == "completed":
                out = work / "mp4" / f"{name}.mp4"
                out.parent.mkdir(parents=True, exist_ok=True)
                with requests.get(d["video_url"], stream=True, timeout=300) as dl:
                    dl.raise_for_status()
                    with out.open("wb") as fh:
                        for chunk in dl.iter_content(1 << 20):
                            fh.write(chunk)
                v["file"] = str(out)
                if d.get("subtitle_url"):
                    v["subtitle_url"] = d["subtitle_url"]
                p = duration_problem(name, v["duration_s"])
                if p:
                    problems.append(p)
            elif v["status"] == "failed":
                v["failure"] = d.get("failure_message") or d.get("failure_code")
                problems.append(f"{name}: failed ({v['failure']})")
            else:
                open_ += 1
            _save(work, "videos.json", videos)
        if not wait or open_ == 0:
            break
        time.sleep(30)
    done = sum(1 for v in videos.values() if v.get("file"))
    print(f"{done} of {len(videos)} downloaded to {work / 'mp4'}")
    print("\n".join(problems) if problems else "no length problems")
    return 1 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--item")
    p.add_argument("--mode", choices=["no-avatar", "photo-avatar"])
    v = sub.add_parser("voices")
    v.add_argument("--lang", choices=spec_tools.LANGS, required=True)
    u = sub.add_parser("upload")
    u.add_argument("--work", type=pathlib.Path, default=DEFAULT_WORK)
    u.add_argument("--scenes", type=pathlib.Path)
    r = sub.add_parser("render")
    r.add_argument("--work", type=pathlib.Path, default=DEFAULT_WORK)
    r.add_argument("--item")
    r.add_argument("--all", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    f = sub.add_parser("fetch")
    f.add_argument("--work", type=pathlib.Path, default=DEFAULT_WORK)
    f.add_argument("--wait", action="store_true")
    args = ap.parse_args(argv)

    spec = spec_tools.load()
    cfg = load_config()
    if args.cmd == "plan" or (args.cmd == "render" and args.dry_run):
        if getattr(args, "mode", None):
            cfg["mode"] = args.mode
        work = getattr(args, "work", None)
        assets = _state(work, "assets.json") if work else {}
        for uc, fmt, lang in selected(spec, args.item, getattr(args, "all", False) or not args.item):
            print(f"# {spec_tools.item_name(uc, fmt, lang)}: POST {API}/v3/videos")
            print(json.dumps(payload(uc, fmt, lang, cfg, assets), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "voices":
        return cmd_voices(args.lang)
    if args.cmd == "upload":
        return cmd_upload(args.work, args.scenes or args.work / "scenes")
    if args.cmd == "render":
        if not (args.item or args.all):
            raise SystemExit("name --item or --all; rendering costs credits")
        return cmd_render(args.work, selected(spec, args.item, args.all), cfg)
    if args.cmd == "fetch":
        return cmd_fetch(args.work, args.wait)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
