"""Scene backgrounds for the use-case videos, flat design in MolTrust colours (ops UC-VID).

Every scene of spec/usecases.json is drawn as one still image: 1920x1080 for
the 16:9 video, 1080x1920 for the 9:16 Short. HeyGen narrates over these
images (a studio video's narrated image scenes), so the picture carries the
meaning: a shield for the MolTrust check, a green check mark for allowed, a
red mark for stopped.

Colours are the site's (moltrust-web assets/css, read 2026-10-25): navy
#0F172A, orange #E85D26, slate, verified green #22C55E. Stopped is a plain red
#DC2626 so it never reads as the brand orange.

The images are SVG first and turned into PNG by headless Chrome, which is
present on the Mac; no other renderer is needed.

  python scripts/usecase_video/scenes.py --out ~/Downloads/ucvid-scenes
  python scripts/usecase_video/scenes.py --out DIR --only einkauf-video-de
"""
from __future__ import annotations

import argparse
import html
import pathlib
import shutil
import subprocess  # nosec B404 - runs the local Chrome binary only
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import spec_tools  # noqa: E402

NAVY = "#0F172A"
ORANGE = "#E85D26"
ORANGE_LIGHT = "#FFF3EE"
SLATE_50 = "#F8FAFC"
SLATE_200 = "#E2E8F0"
SLATE_500 = "#64748B"
GREEN = "#22C55E"
RED = "#DC2626"
WHITE = "#FFFFFF"

SIZES = {"video": (1920, 1080), "short": (1080, 1920)}

FICTIONAL = {"de": "Fiktives Beispiel", "en": "Fictional example"}
TITLES = {
    "einkauf": {"de": "Einkaufsagent im Maschinenbau", "en": "Purchasing agent at a machine builder"},
    "rechnungen": {"de": "Rechnungsprüfung im Großhandel", "en": "Invoice checks at a wholesaler"},
    "kundenservice": {"de": "Kundenservice im Onlinehandel", "en": "Customer service at an online retailer"},
    "a2a": {"de": "Agenten zweier Firmen", "en": "Agents of two companies"},
}

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "chromium", "chromium-browser",
]


# ------------------------------------------------------------------ icons
# Each icon is drawn in a 200x200 box and placed with a transform.

def icon_shield(color=ORANGE):
    return (f'<path d="M100 10 L175 40 V95 C175 145 140 178 100 192 C60 178 25 145 25 95 V40 Z" '
            f'fill="{color}"/>'
            f'<path d="M100 32 L155 54 V95 C155 132 130 157 100 168 C70 157 45 132 45 95 V54 Z" '
            f'fill="none" stroke="{WHITE}" stroke-width="6"/>')


def icon_check():
    return (f'<circle cx="100" cy="100" r="88" fill="{GREEN}"/>'
            f'<path d="M58 102 L88 132 L144 72" fill="none" stroke="{WHITE}" stroke-width="18" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


def icon_stop():
    return (f'<circle cx="100" cy="100" r="88" fill="{RED}"/>'
            f'<path d="M66 66 L134 134 M134 66 L66 134" stroke="{WHITE}" stroke-width="18" '
            'stroke-linecap="round"/>')


def icon_agent():
    return (f'<rect x="40" y="55" width="120" height="100" rx="26" fill="{NAVY}"/>'
            f'<circle cx="78" cy="102" r="12" fill="{WHITE}"/><circle cx="122" cy="102" r="12" fill="{WHITE}"/>'
            f'<rect x="76" y="128" width="48" height="8" rx="4" fill="{WHITE}"/>'
            f'<line x1="100" y1="55" x2="100" y2="28" stroke="{NAVY}" stroke-width="8"/>'
            f'<circle cx="100" cy="24" r="10" fill="{ORANGE}"/>')


def icon_person(color=NAVY):
    return (f'<circle cx="100" cy="62" r="36" fill="{color}"/>'
            f'<path d="M36 182 C36 128 164 128 164 182 Z" fill="{color}"/>')


def icon_papers():
    out = ""
    for i in range(4):
        out += (f'<rect x="{38 + i * 10}" y="{40 + i * 12}" width="110" height="130" rx="8" '
                f'fill="{WHITE}" stroke="{NAVY}" stroke-width="5"/>')
    for j in range(4):
        out += f'<rect x="90" y="{110 + j * 16}" width="74" height="6" rx="3" fill="{SLATE_500}"/>'
    return out


def icon_document_seal():
    return (f'<rect x="40" y="20" width="120" height="160" rx="10" fill="{WHITE}" stroke="{NAVY}" stroke-width="6"/>'
            + "".join(f'<rect x="60" y="{50 + j * 18}" width="80" height="7" rx="3" fill="{SLATE_500}"/>'
                      for j in range(4))
            + f'<circle cx="128" cy="148" r="26" fill="{ORANGE}"/>'
              f'<path d="M116 148 L125 157 L141 139" fill="none" stroke="{WHITE}" stroke-width="6" '
              'stroke-linecap="round" stroke-linejoin="round"/>')


def icon_rules():
    rows = ""
    for j in range(3):
        y = 52 + j * 46
        rows += (f'<circle cx="62" cy="{y}" r="12" fill="{GREEN}"/>'
                 f'<rect x="84" y="{y - 5}" width="76" height="10" rx="5" fill="{NAVY}"/>')
    return f'<rect x="28" y="18" width="150" height="164" rx="14" fill="{WHITE}" stroke="{NAVY}" stroke-width="6"/>' + rows


def icon_building(color=NAVY):
    win = "".join(f'<rect x="{62 + (k % 3) * 28}" y="{70 + (k // 3) * 30}" width="16" height="18" fill="{WHITE}"/>'
                  for k in range(9))
    return f'<rect x="48" y="40" width="104" height="150" fill="{color}"/>' + win


def icon_question():
    return (f'<circle cx="100" cy="100" r="88" fill="{SLATE_200}"/>'
            f'<text x="100" y="138" text-anchor="middle" font-size="120" font-weight="700" '
            f'fill="{NAVY}" font-family="Helvetica, Arial, sans-serif">?</text>')


def place(icon: str, x: float, y: float, scale: float) -> str:
    return f'<g transform="translate({x:.0f},{y:.0f}) scale({scale:.2f})">{icon}</g>'


# ------------------------------------------------------------------ scenes

def composition(kind: str) -> list[tuple[str, float, float]]:
    """(icon, x, y) on a 1000x600 stage, in stage units; the scale is set by
    the caller. A scene shows its subject, and the MolTrust shield where the
    check happens."""
    shield = icon_shield()
    return {
        "title": [(shield, 400, 160)],
        "outro": [(shield, 400, 160)],
        "office": [(icon_papers(), 260, 170), (icon_person(), 540, 170)],
        "agent": [(icon_agent(), 400, 170)],
        "rules": [(icon_rules(), 250, 170), (shield, 470, 170), (icon_agent(), 650, 170)],
        "stop": [(icon_agent(), 180, 170), (shield, 400, 170), (icon_stop(), 620, 170)],
        "allow": [(icon_agent(), 180, 170), (shield, 400, 170), (icon_check(), 620, 170)],
        "handoff": [(icon_stop(), 200, 170), (icon_person(ORANGE), 560, 170)],
        "proof": [(icon_document_seal(), 260, 170), (shield, 460, 170), (icon_check(), 660, 170)],
        "two_firms": [(icon_building(), 140, 150), (icon_agent(), 400, 170), (icon_building(SLATE_500), 660, 150)],
        "question": [(icon_agent(), 260, 170), (icon_question(), 540, 170)],
    }[kind]


def wrap(label: str, width_chars: int) -> list[str]:
    lines = []
    for part in label.split(" · "):
        cur = ""
        for w in part.split():
            if len(cur) + len(w) + 1 > width_chars and cur:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        lines.append(cur)
    return lines


def svg(kind: str, label: str, lang: str, fmt: str, title: str | None = None) -> str:
    w, h = SIZES[fmt]
    vertical = fmt == "short"
    # The icon group is centred and scaled to the frame: its stage-unit bounding
    # box (each icon 200 units square) takes at most this share of the width.
    comp = composition(kind)
    minx = min(x for _, x, _ in comp)
    maxx = max(x for _, x, _ in comp) + 200
    miny = min(y for _, _, y in comp)
    span = maxx - minx
    target_w = w * (0.86 if vertical else 0.56)
    scale = min(target_w / span, (h * (0.30 if vertical else 0.42)) / 200)
    ox = (w - span * scale) / 2 - minx * scale
    oy = h * (0.30 if vertical else 0.26) - miny * scale
    icons = "".join(place(ic, ox + x * scale, oy + y * scale, scale)
                    for ic, x, y in comp)
    font = "Helvetica, Arial, sans-serif"
    big = 64 if vertical else 58
    lines = wrap(title if kind == "title" and title else label, 26 if vertical else 48)
    ty0 = h * (0.62 if vertical else 0.74)
    text = "".join(
        f'<text x="{w / 2:.0f}" y="{ty0 + i * big * 1.25:.0f}" text-anchor="middle" '
        f'font-family="{font}" font-size="{big}" font-weight="700" fill="{NAVY}">{html.escape(t)}</text>'
        for i, t in enumerate(lines))
    if kind == "outro":
        text = (f'<text x="{w / 2:.0f}" y="{ty0:.0f}" text-anchor="middle" font-family="{font}" '
                f'font-size="{big + 20}" font-weight="800" fill="{NAVY}">moltrust.ch</text>'
                f'<text x="{w / 2:.0f}" y="{ty0 + big * 1.4:.0f}" text-anchor="middle" font-family="{font}" '
                f'font-size="{big * 0.6:.0f}" fill="{SLATE_500}">'
                f'{"Vertrauen für autonome Agenten" if lang == "de" else "Trust for autonomous agents"}</text>')
    bar = 14
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<rect width="{w}" height="{h}" fill="{SLATE_50}"/>'
            f'<rect width="{w}" height="{bar}" fill="{ORANGE}"/>'
            f'<text x="{w * 0.04:.0f}" y="{bar + 70}" font-family="{font}" font-size="44" '
            f'font-weight="800" fill="{NAVY}">Mol<tspan fill="{ORANGE}">Trust</tspan></text>'
            f'<text x="{w * 0.96:.0f}" y="{bar + 66}" text-anchor="end" font-family="{font}" '
            f'font-size="30" fill="{SLATE_500}">{FICTIONAL[lang]}</text>'
            f'{icons}{text}</svg>')


def chrome() -> str:
    for c in CHROME_CANDIDATES:
        if pathlib.Path(c).exists() or shutil.which(c):
            return c
    raise SystemExit("no Chrome or Chromium found for rendering")


def render_png(svg_text: str, out: pathlib.Path, fmt: str) -> None:
    w, h = SIZES[fmt]
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "scene.svg"
        src.write_text(svg_text, encoding="utf-8")
        subprocess.run(  # nosec B603 - fixed argument list, local file
            [chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={w},{h}", f"--screenshot={out}", src.as_uri()],
            check=True, capture_output=True, timeout=120)


def scene_files(spec: dict) -> list[tuple[str, str, str, str, str, str]]:
    """(file stem, kind, label, lang, fmt, title) for every distinct image."""
    out = []
    for uc, fmt, lang in spec_tools.items(spec):
        for x in spec_tools.timeline(uc, fmt, lang):
            if x["narration"] is None and x["kind"] not in ("title", "outro"):
                continue  # a beat reuses its scene's image
            stem = f"{spec_tools.item_name(uc, fmt, lang)}-s{x['scene']:02d}"
            out.append((stem, x["kind"], x["label"], lang, fmt, TITLES[uc["slug"]][lang]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--only", help="item name, e.g. einkauf-video-de")
    ap.add_argument("--svg-only", action="store_true", help="write SVG, skip PNG rendering")
    args = ap.parse_args(argv)
    spec = spec_tools.load()
    n = 0
    for stem, kind, label, lang, fmt, title in scene_files(spec):
        if args.only and not stem.startswith(args.only + "-"):
            continue
        doc = svg(kind, label, lang, fmt, title)
        (args.out / f"{stem}.svg").parent.mkdir(parents=True, exist_ok=True)
        (args.out / f"{stem}.svg").write_text(doc, encoding="utf-8")
        if not args.svg_only:
            render_png(doc, args.out / f"{stem}.png", fmt)
        n += 1
    print(f"{n} scene image(s) in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
