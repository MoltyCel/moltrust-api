"""Moving scenes for the use-case videos: HeyGen speaks, Chrome draws (ops UC-VID).

Review of the first test video (25.10.): no still frames, at most 0.3 s between
the end of a sentence and the next scene, something moving in every scene, the
MolTrust shield logo instead of the watermark PNG, and one robot as the
agent in every video. HeyGen's image scenes are stills and its holds are
silence, so the picture is now made here:

- `narrate` (server, costs credits): one HeyGen studio video per narrated scene,
  downloaded to WORK/narration/<item>-sNN.mp4. Only the audio is used.
- `compose` (Mac, Chrome and ffmpeg): trims each clip's silence, lays the scenes
  end to end (the next one starts when the sentence ends, 0.3 s cross-fade),
  draws every frame of the animated scenes with headless Chrome and muxes the
  narration under it.
- `frame`: one PNG at a given second, for a quick look without rendering.

  venv/bin/python motion.py narrate --item einkauf-video-de --wait        # server
  python3 motion.py compose --item einkauf-video-de --narration DIR --out x.mp4
  python3 motion.py frame --item einkauf-video-de --t 12.5 --out f.png
"""
from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import shutil
import subprocess  # nosec B404 - runs local ffmpeg/ffprobe only
import sys
import tempfile
import time
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import spec_tools  # noqa: E402

NAVY = "#0F172A"
ORANGE = "#E85D26"
ORANGE_SOFT = "#FDEBE3"
SLATE_50 = "#F8FAFC"
SLATE_200 = "#E2E8F0"
SLATE_500 = "#64748B"
GREEN = "#22C55E"
RED = "#DC2626"
WHITE = "#FFFFFF"
# the robot: white and light grey, light-blue displays, orange from the logo
SKY_100 = "#E3F2FD"
SKY_200 = "#BFE3FA"
SKY_400 = "#5FB3E8"
SKY_700 = "#1E5A85"
GREY_400 = "#AAB4C3"

SIZES = {"video": (1920, 1080), "short": (1080, 1920)}
FPS = 25
FADE = 0.3          # cross-fade between scenes, the review's upper bound
LEAD = 0.25         # silence before a sentence within its scene
FIRST_LEAD = 1.0    # the first scene runs this long before the voice starts
TAIL = 0.10         # kept after the trimmed end of a sentence
OUTRO = 2.0         # moving end card after the last sentence
WORK = pathlib.Path(os.path.expanduser("~/Downloads/ucvid-work"))

FICTIONAL = {"de": "Fiktives Beispiel", "en": "Fictional example"}
TAGLINE = {"de": "Vertrauen für autonome Agenten", "en": "Trust for autonomous agents"}
TITLES = {
    "einkauf": {"de": "Einkaufsagent im Maschinenbau", "en": "Purchasing agent at a machine builder"},
    "rechnungen": {"de": "Rechnungsprüfung im Großhandel", "en": "Invoice checks at a wholesaler"},
    "kundenservice": {"de": "Kundenservice im Onlinehandel", "en": "Customer service at an online retailer"},
    "a2a": {"de": "Agenten zweier Firmen", "en": "Agents of two companies"},
}
#: the amount on the order card that travels through a scene
AMOUNT = {"einkauf": ("3.500 €", "€3,500"), "rechnungen": ("IBAN", "IBAN"),
          "kundenservice": ("300 €", "€300"), "a2a": ("PO", "PO")}


# ------------------------------------------------------------------ figures
# Each figure is drawn around (0, 0) in a box about 200 units wide. Parts that
# move carry a class; the animations address them by class within a scene.

def logo(r: float = 100) -> str:
    """The MolTrust logo (moltrust-web favicon.svg / img/moltrust-logo.png):
    round, dark blue outer ring, orange inner ring, orange shield with check
    mark, "MT" below."""
    k = r / 36
    return (f'<g transform="scale({k:.3f}) translate(-36,-36)">'
            f'<circle cx="36" cy="36" r="35" fill="{WHITE}"/>'
            f'<circle cx="36" cy="36" r="33" fill="none" stroke="{NAVY}" stroke-width="2"/>'
            f'<circle cx="36" cy="36" r="27" fill="none" stroke="{ORANGE}" stroke-width="2"/>'
            f'<path d="M36 18L25 24V32.5C25 39.5 29.3 46 36 48.5C42.7 46 47 39.5 47 32.5V24L36 18Z" '
            f'fill="{ORANGE}" fill-opacity="0.1" stroke="{ORANGE}" stroke-width="2"/>'
            f'<path d="M31 33L34 36L41 29" fill="none" stroke="{ORANGE}" stroke-width="2.5" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<text x="36" y="59" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="7" font-weight="700" fill="{NAVY}" letter-spacing="1.5">MT</text></g>')


def robot(mood: str = "happy") -> str:
    """The agent: a slim robot of plain geometric parts, white and light grey,
    light-blue display panels, orange accents from the logo. The face is a
    narrow display with two light points as eyes; the mood shows in them
    (happy, surprised, thinking). About 160 wide, 260 high, centred on (0, 0)."""
    glow = lambda cx, r, col=WHITE, k=1.0: (f'<circle cx="{cx}" cy="0" r="{r * 2:.1f}" fill="{col}" opacity="{0.28 * k:.2f}"/>'
                                            f'<circle cx="{cx}" cy="0" r="{r}" fill="{col}" opacity="{k:.2f}"/>')
    eyes = {
        "happy": glow(-18, 5) + glow(18, 5),
        "surprised": glow(-18, 7, ORANGE_SOFT) + glow(18, 7, ORANGE_SOFT),
        "thinking": glow(-12, 4, WHITE, 0.55) + glow(24, 5),
    }
    face = "".join(f'<g class="rb-mood rb-{m}" style="opacity:{1 if m == mood else 0}">{eyes[m]}</g>' for m in eyes)
    return (
        '<g transform="scale(1.15)">'
        f'<ellipse class="rb-shadow" cx="0" cy="96" rx="52" ry="8" fill="{NAVY}" opacity="0.12"/>'
        '<g class="rb">'
        # legs
        f'<rect x="-27" y="26" width="16" height="58" rx="3" fill="{SLATE_200}" stroke="{GREY_400}" stroke-width="3"/>'
        f'<rect x="11" y="26" width="16" height="58" rx="3" fill="{SLATE_200}" stroke="{GREY_400}" stroke-width="3"/>'
        f'<rect x="-33" y="82" width="26" height="9" rx="2" fill="{GREY_400}"/><rect x="7" y="82" width="26" height="9" rx="2" fill="{GREY_400}"/>'
        # arms, hinged at the orange shoulder joints
        f'<g class="rb-arm-l"><rect x="-68" y="-74" width="15" height="88" rx="4" fill="{SLATE_200}" stroke="{GREY_400}" stroke-width="3"/>'
        f'<rect x="-67" y="12" width="13" height="14" rx="2" fill="{GREY_400}"/></g>'
        f'<g class="rb-arm-r"><rect x="53" y="-74" width="15" height="88" rx="4" fill="{SLATE_200}" stroke="{GREY_400}" stroke-width="3"/>'
        f'<rect x="54" y="12" width="13" height="14" rx="2" fill="{GREY_400}"/></g>'
        f'<circle cx="-60" cy="-70" r="8" fill="{ORANGE}"/><circle cx="60" cy="-70" r="8" fill="{ORANGE}"/>'
        # torso: a tapered block with a display panel and the shield
        f'<path d="M-50 -80 H50 L40 28 H-40 Z" fill="{WHITE}" stroke="{GREY_400}" stroke-width="4" stroke-linejoin="round"/>'
        f'<rect x="-28" y="-64" width="56" height="40" rx="4" fill="{SKY_200}"/>'
        f'<g transform="translate(0,-44) scale(0.42)"><path d="M0 -36 L28 -24 V2 C28 22 14 34 0 40 C-14 34 -28 22 -28 2 V-24 Z" fill="{ORANGE}"/>'
        f'<path d="M-11 2 L-3 10 L13 -8" fill="none" stroke="{WHITE}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></g>'
        f'<rect x="-30" y="-8" width="60" height="5" rx="2" fill="{ORANGE}"/>'
        f'<rect x="-30" y="4" width="38" height="4" rx="2" fill="{SLATE_200}"/>'
        # head: neck, block, side modules, narrow face display
        '<g class="rb-head">'
        f'<rect x="-9" y="-92" width="18" height="14" fill="{GREY_400}"/>'
        f'<rect x="-66" y="-140" width="10" height="30" rx="2" fill="{GREY_400}"/><rect x="56" y="-140" width="10" height="30" rx="2" fill="{GREY_400}"/>'
        f'<rect x="-56" y="-156" width="112" height="64" rx="10" fill="{WHITE}" stroke="{GREY_400}" stroke-width="4"/>'
        f'<rect x="-30" y="-156" width="60" height="5" fill="{ORANGE}"/>'
        f'<rect x="-44" y="-136" width="88" height="24" rx="5" fill="{SKY_400}"/>'
        f'<rect class="rb-scan" x="-44" y="-136" width="6" height="24" fill="{WHITE}" opacity="0.30"/>'
        f'<g transform="translate(0,-124)"><g class="rb-eyes">{face}</g></g>'
        '</g></g></g>')


def person(color: str = NAVY) -> str:
    """An employee: round head, shoulders, a lanyard badge."""
    return (f'<ellipse cx="0" cy="118" rx="62" ry="10" fill="{NAVY}" opacity="0.10"/>'
            '<g class="ps">'
            f'<path d="M-70 110 C-70 40 70 40 70 110 Z" fill="{color}"/>'
            f'<rect x="-12" y="62" width="24" height="30" rx="5" fill="{WHITE}"/>'
            f'<circle cx="0" cy="-10" r="44" fill="#F2C9A8"/>'
            f'<path d="M-44 -14 C-44 -60 44 -60 44 -14 C30 -38 -24 -40 -44 -14 Z" fill="{SKY_700 if color == NAVY else NAVY}"/>'
            f'<circle cx="-15" cy="-6" r="5" fill="{NAVY}"/><circle cx="15" cy="-6" r="5" fill="{NAVY}"/>'
            f'<path d="M-14 14 Q0 24 14 14" fill="none" stroke="{NAVY}" stroke-width="4" stroke-linecap="round"/>'
            '</g>')


def card(text: str, color: str = ORANGE) -> str:
    return (f'<rect x="-78" y="-46" width="156" height="92" rx="14" fill="{WHITE}" stroke="{color}" stroke-width="5"/>'
            f'<rect x="-58" y="-28" width="58" height="8" rx="4" fill="{SLATE_200}"/>'
            f'<text x="0" y="26" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="34" font-weight="800" fill="{NAVY}">{html.escape(text)}</text>')


def check() -> str:
    return (f'<circle cx="0" cy="0" r="88" fill="{GREEN}"/>'
            f'<path d="M-42 2 L-12 32 L44 -28" fill="none" stroke="{WHITE}" stroke-width="18" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


def stop() -> str:
    return (f'<circle cx="0" cy="0" r="88" fill="{RED}"/>'
            f'<path d="M-34 -34 L34 34 M34 -34 L-34 34" stroke="{WHITE}" stroke-width="18" stroke-linecap="round"/>')


def papers() -> str:
    out = ""
    for i in range(3):
        out += (f'<rect x="{-62 + i * 8}" y="{-56 + i * 10}" width="112" height="132" rx="8" '
                f'fill="{WHITE}" stroke="{NAVY}" stroke-width="5"/>')
    out += "".join(f'<rect x="-30" y="{14 + j * 15}" width="70" height="6" rx="3" fill="{SLATE_500}"/>' for j in range(4))
    # the sheet that keeps landing on the stack
    out += (f'<g class="drop"><rect x="-46" y="-36" width="112" height="132" rx="8" fill="{WHITE}" '
            f'stroke="{ORANGE}" stroke-width="5"/><rect x="-26" y="0" width="60" height="6" rx="3" fill="{SLATE_200}"/></g>')
    return out


def rules(lines: int = 3) -> str:
    rows = ""
    for j in range(lines):
        y = -50 + j * 46
        rows += (f'<g class="rule rule{j}"><circle cx="-48" cy="{y}" r="13" fill="{GREEN}"/>'
                 f'<path d="M-55 {y} L-50 {y + 5} L-41 {y - 5}" fill="none" stroke="{WHITE}" stroke-width="4" '
                 'stroke-linecap="round" stroke-linejoin="round"/>'
                 f'<rect x="-26" y="{y - 6}" width="86" height="12" rx="6" fill="{NAVY}"/></g>')
    return (f'<rect x="-82" y="-92" width="164" height="184" rx="18" fill="{WHITE}" stroke="{NAVY}" stroke-width="6"/>'
            + rows)


def document() -> str:
    return (f'<rect x="-62" y="-82" width="124" height="164" rx="12" fill="{WHITE}" stroke="{NAVY}" stroke-width="6"/>'
            + "".join(f'<rect x="-40" y="{-52 + j * 18}" width="80" height="7" rx="3" fill="{SLATE_500}"/>' for j in range(4))
            + f'<g class="seal" transform="translate(30,50)"><circle r="28" fill="{ORANGE}"/>'
              f'<path d="M-12 0 L-3 9 L13 -9" fill="none" stroke="{WHITE}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/></g>')


def building(color: str = NAVY) -> str:
    win = "".join(f'<rect class="win" x="{-38 + (k % 3) * 28}" y="{-50 + (k // 3) * 30}" width="16" height="18" fill="{WHITE}"/>'
                  for k in range(9))
    return f'<rect x="-54" y="-80" width="108" height="170" rx="6" fill="{color}"/>' + win


def question() -> str:
    return (f'<circle cx="0" cy="0" r="88" fill="{SLATE_200}"/>'
            f'<text x="0" y="40" text-anchor="middle" font-size="120" font-weight="700" '
            f'fill="{NAVY}" font-family="Helvetica, Arial, sans-serif">?</text>')


# ------------------------------------------------------------------ animation

class Scene:
    """One scene's SVG markup plus the animations that drive it. Times are in
    seconds from the scene's start."""

    def __init__(self, idx: int):
        self.idx = idx
        self.parts: list[str] = []
        self.anims: list[dict] = []
        self.n = 0

    def put(self, svg: str, x: float, y: float, s: float = 1.0, cls: str = "") -> str:
        """Place a figure; returns a class that addresses it. The outer group
        positions, the inner group is what animations move."""
        self.n += 1
        name = f"p{self.idx}_{self.n}"
        self.parts.append(f'<g transform="translate({x:.0f},{y:.0f}) scale({s:.3f})">'
                          f'<g class="{name} {cls}">{svg}</g></g>')
        return name

    def anim(self, sel: str, frames: list[dict], dur: float, delay: float = 0.0,
             loop: bool = False, ease: str = "ease-in-out", alt: bool = False) -> None:
        self.anims.append({"sel": sel, "frames": frames, "dur": dur, "delay": delay,
                           "loop": loop, "ease": ease, "alt": alt})

    # common movements
    def pop(self, sel: str, at: float = 0.0, dur: float = 0.45) -> None:
        self.anim(sel, [{"transform": "scale(0)", "opacity": 0},
                        {"transform": "scale(1.12)", "opacity": 1, "offset": 0.7},
                        {"transform": "scale(1)", "opacity": 1}], dur, at, ease="ease-out")

    def float(self, sel: str, amp: float = 10, period: float = 2.4, phase: float = 0.0) -> None:
        self.anim(sel, [{"transform": "translateY(0px)"}, {"transform": f"translateY({-amp}px)"}],
                  period / 2, -phase, loop=True, alt=True)

    def pulse(self, sel: str, k: float = 1.06, period: float = 1.6, phase: float = 0.0) -> None:
        self.anim(sel, [{"transform": "scale(1)"}, {"transform": f"scale({k})"}], period / 2, -phase,
                  loop=True, alt=True)

    def robot_life(self, sel: str, wave: bool = False) -> None:
        self.float(f".{sel} .rb", 5, 2.6)
        self.anim(f".{sel} .rb-shadow", [{"transform": "scaleX(1)"}, {"transform": "scaleX(0.92)"}], 1.3, loop=True, alt=True)
        self.anim(f".{sel} .rb-eyes", [{"transform": "scaleY(1)"}, {"transform": "scaleY(1)", "offset": 0.92},
                                       {"transform": "scaleY(0.15)", "offset": 0.96}, {"transform": "scaleY(1)"}],
                  3.8, 0.6, loop=True, ease="linear")
        # a light line runs across the face display
        self.anim(f".{sel} .rb-scan", [{"transform": "translateX(0px)", "opacity": 0},
                                       {"transform": "translateX(41px)", "opacity": 1, "offset": 0.5},
                                       {"transform": "translateX(82px)", "opacity": 0}], 1.8, 0.3, loop=True, ease="linear")
        self.anim(f".{sel} .rb-head", [{"transform": "rotate(-1.5deg)"}, {"transform": "rotate(1.5deg)"}], 2.2, loop=True, alt=True)
        if wave:
            self.anim(f".{sel} .rb-arm-r", [{"transform": "rotate(0deg)"}, {"transform": "rotate(-115deg)", "offset": 0.3},
                                            {"transform": "rotate(-100deg)", "offset": 0.5}, {"transform": "rotate(-115deg)", "offset": 0.7},
                                            {"transform": "rotate(0deg)"}], 2.4, 0.3, loop=True)
        else:
            self.anim(f".{sel} .rb-arm-r", [{"transform": "rotate(0deg)"}, {"transform": "rotate(-10deg)"}], 1.2, loop=True, alt=True)
        self.anim(f".{sel} .rb-arm-l", [{"transform": "rotate(0deg)"}, {"transform": "rotate(8deg)"}], 1.4, loop=True, alt=True)

    def ripple(self, x: float, y: float, r: float, color: str = ORANGE, period: float = 1.4) -> None:
        """A ring that keeps growing and fading around a figure."""
        name = self.put(f'<circle r="{r:.0f}" fill="none" stroke="{color}" stroke-width="6"/>', x, y)
        self.anim(f".{name}", [{"transform": "scale(0.9)", "opacity": 0.7}, {"transform": "scale(1.5)", "opacity": 0}],
                  period, loop=True, ease="linear")

    def thinking_dots(self, x: float, y: float, s: float) -> None:
        for k in range(3):
            d = self.put(f'<circle r="12" fill="{SKY_400}"/>', x + (k - 1) * 34 * s, y, s)
            self.anim(f".{d}", [{"opacity": 0.2, "transform": "translateY(0px)"},
                                {"opacity": 1, "transform": "translateY(-14px)", "offset": 0.3},
                                {"opacity": 0.2, "transform": "translateY(0px)", "offset": 0.6},
                                {"opacity": 0.2, "transform": "translateY(0px)"}], 1.2, k * 0.25, loop=True, ease="linear")

    def mood(self, sel: str, to: str, at: float, frm: str = "happy") -> None:
        self.anim(f".{sel} .rb-{frm}", [{"opacity": 1}, {"opacity": 0}], 0.2, at, ease="linear")
        self.anim(f".{sel} .rb-{to}", [{"opacity": 0}, {"opacity": 1}], 0.2, at, ease="linear")

    def travel(self, sel: str, path: list[tuple[float, float]], dur: float, delay: float = 0.0,
               loop: bool = False, fade: bool = True) -> None:
        """Move a figure placed at path[0] along straight segments."""
        x0, y0 = path[0]
        n = len(path) - 1
        frames = []
        for i, (x, y) in enumerate(path):
            f = {"transform": f"translate({x - x0:.0f}px,{y - y0:.0f}px)", "offset": i / n}
            if fade:
                f["opacity"] = 0 if i in (0, n) and loop else 1
            frames.append(f)
        if fade and loop and n >= 1:
            frames.insert(1, {"transform": frames[0]["transform"], "opacity": 1, "offset": 0.04})
            frames.insert(-1, {"transform": frames[-1]["transform"], "opacity": 1, "offset": 0.96})
        self.anim(f".{sel}", frames, dur, delay, loop=loop, ease="ease-in-out")

    def svg(self) -> str:
        return "".join(self.parts)


def slots(fmt: str, n: int) -> list[tuple[float, float, float]]:
    """Centres and figure scale for n figures: a row in 16:9; in 9:16 two side
    by side, three as a triangle, four as a 2x2 grid (reading order)."""
    w, h = SIZES[fmt]
    if fmt == "video":
        gap = {1: 0, 2: 480, 3: 400, 4: 360}[n]
        x0 = w / 2 - gap * (n - 1) / 2
        return [(x0 + i * gap, 520, 1.45) for i in range(n)]
    l, r, top, low = w / 2 - 250, w / 2 + 250, 680, 1130
    return {1: [(w / 2, 900, 1.75)],
            2: [(l, 900, 1.35), (r, 900, 1.35)],
            3: [(l, top, 1.3), (r, top, 1.3), (w / 2, low, 1.3)],
            4: [(l, top, 1.25), (r, top, 1.25), (l, low, 1.25), (r, low, 1.25)]}[n]


def build_scene(idx: int, kind: str, uc: dict, lang: str, fmt: str, dur: float) -> Scene:
    sc = Scene(idx)
    amount = AMOUNT[uc["slug"]][0 if lang == "de" else 1]
    mood_at = min(1.6, dur * 0.4)

    if kind == "office":
        (xa, y, s), (xb, _, _) = slots(fmt, 2)
        p = sc.put(papers(), xa, y, s)
        sc.anim(f".{p} .drop", [{"transform": "translateY(-260px)", "opacity": 0},
                                {"transform": "translateY(0px)", "opacity": 1, "offset": 0.45},
                                {"transform": "translateY(0px)", "opacity": 1, "offset": 0.85},
                                {"transform": "translateY(0px)", "opacity": 0}], 1.4, loop=True, ease="ease-out")
        sc.anim(f".{p}", [{"transform": "rotate(-2deg)"}, {"transform": "rotate(2deg)"}], 0.7, loop=True, alt=True)
        m = sc.put(person(), xb, y, s)
        sc.float(f".{m} .ps", 6, 1.8)
        for k in range(3):  # orders keep arriving
            c = sc.put(card(""), xa - 360 * (1 if fmt == "video" else 0.6), y - 180 + k * 120, 0.45 * s)
            sc.travel(c, [(xa - 360 * (1 if fmt == "video" else 0.6), y - 180 + k * 120), (xa, y)], 1.8, -k * 0.6, loop=True)
    elif kind == "agent":
        (x, y, s), = slots(fmt, 1)
        r = sc.put(robot("happy"), x, y, s * 1.15)
        sc.pop(f".{r}", 0.05, 0.55)
        sc.robot_life(r, wave=True)
        for k in range(3):  # small orders circling the robot
            ox = sc.put(f'<g class="orbit"><g transform="translate(260,0)"><g class="orbit-in">{card("€", ORANGE)}</g></g></g>',
                        x, y - 20, 0.5 * s)
            sc.anim(f".{ox} .orbit", [{"transform": f"rotate({k * 120}deg)"}, {"transform": f"rotate({k * 120 + 360}deg)"}],
                    7.0, loop=True, ease="linear")
            # the card turns back as it goes round, so the text stays upright
            sc.anim(f".{ox} .orbit-in", [{"transform": f"rotate({-k * 120}deg)"}, {"transform": f"rotate({-k * 120 - 360}deg)"}],
                    7.0, loop=True, ease="linear")
    elif kind == "rules":
        (xa, ya, s), (xb, yb, _), (xc, yc, _) = slots(fmt, 3)
        ru = sc.put(rules(), xa, ya, s)
        for j in range(3):
            sc.pop(f".{ru} .rule{j}", 0.2 + j * 0.45, 0.4)
        sc.float(f".{ru}", 6, 2.6)
        lg = sc.put(logo(92), xb, yb, s)
        sc.pulse(f".{lg}", 1.07, 1.4)
        rb = sc.put(robot("happy"), xc, yc, s * 0.9)
        sc.robot_life(rb)
        dot = sc.put(f'<circle r="12" fill="{ORANGE}"/>', xa + 110 * s, ya)
        sc.travel(dot, [(xa + 110 * s, ya), (xb, yb), (xc + (0 if fmt == "short" else -100 * s), yc - (110 * s if fmt == "short" else 0))],
                  1.6, 0.4, loop=True)
    elif kind == "stop":
        (xa, ya, s), (xb, yb, _), (xc, yc, _), (xd, yd, _) = slots(fmt, 4)
        rb = sc.put(robot("happy"), xa, ya, s * 0.85)
        sc.robot_life(rb)
        lg = sc.put(logo(92), xb, yb, s * 0.95)
        sc.pulse(f".{lg}", 1.06, 1.3)
        st = sc.put(stop(), xc, yc, s * 0.8)
        sc.pop(f".{st}", mood_at, 0.4)
        sc.pulse(f".{st}", 1.06, 1.0, -mood_at - 0.4)
        ps = sc.put(person(ORANGE), xd, yd, s * 0.85)
        sc.float(f".{ps} .ps", 6, 1.8)
        up = 190 * s
        cd = sc.put(card(amount), xa, ya - up, 0.75 * s)
        sc.travel(cd, [(xa, ya - up), (xb, yb - up), (xb, yb - up - (40 * s if fmt == "short" else 0)), (xd, yd - up)],  # Shorts: no halt at the check
                  max(dur - 0.4, 2.4), 0.2, fade=False)
        sc.anim(f".{lg}", [{"transform": "translateX(0px)"}, {"transform": "translateX(-8px)"},
                           {"transform": "translateX(8px)"}, {"transform": "translateX(0px)"}], 0.3, mood_at - 0.3)
        sc.mood(rb, "surprised", mood_at)
    elif kind == "handoff":
        (xa, ya, s), (xb, yb, _) = slots(fmt, 2)
        st = sc.put(stop(), xa, ya, s * 0.9)
        sc.pulse(f".{st}", 1.06, 1.2)
        ps = sc.put(person(ORANGE), xb, yb, s)
        sc.float(f".{ps} .ps", 7, 1.8)
        sc.ripple(xb, yb, 110 * s, ORANGE)
        for k in range(2):  # half a period apart, so one card is always on its way
            cd = sc.put(card(amount), xa, ya - 170 * s, 0.7 * s)
            sc.travel(cd, [(xa, ya - 170 * s), (xb, yb - 170 * s)], 2.2, 0.2 + k * 1.1, loop=True)
    elif kind == "allow":
        (xa, ya, s), (xb, yb, _), (xc, yc, _) = slots(fmt, 3)
        rb = sc.put(robot("happy"), xa, ya, s * 0.9)
        sc.robot_life(rb, wave=idx > 1 and dur > 6)
        lg = sc.put(logo(92), xb, yb, s)
        sc.pulse(f".{lg}", 1.06, 1.0)
        ck = sc.put(check(), xc, yc, s * 0.85)
        sc.pop(f".{ck}", 0.3, 0.45)
        sc.pulse(f".{ck}", 1.08, 1.0, -0.75)
        up = 190 * s
        for k in range(3):  # routine orders flow through
            cd = sc.put(card("✓", GREEN), xa, ya - up, 0.55 * s)
            sc.travel(cd, [(xa, ya - up), (xb, yb - up), (xc, yc - up)], 2.4, k * 0.8, loop=True)
    elif kind == "proof":
        (xa, ya, s), (xb, yb, _), (xc, yc, _) = slots(fmt, 3)
        dc = sc.put(document(), xa, ya, s)
        sc.anim(f".{dc} .seal", [{"transform": "scale(0)"}, {"transform": "scale(1.25)", "offset": 0.25},
                                 {"transform": "scale(1)", "offset": 0.4}, {"transform": "scale(1)"}], 2.0, 0.3, loop=True)
        sc.float(f".{dc}", 6, 2.4)
        lg = sc.put(logo(92), xb, yb, s)
        sc.pulse(f".{lg}", 1.06, 1.3)
        ck = sc.put(check(), xc, yc, s * 0.85)
        sc.pop(f".{ck}", 0.6)
        sc.pulse(f".{ck}", 1.07, 1.1, -1.05)
        sc.ripple(xc, yc, 80 * s, GREEN)
    elif kind == "two_firms":
        # each firm with its own agent; a request goes back and forth between them
        (xa, ya, s), (xb, yb, _), (xc, yc, _), (xd, yd, _) = slots(fmt, 4)
        if fmt == "video":
            firms, agents = ((xa, ya, NAVY), (xd, yd, SLATE_500)), ((xb, yb), (xc, yc))
        else:
            firms, agents = ((xa, ya, NAVY), (xb, yb, SLATE_500)), ((xc, yc), (xd, yd))
        for xx, yy, col in firms:
            bd = sc.put(building(col), xx, yy - 20, s * 0.95)
            sc.anim(f".{bd} .win", [{"opacity": 1}, {"opacity": 0.55}], 0.9, loop=True, alt=True)
        for k, (xx, yy) in enumerate(agents):
            rb = sc.put(robot("happy"), xx, yy + 10, s * 0.75)
            sc.pop(f".{rb}", 0.1 + k * 0.3, 0.5)
            sc.robot_life(rb)
        (x1, y1), (x2, y2) = agents
        a, b = (x1 + 95 * s, y1 - 30 * s), (x2 - 95 * s, y2 - 30 * s)
        for k in range(2):  # half a period apart, so a card is always moving
            cd = sc.put(card("?", ORANGE), *a, 0.42 * s)
            sc.travel(cd, [a, b, a], 2.8, 0.6 + k * 1.4, loop=True)
    elif kind == "question":
        (xa, y, s), (xb, _, _) = slots(fmt, 2)
        rb = sc.put(robot("thinking"), xa, y, s)
        sc.robot_life(rb)
        # the bob and the swing sit on separate groups, so neither overrides the other
        q = sc.put(f'<g class="qb"><g class="qs">{question()}</g></g>', xb, y, s * 0.85)
        sc.pop(f".{q}", 0.2)
        sc.float(f".{q} .qb", 18, 1.6)
        sc.ripple(xb, y, 80 * s, SKY_400)
        sc.thinking_dots(xa + 90 * s, y - 230 * s, s)
        sc.anim(f".{q} .qs", [{"transform": "rotate(-12deg)"}, {"transform": "rotate(12deg)"}], 0.9, 0.65, loop=True, alt=True,
                ease="linear")
    elif kind == "outro":
        w, h = SIZES[fmt]
        lg = sc.put(logo(150 if fmt == "video" else 170), w / 2, h * (0.40 if fmt == "video" else 0.36))
        sc.anim(f".{lg}", [{"transform": "scale(0) rotate(-90deg)", "opacity": 0},
                           {"transform": "scale(1) rotate(0deg)", "opacity": 1}], 0.6, 0, ease="ease-out")
        sc.pulse(f".{lg}", 1.06, 0.9, -0.6)
        rb = sc.put(robot("happy"), w * (0.80 if fmt == "video" else 0.76), h * (0.60 if fmt == "video" else 0.62), 1.0)
        sc.anim(f".{rb}", [{"transform": "translateX(500px)"}, {"transform": "translateX(0px)"}], 0.7, 0.0,
                ease="ease-out")  # walks in from the right
        sc.robot_life(rb, wave=True)
        # the outro has no sentence to follow, so it keeps a visible movement to the end
        sc.anim(f".{rb} .rb", [{"transform": "translateX(0px)"}, {"transform": "translateX(-40px)"}], 0.8, 0.7, loop=True, alt=True)
    else:
        raise ValueError(f"unknown kind {kind!r}")
    return sc


# ------------------------------------------------------------------ page

def label_svg(sc: Scene, kind: str, label: str, fmt: str, lang: str) -> None:
    w, h = SIZES[fmt]
    font = "Helvetica Neue, Helvetica, Arial, sans-serif"
    big = 64 if fmt == "short" else 56
    if kind == "outro":
        lines = [("moltrust.ch", big + 22, 800, NAVY), (TAGLINE[lang], int(big * 0.62), 400, SLATE_500)]
        y0 = h * (0.66 if fmt == "video" else 0.58)
    else:
        from scenes import wrap  # same line breaking as the stills
        lines = [(t, big, 700, NAVY) for t in wrap(label, 24 if fmt == "short" else 46)]
        y0 = h * (0.80 if fmt == "video" else 0.79)
    y = y0
    for i, (t, size, weight, col) in enumerate(lines):
        name = sc.put(f'<text x="0" y="0" text-anchor="middle" font-family="{font}" font-size="{size}" '
                      f'font-weight="{weight}" fill="{col}">{html.escape(t)}</text>', w / 2, y)
        sc.anim(f".{name}", [{"transform": "translateY(30px)", "opacity": 0},
                             {"transform": "translateY(0px)", "opacity": 1}], 0.4, 0.15 + i * 0.25, ease="ease-out")
        y += size * 1.3


def title_svg(sc: Scene, title: str, fmt: str) -> None:
    w, h = SIZES[fmt]
    size = 40 if fmt == "video" else 46
    tw = len(title) * size * 0.56 + 80
    y = 175 if fmt == "video" else 260
    name = sc.put(f'<rect x="{-tw / 2:.0f}" y="-40" width="{tw:.0f}" height="80" rx="40" fill="{ORANGE_SOFT}"/>'
                  f'<text x="0" y="14" text-anchor="middle" font-family="Helvetica Neue, Helvetica, Arial, sans-serif" '
                  f'font-size="{size}" font-weight="700" fill="{ORANGE}">{html.escape(title)}</text>', w / 2, y)
    sc.anim(f".{name}", [{"transform": "translateY(-40px)", "opacity": 0},
                         {"transform": "translateY(0px)", "opacity": 1}], 0.5, 0.0, ease="ease-out")


def page(item: list[dict], fmt: str, lang: str, uc: dict) -> str:
    """The whole video as one HTML page; window.seek(t) shows second t."""
    w, h = SIZES[fmt]
    scenes, anims = [], []
    for i, x in enumerate(item):
        sc = build_scene(i, x["kind"], uc, lang, fmt, x["end"] - x["start"])
        if i == 0:
            title_svg(sc, TITLES[uc["slug"]][lang], fmt)
        label_svg(sc, x["kind"], x["label"], fmt, lang)
        scenes.append(f'<g class="scene" id="s{i}" style="opacity:0">{sc.svg()}</g>')
        for a in sc.anims:
            anims.append(dict(a, scene=i))
    times = [{"start": x["start"], "end": x["end"]} for x in item]
    # background shapes drift for the whole video, so no frame is ever still
    bg = "".join(f'<circle class="bgc bg{k}" cx="{cx}" cy="{cy}" r="{r}" fill="{col}" opacity="0.5"/>'
                 for k, (cx, cy, r, col) in enumerate([(w * 0.12, h * 0.82, 260, SKY_100),
                                                       (w * 0.9, h * 0.25, 220, ORANGE_SOFT),
                                                       (w * 0.55, h * 1.02, 300, SKY_100)]))
    head_logo = f'<g transform="translate({w * 0.04 + 34:.0f},{14 + 52})">{logo(34)}</g>'
    header = (f'<rect width="{w}" height="14" fill="{ORANGE}"/>{head_logo}'
              f'<text x="{w * 0.04 + 82:.0f}" y="{14 + 66}" font-family="Helvetica Neue, Helvetica, Arial, sans-serif" '
              f'font-size="42" font-weight="800" fill="{NAVY}">Mol<tspan fill="{ORANGE}">Trust</tspan></text>'
              f'<text x="{w * 0.96:.0f}" y="{14 + 62}" text-anchor="end" font-family="Helvetica Neue, Helvetica, Arial, sans-serif" '
              f'font-size="30" fill="{SLATE_500}">{FICTIONAL[lang]}</text>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;background:{SLATE_50};overflow:hidden}}
svg{{display:block}} svg *{{transform-box:fill-box;transform-origin:50% 50%}}
.rb-arm-r{{transform-origin:50% 8%}} .rb-arm-l{{transform-origin:50% 8%}} .rb-head{{transform-origin:50% 100%}}
.orbit{{transform-box:view-box;transform-origin:0 0}}
</style></head><body>
<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<rect width="{w}" height="{h}" fill="{SLATE_50}"/>{bg}{''.join(scenes)}{header}</svg>
<script>
const T = {json.dumps(times)}, A = {json.dumps(anims)}, FADE = {FADE};
const scenes = T.map((_, i) => document.getElementById('s' + i));
const live = [];
for (const a of A) {{
  for (const el of scenes[a.scene].querySelectorAll(a.sel)) {{
    const an = el.animate(a.frames, {{duration: a.dur * 1000, delay: a.delay * 1000, fill: 'both',
      easing: a.ease, iterations: a.loop ? Infinity : 1, direction: a.alt ? 'alternate' : 'normal'}});
    an.pause(); live.push([a.scene, an]);
  }}
}}
const bg = [...document.querySelectorAll('.bgc')].map((el, k) => {{
  const an = el.animate([{{transform: 'translate(0px,0px)'}}, {{transform: `translate(${{60 - k * 40}}px,${{-40 + k * 25}}px)`}}],
    {{duration: 5000 + k * 1300, iterations: Infinity, direction: 'alternate', easing: 'ease-in-out'}});
  an.pause(); return an;
}});
window.seek = (t) => {{
  T.forEach((s, i) => {{
    const next = T[i + 1];
    let o = i === 0 ? 1 : Math.min(1, Math.max(0, (t - s.start) / FADE));
    if (t < s.start) o = 0;
    if (next && t >= next.start + FADE) o = 0;
    scenes[i].style.opacity = o;
  }});
  for (const [i, an] of live) an.currentTime = Math.max(0, t - T[i].start) * 1000;
  for (const an of bg) an.currentTime = t * 1000;
}};
window.seek(0);
</script></body></html>"""


# ------------------------------------------------------------------ timeline

def narrated(uc: dict, fmt: str, lang: str) -> list[dict]:
    """The narrated scenes only: the silent holds of spec_tools are what the
    review took out."""
    return [x for x in spec_tools.timeline(uc, fmt, lang) if x["narration"]]


def lay_out(scenes: list[dict], speech: list[float], fmt: str) -> list[dict]:
    """Scene i runs from its start through LEAD + its sentence; the next starts
    right there (cross-fading over FADE); the voice starts FIRST_LEAD into
    the first scene. The video ends with a moving outro."""
    out, t = [], 0.0
    for i, (x, d) in enumerate(zip(scenes, speech)):
        lead = FIRST_LEAD if i == 0 else LEAD
        out.append(dict(x, start=round(t, 3), end=round(t + lead + d, 3), speech=d, lead=lead))
        t += lead + d
    if fmt == "video" and out[-1]["kind"] == "outro":
        out[-1]["end"] = round(out[-1]["end"] + OUTRO, 3)  # the spec's own end card, held like the added one
    elif fmt == "video":
        out.append({"kind": "outro", "label": "moltrust.ch", "narration": None, "scene": out[-1]["scene"] + 1,
                    "start": round(t, 3), "end": round(t + OUTRO, 3), "speech": 0.0, "lead": 0.0})
    else:
        out[-1]["end"] = round(out[-1]["end"] + 0.8, 3)
    return out


def find_item(spec: dict, name: str) -> tuple[dict, str, str]:
    for uc, fmt, lang in spec_tools.items(spec):
        if spec_tools.item_name(uc, fmt, lang) == name:
            return uc, fmt, lang
    raise SystemExit(f"unknown item {name!r}")


# ------------------------------------------------------------------ audio

def ffmpeg() -> str:
    for c in ("ffmpeg", "/opt/homebrew/bin/ffmpeg"):
        if shutil.which(c):
            return shutil.which(c)
    raise SystemExit("ffmpeg not found (brew install ffmpeg)")


def trimmed_wav(src: pathlib.Path, out: pathlib.Path) -> float:
    """The clip's audio as 48 kHz mono WAV without leading and trailing
    silence (TAIL kept after the last word); returns its length."""
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,"
            "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,"
            f"apad=pad_dur={TAIL},areverse")
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "48000",  # nosec B603
                    "-af", trim, str(out)], check=True)
    with wave.open(str(out)) as wf:
        return wf.getnframes() / wf.getframerate()


def mix(layout: list[dict], wavs: list[pathlib.Path], out: pathlib.Path) -> None:
    rate = 48000
    total = int(layout[-1]["end"] * rate) + rate // 2
    buf = bytearray(total * 2)
    for x, wpath in zip(layout, wavs):
        with wave.open(str(wpath)) as wf:
            data = wf.readframes(wf.getnframes())
        at = int((x["start"] + x["lead"]) * rate) * 2
        buf[at:at + len(data)] = data
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(buf))


# ------------------------------------------------------------------ commands

def cmd_frame(name: str, t: float, out: pathlib.Path, narration: pathlib.Path | None) -> int:
    from playwright.sync_api import sync_playwright
    uc, fmt, lang = find_item(spec_tools.load(), name)
    layout = timing(name, uc, fmt, lang, narration)
    w, h = SIZES[fmt]
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        src = pathlib.Path(tmp) / "v.html"
        src.write_text(page(layout, fmt, lang, uc), encoding="utf-8")
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": w, "height": h})
        pg.goto(src.as_uri())
        for i, tt in enumerate([t] if t >= 0 else [x["start"] + 1.2 for x in layout]):
            pg.evaluate(f"window.seek({tt})")
            dest = out if t >= 0 else out.with_name(f"{out.stem}-{i:02d}{out.suffix}")
            pg.screenshot(path=str(dest))
        b.close()
    print(json.dumps([{k: x[k] for k in ("kind", "start", "end")} for x in layout]))
    return 0


def timing(name: str, uc: dict, fmt: str, lang: str, narration: pathlib.Path | None,
           wav_dir: pathlib.Path | None = None) -> list[dict]:
    scenes = narrated(uc, fmt, lang)
    if narration and wav_dir:
        speech = [trimmed_wav(narration / f"{name}-s{x['scene']:02d}.mp4", wav_dir / f"s{x['scene']:02d}.wav")
                  for x in scenes]
    else:
        speech = [spec_tools.estimate_seconds(x["narration"], lang, spec_tools.speed_for(fmt)) for x in scenes]
    return lay_out(scenes, speech, fmt)


def cmd_compose(name: str, narration: pathlib.Path, out: pathlib.Path) -> int:
    from playwright.sync_api import sync_playwright
    uc, fmt, lang = find_item(spec_tools.load(), name)
    w, h = SIZES[fmt]
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = pathlib.Path(tmp)
        layout = timing(name, uc, fmt, lang, narration, tmpd)
        wavs = [tmpd / f"s{x['scene']:02d}.wav" for x in layout if x["narration"]]
        mix(layout, wavs, tmpd / "voice.wav")
        (tmpd / "v.html").write_text(page(layout, fmt, lang, uc), encoding="utf-8")
        frames = int(layout[-1]["end"] * FPS)
        out.parent.mkdir(parents=True, exist_ok=True)
        enc = subprocess.Popen([ffmpeg(), "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS),  # nosec B603
                                "-i", "-", "-i", str(tmpd / "voice.wav"), "-c:v", "libx264", "-preset", "medium",
                                "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
                                "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
        t0 = time.time()
        with sync_playwright() as p:
            b = p.chromium.launch(channel="chrome")
            pg = b.new_page(viewport={"width": w, "height": h})
            pg.goto((tmpd / "v.html").as_uri())
            for f in range(frames):
                pg.evaluate(f"window.seek({f / FPS})")
                enc.stdin.write(pg.screenshot(type="jpeg", quality=95))
            b.close()
        enc.stdin.close()
        if enc.wait() != 0:
            raise SystemExit("ffmpeg failed")
    print(f"{out}: {frames / FPS:.1f} s, {frames} frames in {time.time() - t0:.0f} s")
    print(json.dumps([{k: x[k] for k in ("kind", "start", "end")} for x in layout]))
    return 0


#: the scene a thumbnail is taken from, first match wins
THUMB_KINDS = ("stop", "proof", "allow")


def cmd_package(name: str, narration: pathlib.Path, out_dir: pathlib.Path) -> int:
    """Title, description, tags and a thumbnail next to the item's MP4."""
    from playwright.sync_api import sync_playwright
    uc, fmt, lang = find_item(spec_tools.load(), name)
    meta = spec_tools.youtube_metadata(uc, fmt, lang)["snippet"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "titel.txt").write_text(meta["title"] + "\n", encoding="utf-8")
    (out_dir / "beschreibung.txt").write_text(meta["description"] + "\n", encoding="utf-8")
    (out_dir / "tags.txt").write_text(", ".join(meta["tags"]) + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        layout = timing(name, uc, fmt, lang, narration, pathlib.Path(tmp))
    x = next(x for k in THUMB_KINDS for x in layout if x["kind"] == k)
    w, h = SIZES[fmt]
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        src = pathlib.Path(tmp) / "v.html"
        src.write_text(page(layout, fmt, lang, uc), encoding="utf-8")
        b = p.chromium.launch(channel="chrome")
        # YouTube wants 1280x720 (or 720x1280) and at most 2 MB
        k = 1280 / max(w, h)
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=k)
        pg.goto(src.as_uri())
        pg.evaluate(f"window.seek({min(x['start'] + 3.0, x['end'] - 0.4)})")
        pg.screenshot(path=str(out_dir / "vorschaubild.jpg"), type="jpeg", quality=90)
        b.close()
    print(f"{out_dir}: titel, beschreibung, tags, vorschaubild ({x['kind']})")
    return 0


def cmd_narrate(name: str, work: pathlib.Path, wait: bool) -> int:
    """Server side: one studio video per narrated scene, picture discarded."""
    import heygen_make as hg
    uc, fmt, lang = find_item(spec_tools.load(), name)
    cfg = hg.load_config()
    assets = hg._state(work, "assets.json")
    state = hg._state(work, "narration.json")
    s = hg._session()
    for x in narrated(uc, fmt, lang):
        key = f"{name}-s{x['scene']:02d}"
        if key in state and state[key].get("text") == x["narration"] and state[key].get("status") != "failed":
            continue
        asset = assets.get(key) or next(iter(assets.values()))
        body = {"type": "studio", "title": f"MolTrust narration {key}", "aspect_ratio": hg.ASPECT[fmt],
                "resolution": "720p", "scenes": [{"type": "image", "source": {"type": "asset_id", "asset_id": asset},
                                                  "script": x["narration"], "voice_id": cfg["voices"][lang],
                                                  "voice_settings": {"speed": spec_tools.speed_for(fmt)}}]}
        r = s.post(f"{hg.API}/v3/videos", json=body, timeout=60)
        r.raise_for_status()
        state[key] = {"video_id": r.json()["data"]["video_id"], "status": "pending", "text": x["narration"]}
        hg._save(work, "narration.json", state)
        print(f"{key}: submitted")
    import requests
    while True:
        open_ = 0
        for key, v in state.items():
            if not key.startswith(name + "-") or v.get("file") or v["status"] == "failed":
                continue
            d = s.get(f"{hg.API}/v3/videos/{v['video_id']}", timeout=30).json()["data"]
            v["status"] = d.get("status")
            if v["status"] == "completed":
                dest = work / "narration" / f"{key}.mp4"
                dest.parent.mkdir(parents=True, exist_ok=True)
                with requests.get(d["video_url"], stream=True, timeout=300) as dl:
                    dl.raise_for_status()
                    with dest.open("wb") as fh:
                        for chunk in dl.iter_content(1 << 20):
                            fh.write(chunk)
                v["file"] = str(dest)
                print(f"{key}: {d.get('duration')} s")
            elif v["status"] == "failed":
                print(f"{key}: failed ({d.get('failure_message') or d.get('failure_code')})")
            else:
                open_ += 1
            hg._save(work, "narration.json", state)
        if not wait or open_ == 0:
            break
        time.sleep(20)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("narrate")
    n.add_argument("--item", required=True)
    n.add_argument("--work", type=pathlib.Path, default=WORK)
    n.add_argument("--wait", action="store_true")
    c = sub.add_parser("compose")
    c.add_argument("--item", required=True)
    c.add_argument("--narration", type=pathlib.Path, required=True)
    c.add_argument("--out", type=pathlib.Path, required=True)
    f = sub.add_parser("frame")
    f.add_argument("--item", required=True)
    f.add_argument("--t", type=float, default=-1, help="second; omitted = one frame per scene")
    f.add_argument("--narration", type=pathlib.Path)
    f.add_argument("--out", type=pathlib.Path, required=True)
    k = sub.add_parser("package")
    k.add_argument("--item", required=True)
    k.add_argument("--narration", type=pathlib.Path, required=True)
    k.add_argument("--out-dir", type=pathlib.Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "narrate":
        return cmd_narrate(args.item, args.work, args.wait)
    if args.cmd == "package":
        return cmd_package(args.item, args.narration, args.out_dir)
    if args.cmd == "compose":
        return cmd_compose(args.item, args.narration, args.out)
    return cmd_frame(args.item, args.t, args.out, args.narration)


if __name__ == "__main__":
    raise SystemExit(main())
