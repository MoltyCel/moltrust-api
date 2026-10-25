"""The use-case video spec: loading, checking and timing (ops UC-VID).

spec/usecases.json holds, per use case, the scenes of the 16:9 video and of
the 9:16 Short, each with a visual kind, an on-screen label and the narration
in German and English, plus the YouTube title, description and tags. Every
other script in this directory reads it through this module, so the limits are
checked in one place:

- a video runs 60 to 90 seconds, a Short at most 30 (job UC-VID);
- YouTube: title at most 100 characters, description at most 5000 bytes, tags
  at most 500 characters with the commas between them and the quotes YouTube
  puts around a tag that contains a space
  (https://developers.google.com/youtube/v3/docs/videos, read 2026-10-25).

HeyGen derives a narrated scene's length from its audio, so the length here is
an estimate from the word count. The video gets silent holds (a title card and
a beat after each scene) until the estimate reaches the target; the real length
is read from the finished file and checked again before upload.
"""
from __future__ import annotations

import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
SPEC = HERE / "spec" / "usecases.json"

LANGS = ("de", "en")
FORMATS = ("video", "short")

#: Spoken words per second at speed 1.0, a working estimate (German runs longer
#: words, so fewer per second). Corrected from the first rendered video.
WORDS_PER_SECOND = {"de": 2.2, "en": 2.5}

VIDEO_MIN, VIDEO_MAX, VIDEO_TARGET = 60.0, 90.0, 66.0
SHORT_MAX, SHORT_TARGET_MAX = 30.0, 28.0
TITLE_HOLD = 4.0
END_HOLD = 5.0
BEAT_HOLD = 3.0
MAX_BEATS_PER_SCENE = 2
#: Narration speed passed to HeyGen (voice_settings.speed, 0.5-1.5). The
#: document's narration is short for a 60-90 s video; a calmer pace and
#: silent holds make up the rest.
VIDEO_SPEED = 0.92
SHORT_SPEED = 1.0

TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX = 500

KINDS = {"office", "agent", "rules", "stop", "allow", "handoff", "proof",
         "two_firms", "question", "outro"}


def load(path: pathlib.Path | None = None) -> dict:
    return json.loads((path or SPEC).read_text(encoding="utf-8"))


def words(text: str) -> int:
    return len(re.findall(r"[\w€.,%-]+", text))


def estimate_seconds(text: str, lang: str, speed: float = 1.0) -> float:
    return words(text) / (WORDS_PER_SECOND[lang] * speed)


def tags_length(tags: list[str]) -> int:
    """Length as YouTube counts it: commas between tags, quotes around a tag
    with a space."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def speed_for(fmt: str) -> float:
    return VIDEO_SPEED if fmt == "video" else SHORT_SPEED


def timeline(uc: dict, fmt: str, lang: str, speed: float | None = None) -> list[dict]:
    """The scenes in render order, with silent holds added where the format
    needs them. Each item: {kind, label, narration|None, hold|None, est}."""
    speed = speed_for(fmt) if speed is None else speed
    out = []
    if fmt == "video":
        out.append({"kind": "title", "label": uc["slug"], "narration": None,
                    "hold": TITLE_HOLD, "est": TITLE_HOLD, "scene": 0})
    for i, sc in enumerate(uc[fmt]["scenes"], start=1):
        text = sc["narration"][lang]
        out.append({"kind": sc["kind"], "label": sc["label"][lang], "narration": text,
                    "hold": None, "est": estimate_seconds(text, lang, speed), "scene": i})
    if fmt == "video":
        out.append({"kind": "outro", "label": "moltrust.ch", "narration": None,
                    "hold": END_HOLD, "est": END_HOLD, "scene": len(uc[fmt]["scenes"]) + 1})
        beats = 0
        while sum(x["est"] for x in out) < VIDEO_TARGET:
            # a beat after the scene with the fewest beats so far, in order
            narrated = [x for x in out if x["narration"]][:-1]  # none after the last line
            counts = {x["scene"]: sum(1 for y in out if y["hold"] == BEAT_HOLD
                                      and y["scene"] == x["scene"]) for x in narrated}
            scene = min(counts, key=lambda s: (counts[s], s))
            if counts[scene] >= MAX_BEATS_PER_SCENE:
                break
            at = max(i for i, x in enumerate(out) if x["scene"] == scene) + 1
            src = next(x for x in out if x["scene"] == scene)
            out.insert(at, {"kind": src["kind"], "label": src["label"], "narration": None,
                            "hold": BEAT_HOLD, "est": BEAT_HOLD, "scene": scene})
            beats += 1
    return out


def total_estimate(items: list[dict]) -> float:
    return round(sum(x["est"] for x in items), 1)


def problems(spec: dict) -> list[str]:
    """Everything that would break a limit, as readable lines. Empty = fine."""
    out = []
    for uc in spec["usecases"]:
        for fmt in FORMATS:
            for sc in uc[fmt]["scenes"]:
                if sc["kind"] not in KINDS:
                    out.append(f"{uc['slug']} {fmt}: unknown kind {sc['kind']!r}")
                for lang in LANGS:
                    if not sc["narration"].get(lang) or not sc["label"].get(lang):
                        out.append(f"{uc['slug']} {fmt}: scene without {lang} text")
            for lang in LANGS:
                est = total_estimate(timeline(uc, fmt, lang))
                if fmt == "video" and not VIDEO_MIN <= est <= VIDEO_MAX:
                    out.append(f"{uc['slug']} video {lang}: about {est} s, not {VIDEO_MIN:.0f}-{VIDEO_MAX:.0f}")
                if fmt == "short" and est > SHORT_TARGET_MAX:
                    out.append(f"{uc['slug']} short {lang}: about {est} s, over {SHORT_TARGET_MAX:.0f}")
        for lang in LANGS:
            yt = uc["youtube"][lang]
            if len(yt["title"]) > TITLE_MAX:
                out.append(f"{uc['slug']} {lang}: title {len(yt['title'])} > {TITLE_MAX}")
            if len(yt["description"].encode("utf-8")) > DESCRIPTION_MAX_BYTES:
                out.append(f"{uc['slug']} {lang}: description over {DESCRIPTION_MAX_BYTES} bytes")
            if tags_length(yt["tags"]) > TAGS_MAX:
                out.append(f"{uc['slug']} {lang}: tags {tags_length(yt['tags'])} > {TAGS_MAX}")
            if uc["page"] not in yt["description"]:
                out.append(f"{uc['slug']} {lang}: description does not link {uc['page']}")
    return out


def short_title(title: str, lang: str) -> str:
    """The Short's title: the video's title with a #Shorts marker, within 100."""
    tag = " #Shorts"
    return (title if len(title) + len(tag) <= TITLE_MAX else title[:TITLE_MAX - len(tag)].rstrip()) + tag


def youtube_metadata(uc: dict, fmt: str, lang: str) -> dict:
    yt = uc["youtube"][lang]
    title = yt["title"] if fmt == "video" else short_title(yt["title"], lang)
    return {
        "snippet": {
            "title": title,
            "description": yt["description"],
            "tags": yt["tags"],
            "categoryId": "28",           # Science & Technology
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            # Animated flat-design scenes and a synthetic voice; nothing that
            # depicts a real person or event realistically.
            "containsSyntheticMedia": False,
        },
    }


def items(spec: dict) -> list[tuple[dict, str, str]]:
    """Every (use case, format, language) the job produces: 4 x 2 x 2 = 16."""
    return [(uc, fmt, lang) for uc in spec["usecases"] for fmt in FORMATS for lang in LANGS]


def item_name(uc: dict, fmt: str, lang: str) -> str:
    return f"{uc['slug']}-{fmt}-{lang}"


if __name__ == "__main__":
    s = load()
    for uc, fmt, lang in items(s):
        print(f"{item_name(uc, fmt, lang):24s} about {total_estimate(timeline(uc, fmt, lang)):5.1f} s")
    p = problems(s)
    print("\n".join(p) if p else "spec ok")
    raise SystemExit(1 if p else 0)
