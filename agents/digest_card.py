"""PNG card for the Daily Integrity Digest.

Renders the day's top flagged markets as a 1200x675 image so the post carries
the numbers even for someone who never opens the link. Colours are the live
site palette (read off moltrust.ch/integrity.html 2026-09-21): navy #0F172A
ground, orange #E85D26 for flagged, amber #F59E0B for caution, green #22C55E
for verified.
"""
from __future__ import annotations

import datetime
import io

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 675
NAVY = (15, 23, 42)
CARD = (30, 41, 59)
SLATE = (51, 65, 85)
TEXT = (248, 250, 252)
MUTED = (148, 163, 184)
ORANGE = (232, 93, 38)
AMBER = (245, 158, 11)
GREEN = (34, 197, 94)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(name: str, size: int):
    for path in (f"{FONT_DIR}/{name}", name):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def tier_color(score: int) -> tuple:
    if score >= 70:
        return ORANGE
    if score >= 40:
        return AMBER
    return GREEN


def fmt_vol(v) -> str:
    """Compact money. Mirrors herald_v3.fmt_vol so card and tweet agree."""
    try:
        v = abs(float(v or 0))
    except (TypeError, ValueError):
        return "$0"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


def signal_labels(signals: dict) -> list[str]:
    names = [("volumeSpike", "volume spike"),
             ("priceVolumeDiv", "price-volume divergence"),
             ("walletConcentration", "wallet concentration"),
             ("newWalletInflux", "new wallet influx")]
    return [label for key, label in names if signals.get(key)]


def _wrap(draw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and words:
        last = lines[-1]
        while last and draw.textlength(last + " …", font=font) > max_width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        rendered = " ".join(lines)
        if len(rendered) < len(text):
            lines[-1] = last + " …"
    return lines


def render(markets: list[dict], scanned: int, when: datetime.datetime | None = None) -> bytes:
    """Return PNG bytes for up to three flagged markets."""
    when = when or datetime.datetime.now(datetime.timezone.utc)
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)

    f_kicker = _font("DejaVuSans-Bold.ttf", 22)
    f_title = _font("DejaVuSans-Bold.ttf", 46)
    f_score = _font("DejaVuSans-Bold.ttf", 40)
    f_row = _font("DejaVuSans-Bold.ttf", 25)
    f_meta = _font("DejaVuSans.ttf", 21)
    f_foot = _font("DejaVuSans.ttf", 20)

    d.rectangle([0, 0, W, 8], fill=ORANGE)
    d.text((56, 44), "MOLTGUARD · POLYMARKET", font=f_kicker, fill=ORANGE)
    d.text((56, 78), "Daily Integrity Digest", font=f_title, fill=TEXT)
    stamp = when.strftime("%d %b %Y · %H:%M UTC")
    d.text((W - 56 - d.textlength(stamp, font=f_meta), 92), stamp, font=f_meta, fill=MUTED)

    top = 168
    row_h = 132
    for i, m in enumerate(markets[:3]):
        y = top + i * (row_h + 14)
        d.rounded_rectangle([56, y, W - 56, y + row_h], radius=14, fill=CARD)

        score = int(m.get("anomalyScore", 0) or 0)
        col = tier_color(score)
        d.rounded_rectangle([76, y + 22, 76 + 104, y + 22 + 88], radius=10, fill=col)
        s = str(score)
        d.text((76 + 52 - d.textlength(s, font=f_score) / 2, y + 40), s,
               font=f_score, fill=NAVY)

        text_x = 212
        max_w = W - 56 - text_x - 24
        q = (m.get("marketQuestion") or "(untitled market)").strip()
        for j, line in enumerate(_wrap(d, q, f_row, max_w, 2)):
            d.text((text_x, y + 22 + j * 32), line, font=f_row, fill=TEXT)

        sigs = m.get("signals", {}) or {}
        labels = signal_labels(sigs)
        delta = fmt_vol(sigs.get("volumeChange24h"))
        meta = f"{delta} 24h  ·  " + (", ".join(labels) if labels else "signals active")
        while d.textlength(meta, font=f_meta) > max_w and ", " in meta:
            meta = meta.rsplit(", ", 1)[0] + " …"
        d.text((text_x, y + 92), meta, font=f_meta, fill=MUTED)

    d.line([56, H - 74, W - 56, H - 74], fill=SLATE, width=1)
    left = f"{len(markets[:3])} flagged · {scanned} markets scanned"
    d.text((56, H - 54), left, font=f_foot, fill=MUTED)
    right = "moltrust.ch/integrity.html"
    d.text((W - 56 - d.textlength(right, font=f_foot), H - 54), right,
           font=f_foot, fill=ORANGE)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Stat-tile card (weekly proof post) ──

def _chrome(d, title: str, kicker: str, when: datetime.datetime,
            foot_left: str, foot_right: str) -> None:
    """Header rule, kicker, title, timestamp and footer — shared by both cards."""
    f_kicker = _font("DejaVuSans-Bold.ttf", 22)
    f_title = _font("DejaVuSans-Bold.ttf", 46)
    f_meta = _font("DejaVuSans.ttf", 21)
    f_foot = _font("DejaVuSans.ttf", 20)

    d.rectangle([0, 0, W, 8], fill=ORANGE)
    d.text((56, 44), kicker, font=f_kicker, fill=ORANGE)
    d.text((56, 78), title, font=f_title, fill=TEXT)
    stamp = when.strftime("%d %b %Y · %H:%M UTC")
    d.text((W - 56 - d.textlength(stamp, font=f_meta), 92), stamp, font=f_meta, fill=MUTED)

    d.line([56, H - 74, W - 56, H - 74], fill=SLATE, width=1)
    d.text((56, H - 54), foot_left, font=f_foot, fill=MUTED)
    d.text((W - 56 - d.textlength(foot_right, font=f_foot), H - 54), foot_right,
           font=f_foot, fill=ORANGE)


def render_metrics(tiles: list[dict], title: str = "Weekly Proof",
                   kicker: str = "MOLTRUST · LAST 7 DAYS",
                   foot_left: str = "", foot_right: str = "moltrust.ch",
                   when: datetime.datetime | None = None) -> bytes:
    """A 2x2 grid of stat tiles. Each tile: {"value", "label", "sub"}.

    The tiles carry no colour of their own. These numbers are magnitudes, not
    categories and not statuses, so giving each one a hue would invent an
    encoding that means nothing; the figure sits in primary ink, the label and
    the sub-line in muted ink, and the one accent is the rule and the footer.
    Status colour stays reserved for the risk tiers on the digest card.
    """
    when = when or datetime.datetime.now(datetime.timezone.utc)
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    _chrome(d, title, kicker, when, foot_left, foot_right)

    f_value = _font("DejaVuSans-Bold.ttf", 64)
    f_label = _font("DejaVuSans-Bold.ttf", 24)
    f_sub = _font("DejaVuSans.ttf", 20)

    gap, left, top = 24, 56, 176
    tile_w = (W - left * 2 - gap) // 2
    tile_h = 186

    for i, tile in enumerate(tiles[:4]):
        col, row = i % 2, i // 2
        x = left + col * (tile_w + gap)
        y = top + row * (tile_h + gap)
        d.rounded_rectangle([x, y, x + tile_w, y + tile_h], radius=14, fill=CARD)

        inner = tile_w - 48
        value = str(tile.get("value", "—"))
        vf = f_value
        while d.textlength(value, font=vf) > inner and vf.size > 34:
            vf = _font("DejaVuSans-Bold.ttf", vf.size - 4)
        d.text((x + 24, y + 22), value, font=vf, fill=TEXT)

        d.text((x + 24, y + 100), str(tile.get("label", "")), font=f_label, fill=TEXT)
        for j, line in enumerate(_wrap(d, str(tile.get("sub", "")), f_sub, inner, 2)):
            d.text((x + 24, y + 132 + j * 24), line, font=f_sub, fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
