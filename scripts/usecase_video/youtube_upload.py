"""Upload the finished use-case videos and Shorts to YouTube (ops UC-VID).

Reads videos.json from the HeyGen work directory (heygen_make.py fetch),
uploads every downloaded MP4 with title, description and tags from
spec/usecases.json, public, and puts it into the playlist "MolTrust Use
Cases", creating that playlist once. results.json records video id and URL per
item, so a second run uploads only what is missing.

API (developers.google.com/youtube/v3, read 2026-10-25): videos.insert as a
resumable upload; uploads from unverified API projects are not restricted to
private; one insert costs 1 unit of the separate Video Uploads bucket (100 a
day); playlistItems.insert costs 50 units. A vertical video of up to three
minutes is a Short (support.google.com/youtube/answer/15424877).

Credentials come from YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and
YOUTUBE_REFRESH_TOKEN (written by youtube_auth.py) and are never printed.

  python youtube_upload.py --work DIR --dry-run     requests only, no network
  python youtube_upload.py --work DIR               upload what is missing
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import spec_tools  # noqa: E402

TOKEN_URI = "https://oauth2.googleapis.com/token"  # nosec B105 - an endpoint
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"
DEFAULT_WORK = pathlib.Path(os.path.expanduser("~/Downloads/ucvid-work"))


def plan(spec: dict, videos: dict, results: dict) -> list[dict]:
    """What would be uploaded, in item order: name, file, metadata."""
    out = []
    for uc, fmt, lang in spec_tools.items(spec):
        name = spec_tools.item_name(uc, fmt, lang)
        if name in results:
            continue
        v = videos.get(name, {})
        out.append({"name": name, "file": v.get("file"),
                    "duration_s": v.get("duration_s"),
                    "body": spec_tools.youtube_metadata(uc, fmt, lang)})
    return out


def playlist_body(spec: dict) -> dict:
    p = spec["playlist"]
    return {"snippet": {"title": p["title"], "description": p["description"],
                        "defaultLanguage": "en"},
            "status": {"privacyStatus": "public"}}


def playlist_item_body(playlist_id: str, video_id: str) -> dict:
    return {"snippet": {"playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id}}}


# ------------------------------------------------------------------ network

def access_token(session) -> str:
    env = {k: os.environ.get(k, "").strip() for k in
           ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)}; run youtube_auth.py first")
    r = session.post(TOKEN_URI, data={
        "client_id": env["YOUTUBE_CLIENT_ID"], "client_secret": env["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": env["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def find_or_create_playlist(session, title: str, body: dict) -> str:
    r = session.get(f"{API}/playlists", params={"part": "snippet", "mine": "true",
                                                 "maxResults": 50}, timeout=30)
    r.raise_for_status()
    for p in r.json().get("items", []):
        if p["snippet"]["title"] == title:
            return p["id"]
    r = session.post(f"{API}/playlists", params={"part": "snippet,status"}, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def upload_video(session, path: pathlib.Path, body: dict) -> str:
    size = path.stat().st_size
    r = session.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                     json=body, headers={"X-Upload-Content-Type": "video/mp4",
                                         "X-Upload-Content-Length": str(size)}, timeout=60)
    r.raise_for_status()
    location = r.headers["Location"]
    with path.open("rb") as fh:
        r = session.put(location, data=fh, headers={"Content-Type": "video/mp4",
                                                    "Content-Length": str(size)}, timeout=1800)
    r.raise_for_status()
    return r.json()["id"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", type=pathlib.Path, default=DEFAULT_WORK)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    spec = spec_tools.load()
    videos_p = args.work / "videos.json"
    videos = json.loads(videos_p.read_text()) if videos_p.exists() else {}
    results_p = args.work / "results.json"
    results = json.loads(results_p.read_text()) if results_p.exists() else {}
    todo = plan(spec, videos, results)

    if args.dry_run:
        print(f"# playlist: find by title or POST {API}/playlists?part=snippet,status")
        print(json.dumps(playlist_body(spec), indent=2, ensure_ascii=False))
        for t in todo:
            print(f"# {t['name']}: POST {UPLOAD}?uploadType=resumable&part=snippet,status "
                  f"file={t['file'] or '<not rendered yet>'}")
            print(json.dumps(t["body"], indent=2, ensure_ascii=False))
        print(f"{len(todo)} upload(s) planned, {len(results)} already done")
        return 0

    not_ready = [t["name"] for t in todo if not t["file"] or not pathlib.Path(t["file"]).exists()]
    if not_ready:
        raise SystemExit(f"not rendered yet: {', '.join(not_ready)}")
    import requests
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {access_token(s)}"
    playlist = results.get("_playlist") or find_or_create_playlist(
        s, spec["playlist"]["title"], playlist_body(spec))
    results["_playlist"] = playlist
    for t in todo:
        vid = upload_video(s, pathlib.Path(t["file"]), t["body"])
        r = s.post(f"{API}/playlistItems", params={"part": "snippet"},
                   json=playlist_item_body(playlist, vid), timeout=30)
        r.raise_for_status()
        url = (f"https://www.youtube.com/shorts/{vid}" if "-short-" in t["name"]
               else f"https://www.youtube.com/watch?v={vid}")
        results[t["name"]] = {"video_id": vid, "url": url}
        tmp = results_p.with_suffix(".tmp")
        tmp.write_text(json.dumps(results, indent=2))
        os.replace(tmp, results_p)
        print(f"{t['name']}: {url}")
    print(f"playlist https://www.youtube.com/playlist?list={playlist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
