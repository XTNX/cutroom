"""Infographic slot renderer (stage 6): spec JSON → animated overlay with alpha.

The slot agent decides WHAT to show (type, labels, the speaker's own numbers); this renderer
decides HOW, deterministically, from a small set of templates — fonts are checked to really
contain Cyrillic, easing is cubic (never linear), one element is revealed at a time, the last
frame is held ≥ 1 s (video-use animation rules).

Outputs in the slot folder:
  render.mov   1920×1080 RGBA (PNG codec)  — overlaid by render.py (setpts shift, subtitles last)
  preview.webm 960×540 VP9 with alpha       — the live program monitor
  poster.png   the finished state           — thumbnail in the panel

Usage: python gfx_render.py <slot_dir> --fps 25 --duration 6
       (reads <slot_dir>/spec.json and <slot_dir>/brand.json)
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FONT_DIRS = [Path(__file__).resolve().parent.parent / "assets" / "fonts", Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")]
DEFAULT_BRAND = {"bg": "#0b0c10", "panel": "#14161c", "fg": "#f3f2ee", "muted": "#9aa1ad", "accent": "#e8a33d",
                 "accent2": "#5b8def", "font": "segoeui.ttf", "font_bold": "seguisb.ttf"}


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def rgb(h: str, a: int = 255) -> tuple:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    name, _, weight = name.partition("@")   # "Font[wght].ttf@800": a weight of a variable font
    for d in [Path(name).parent if Path(name).is_absolute() else None, *FONT_DIRS]:
        if d is None:
            continue
        p = Path(name) if Path(name).is_absolute() else d / name
        if p.exists():
            f = ImageFont.truetype(str(p), size)
            if weight:
                try:
                    f.set_variation_by_axes([int(weight)])
                except (OSError, ValueError):
                    pass
            # fonts fail silently: make sure Cyrillic really renders (not .notdef boxes)
            if f.getmask("Жщ").getbbox() is None or f.getbbox("Ж") == f.getbbox("\uffff"):
                raise SystemExit(f"Шрифт {p.name} не содержит кириллицу — выберите другой")
            return f
    for fallback in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        for d in FONT_DIRS:
            if (d / fallback).exists():
                return ImageFont.truetype(str(d / fallback), size)
    raise SystemExit(f"Шрифт не найден: {name}")


_FONTS: dict = {}


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    if (name, size) not in _FONTS:
        _FONTS[(name, size)] = load_font(name, size)
    return _FONTS[(name, size)]


def fit(d, text: str, name: str, size: int, maxw: float, floor: float = 0.45) -> ImageFont.FreeTypeFont:
    """Largest size ≤ `size` at which `text` fits `maxw` (never below floor·size)."""
    sz = size
    while sz > size * floor and d.textlength(text, font=font(name, sz)) > maxw:
        sz -= 2
    return font(name, sz)


def title_lines(d, text: str, name: str, size: int, maxw: float) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """One line if it fits; else the most balanced two-line break, shrunk only if still too wide."""
    if d.textlength(text, font=font(name, size)) <= maxw or " " not in text.strip():
        return [text], fit(d, text, name, size, maxw)
    w = text.split()
    f0 = font(name, size)
    best = min(range(1, len(w)), key=lambda k: max(d.textlength(" ".join(w[:k]), font=f0), d.textlength(" ".join(w[k:]), font=f0)))
    lines = [" ".join(w[:best]), " ".join(w[best:])]
    longest = max(lines, key=lambda x: d.textlength(x, font=f0))
    return lines, fit(d, longest, name, size, maxw, 0.6)


def fmt_num(v: float, unit: str = "") -> str:
    if v is None:
        return ""
    s = f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.1f}"
    s = s.replace(",", "\u00a0").replace(".", ",")   # no-break space: brand fonts have it, not U+202F
    return f"{s}{(' ' + unit) if unit and not unit.startswith('%') else unit}"


FALLBACK = {"₽": "руб.", "→": "—", " ": " ", " ": " "}
_CMAPS: dict = {}
_GLYPH_FONTS: list[str] = []   # the brand's fonts: a character missing in one is drawn with another


def _cmap(name: str) -> set[int]:
    if name not in _CMAPS:
        cps: set[int] = set()
        try:
            from fontTools.ttLib import TTFont
            p = Path(name.partition("@")[0])
            p = p if p.is_absolute() else next((d / p for d in FONT_DIRS if (d / p).exists()), None)
            if p and p.exists():
                cps = set(TTFont(str(p), lazy=True).getBestCmap())
        except Exception:  # noqa: BLE001 — no fontTools or a broken font: no per-glyph fallback
            pass
        _CMAPS[name] = cps
    return _CMAPS[name]


def _name_of(f: ImageFont.FreeTypeFont) -> str:
    return next((k[0] for k, v in _FONTS.items() if v is f), f.path)


def T(d, xy, text: str, font: ImageFont.FreeTypeFont, fill=None, **kw) -> None:
    """d.text with a per-glyph fallback: Sofia Sans has the arrow but no rouble sign, Jura the other
    way round — each run of characters is drawn in a brand font that has them, on one baseline."""
    have = _cmap(_name_of(font))
    if not have or kw or all(ord(ch) in have or ch.isspace() for ch in text):
        d.text(xy, text, font=font, fill=fill, **kw)
        return
    x, y = xy
    base = y + font.getmetrics()[0]
    runs: list = []
    for ch in text:
        f = font
        if ord(ch) not in have and not ch.isspace():
            alt = next((n for n in _GLYPH_FONTS if ord(ch) in _cmap(n)), None)
            if alt:
                f = globals()["font"](alt, font.size)
        if runs and runs[-1][1] is f:
            runs[-1] = (runs[-1][0] + ch, f)
        else:
            runs.append((ch, f))
    for run, f in runs:
        d.text((x, base), run, font=f, fill=fill, anchor="ls")
        x += d.textlength(run, font=f)


def glyph_safe(spec, font_names: list[str]):
    """Characters none of the brand fonts has would render as boxes: swap them for plain equivalents.
    (One font missing a glyph is fine: T() draws it with another brand font.)"""
    names = list(dict.fromkeys(font_names + _GLYPH_FONTS))
    if not any(_cmap(n) for n in names):
        return spec
    missing = {ch for ch in FALLBACK if not any(ord(ch) in _cmap(n) for n in names)}
    if not missing:
        return spec

    def fix(v):
        if isinstance(v, str):
            for ch in missing:
                v = v.replace(ch, FALLBACK[ch])
            return v
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        return v
    return fix(spec)


class Ctx:
    def __init__(self, spec: dict, brand: dict, duration: float):
        b = {**DEFAULT_BRAND, **brand}
        _GLYPH_FONTS[:] = [n for n in dict.fromkeys([b["font"], b["font_bold"], b.get("font_accent") or ""]) if n]
        spec = glyph_safe(spec, [b["font"], b["font_bold"]])
        self.s, self.b, self.dur = spec, b, duration
        self.f_title = load_font(self.b["font_bold"], 54)
        self.f_sub = load_font(self.b["font"], 30)
        self.f_body = load_font(self.b["font"], 36)
        self.f_bold = load_font(self.b["font_bold"], 40)
        self.f_big = load_font(self.b["font_bold"], 150)
        items = spec.get("items") or []
        self.items = items[:6]
        self.n = max(1, len(self.items))
        side = spec.get("layout", "side") != "full"
        self.box = (1080, 120, 1840, 960) if side else (160, 110, 1760, 970)
        # one element at a time: reveal slots after the card and the title
        self.t_card, self.t_title = 0.35, 0.75
        span = max(0.6, min(1.2, (duration - 2.4) / self.n))
        self.t_items = [self.t_title + 0.1 + k * span for k in range(self.n)]
        self.span = span
        self.t_out = duration - 0.35

    def alpha(self, t: float) -> float:
        return ease_out(t / self.t_card) * (1 - ease_in_out((t - self.t_out) / 0.35))


def draw_frame(c: Ctx, t: float) -> Image.Image:
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    a = c.alpha(t)
    if a <= 0.001:
        return im
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = c.box
    slide = int((1 - ease_out(t / c.t_card)) * 40)
    y0s, y1s = y0 + slide, y1 + slide
    kind = c.s.get("type", "list")
    if kind == "title":
        _title_card(c, d, t, a)
        return im
    panel(c, im, d, (x0, y0s, x1, y1s), a)
    ta = a * ease_out((t - c.t_card * 0.6) / 0.4)
    pad = 56
    inner = x1 - x0 - 2 * pad
    title = c.s.get("title", "")
    title = title.upper() if c.b.get("upper") else title
    lines, ft = title_lines(d, title, c.b["font_bold"], 66 if c.b.get("upper") else 54, inner)
    lh = int(ft.size * 1.15)
    for i, ln in enumerate(lines):
        T(d, (x0 + pad, y0s + 40 + i * lh), ln, font=ft, fill=rgb(c.b["fg"], int(255 * ta)))
    y = y0s + 40 + len(lines) * lh + 12
    if c.s.get("subtitle"):
        fs = fit(d, c.s["subtitle"], c.b["font"], 30, inner)
        T(d, (x0 + pad, y), c.s["subtitle"], font=fs, fill=rgb(c.b["muted"], int(255 * ta)))
        y += 42
    area = (x0 + pad, max(y0s + 190, y + 40), x1 - pad, y1s - 70)
    {"number": _number, "bars": _bars, "donut": _donut, "list": _list, "compare": _compare, "line": _line}.get(kind, _list)(c, d, t, a, area)
    if c.s.get("note"):
        na = a * ease_out((t - c.t_items[-1] - c.span * 0.6) / 0.4)
        T(d, (area[0], y1s - 60), c.s["note"], font=fit(d, c.s["note"], c.b["font"], 30, area[2] - area[0]), fill=rgb(c.b["muted"], int(255 * na)))
    return im


def panel(c: Ctx, im: Image.Image, d: ImageDraw.ImageDraw, box: tuple, a: float) -> None:
    """The card: plain rounded panel, or the brand's corrugated board with a torn edge."""
    x0, y0, x1, y1 = box
    alpha = int((250 if c.b.get("confirmed") else 235) * a)
    if c.b.get("edge") == "torn" or c.b.get("texture") == "stripes":
        import random
        rnd = random.Random(7)   # same tear on every frame
        layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        top = [(x, y0 + rnd.randint(-9, 9)) for x in range(int(x0), int(x1) + 1, 22)]
        bot = [(x, y1 + rnd.randint(-9, 9)) for x in range(int(x1), int(x0) - 1, -22)]
        ld.polygon(top + bot, fill=rgb(c.b["panel"], 255))
        if c.b.get("texture") == "stripes":   # corrugated cardboard: soft darker ribs, blended (not stamped) onto the board
            ribs = Image.new("RGBA", im.size, (0, 0, 0, 0))
            rd = ImageDraw.Draw(ribs)
            for x in range(int(x0), int(x1), 18):
                rd.rectangle((x, y0 - 12, x + 8, y1 + 12), fill=rgb(c.b["fg"], 30))
            layer.alpha_composite(ribs)
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).polygon(top + bot, fill=alpha)
        layer.putalpha(mask)
        im.alpha_composite(layer)
    else:
        d.rounded_rectangle(box, radius=28, fill=rgb(c.b["panel"], alpha))
    d.rectangle((x0, y0 + 28, x0 + 8, y0 + 110), fill=rgb(c.b["accent"], int(255 * a)))


def _k(c: Ctx, t: float, i: int) -> float:
    return ease_out((t - c.t_items[min(i, c.n - 1)]) / (c.span * 0.8))


def _number(c, d, t, a, area):
    it = c.items[0] if c.items else {"value": 0, "label": ""}
    k = _k(c, t, 0)
    v = (it.get("value") or 0) * k
    txt = fmt_num(v if not float(it.get("value") or 0).is_integer() else round(v), c.s.get("unit", ""))
    final = fmt_num(it.get("value") or 0, c.s.get("unit", ""))
    fb = fit(d, final, c.b["font_bold"], 150, area[2] - area[0], 0.35)
    T(d, (area[0], area[1] + 40), txt, font=fb, fill=rgb(c.b["accent"], int(255 * a * min(1, k * 2))))
    lab = it.get("label", "")
    T(d, (area[0], area[1] + 60 + int(fb.size * 1.2)), lab, font=fit(d, lab, c.b["font"], 36, area[2] - area[0]), fill=rgb(c.b["fg"], int(255 * a * k)))
    for j, extra in enumerate(c.items[1:3], start=1):
        kj = _k(c, t, j)
        T(d, (area[0], area[1] + 320 + (j - 1) * 60), f"{extra.get('label', '')}: {fmt_num(extra.get('value'), c.s.get('unit', ''))} {extra.get('text', '')}".strip(),
               font=c.f_sub, fill=rgb(c.b["muted"], int(255 * a * kj)))


def _bars(c, d, t, a, area):
    vals = [abs(float(i.get("value") or 0)) for i in c.items] or [1]
    vmax = max(vals) or 1
    h = min(96, (area[3] - area[1]) / c.n)
    for i, it in enumerate(c.items):
        k = _k(c, t, i)
        y = area[1] + i * h
        hi = c.s.get("highlight") == i
        T(d, (area[0], y), it.get("label", ""), font=c.f_sub, fill=rgb(c.b["fg"], int(255 * a * k)))
        full = (area[2] - area[0] - 170) * vals[i] / vmax
        d.rounded_rectangle((area[0], y + 40, area[0] + max(6, full * k), y + 40 + h * 0.38), radius=8,
                            fill=rgb(c.b["accent"] if hi or c.s.get("highlight") is None else c.b["muted"], int(255 * a)))
        T(d, (area[0] + full * k + 16, y + 34), fmt_num(it.get("value"), c.s.get("unit", "")), font=c.f_bold, fill=rgb(c.b["fg"], int(255 * a * k)))


def _donut(c, d, t, a, area):
    vals = [max(0.0, float(i.get("value") or 0)) for i in c.items] or [1]
    total = sum(vals) or 1
    cx, cy = area[0] + 230, (area[1] + area[3]) // 2
    r = 210
    start = -90.0
    cols = [c.b["accent"], c.b["accent2"], c.b["muted"], c.b["fg"]]
    for i, v in enumerate(vals):
        k = _k(c, t, i)
        sweep = 360 * v / total * k
        if sweep > 0.5:
            d.pieslice((cx - r, cy - r, cx + r, cy + r), start, start + sweep, fill=rgb(cols[i % 4], int(255 * a)))
        start += 360 * v / total
        ly = area[1] + 20 + i * 92
        d.rectangle((area[0] + 500, ly + 10, area[0] + 528, ly + 38), fill=rgb(cols[i % 4], int(255 * a * k)))
        T(d, (area[0] + 545, ly), c.items[i].get("label", ""), font=c.f_sub, fill=rgb(c.b["fg"], int(255 * a * k)))
        T(d, (area[0] + 545, ly + 38), fmt_num(v, c.s.get("unit", "%")), font=c.f_sub, fill=rgb(c.b["muted"], int(255 * a * k)))
    d.ellipse((cx - r * 0.58, cy - r * 0.58, cx + r * 0.58, cy + r * 0.58), fill=rgb(c.b["panel"], int(255 * a)))
    hi = c.s.get("highlight") if c.s.get("highlight") is not None else 0
    k = _k(c, t, hi)
    pct = f"{round(vals[hi] / total * 100)}%"
    tw = d.textlength(pct, font=c.f_bold)
    T(d, (cx - tw / 2, cy - 24), pct, font=c.f_bold, fill=rgb(c.b["accent"], int(255 * a * k)))


def _list(c, d, t, a, area):
    h = min(110, (area[3] - area[1]) / c.n)
    for i, it in enumerate(c.items):
        k = _k(c, t, i)
        y = area[1] + i * h
        off = int((1 - k) * 30)
        d.ellipse((area[0] + off, y + 18, area[0] + 18 + off, y + 36), fill=rgb(c.b["accent"], int(255 * a * k)))
        txt = it.get("text") or it.get("label", "")
        T(d, (area[0] + 40 + off, y), txt, font=fit(d, txt, c.b["font"], 36, area[2] - area[0] - 40), fill=rgb(c.b["fg"], int(255 * a * k)))


def _compare(c, d, t, a, area):
    h = min(110, (area[3] - area[1]) / c.n)
    mid = area[0] + (area[2] - area[0]) * 0.42
    for i, it in enumerate(c.items):
        k = _k(c, t, i)
        y = area[1] + i * h
        T(d, (area[0], y), it.get("label", ""), font=fit(d, it.get("label", ""), c.b["font"], 36, mid - area[0] - 16), fill=rgb(c.b["muted"], int(255 * a * k)))
        val = it.get("text") or fmt_num(it.get("value"), c.s.get("unit", ""))
        T(d, (mid, y), val, font=fit(d, val, c.b["font_bold"], 40, area[2] - mid),
               fill=rgb(c.b["accent"] if c.s.get("highlight") == i else c.b["fg"], int(255 * a * k)))
        d.line((area[0], y + h - 14, area[2], y + h - 14), fill=rgb(c.b["muted"], int(60 * a * k)), width=2)


def _line(c, d, t, a, area):
    vals = [float(i.get("value") or 0) for i in c.items] or [0, 1]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    x0, y0, x1, y1 = area[0] + 10, area[1] + 20, area[2] - 10, area[3] - 70
    pts = [(x0 + (x1 - x0) * i / max(1, len(vals) - 1), y1 - (y1 - y0) * (v - lo) / rng) for i, v in enumerate(vals)]
    d.line((x0, y1, x1, y1), fill=rgb(c.b["muted"], int(90 * a)), width=2)
    # draw progressively along the polyline (ease in-out: a continuous draw)
    prog = ease_in_out((t - c.t_items[0]) / max(0.8, c.span * c.n * 0.9)) * (len(pts) - 1)
    for i in range(len(pts) - 1):
        f = max(0.0, min(1.0, prog - i))
        if f <= 0:
            break
        xa, ya = pts[i]
        xb, yb = pts[i + 1]
        d.line((xa, ya, xa + (xb - xa) * f, ya + (yb - ya) * f), fill=rgb(c.b["accent"], int(255 * a)), width=8)
    for i, (x, y) in enumerate(pts):
        if prog + 1e-6 >= i:
            d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=rgb(c.b["fg"], int(255 * a)))
            lab = c.items[i].get("label", "")
            tw = d.textlength(lab, font=c.f_sub)
            T(d, (x - tw / 2, y1 + 14), lab, font=c.f_sub, fill=rgb(c.b["muted"], int(255 * a)))
    hl = c.s.get("highlight")
    if hl is not None and 0 <= hl < len(pts) and prog >= hl:
        x, y = pts[hl]
        k = ease_out((t - c.t_items[0] - (hl / max(1, len(pts) - 1)) * c.span * c.n * 0.9) / 0.5)
        d.ellipse((x - 18, y - 18, x + 18, y + 18), outline=rgb(c.b["accent"], int(255 * a * k)), width=5)
        tag = c.items[hl].get("text") or "мы здесь"
        tw = d.textlength(tag, font=c.f_bold)
        tx = min(max(x - tw / 2, area[0]), area[2] - tw)
        d.rounded_rectangle((tx - 14, y - 92, tx + tw + 14, y - 34), radius=12, fill=rgb(c.b["panel"], int(245 * a * k)), outline=rgb(c.b["accent"], int(255 * a * k)), width=2)
        T(d, (tx, y - 88), tag, font=c.f_bold, fill=rgb(c.b["accent"], int(255 * a * k)))


def _title_card(c, d, t, a):
    k = ease_out((t - 0.1) / 0.5)
    x, y = 120, 780
    ft = fit(d, c.s.get("title", ""), c.b["font_bold"], 54, W - 2 * x - 120)
    fs = fit(d, c.s.get("subtitle", ""), c.b["font"], 30, W - 2 * x - 120)
    tw = max(d.textlength(c.s.get("title", ""), font=ft), d.textlength(c.s.get("subtitle", ""), font=fs)) + 120
    d.rounded_rectangle((x, y, x + tw * k, y + 170), radius=18, fill=rgb(c.b["panel"], int(235 * a)))
    d.rectangle((x, y, x + 10, y + 170), fill=rgb(c.b["accent"], int(255 * a)))
    ta = a * ease_out((t - 0.4) / 0.4)
    T(d, (x + 50, y + 26), c.s.get("title", ""), font=ft, fill=rgb(c.b["fg"], int(255 * ta)))
    T(d, (x + 50, y + 104), c.s.get("subtitle", ""), font=fs, fill=rgb(c.b["muted"], int(255 * ta)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slot_dir", type=Path)
    ap.add_argument("--fps", type=float, default=25)
    ap.add_argument("--duration", type=float, default=6)
    ap.add_argument("--poster-only", action="store_true", help="only poster.png (style variants)")
    args = ap.parse_args()
    sd = args.slot_dir
    spec = json.loads((sd / "spec.json").read_text(encoding="utf-8"))
    brand = json.loads((sd / "brand.json").read_text(encoding="utf-8")) if (sd / "brand.json").exists() else {}
    dur = max(2.5, args.duration)
    c = Ctx(spec, brand, dur)
    n = int(round(dur * args.fps))
    if args.poster_only:
        draw_frame(c, min(dur - 1.0, c.t_items[-1] + c.span)).save(sd / "poster.png")
        print("PROGRESS 1.0\nDONE", flush=True)
        return
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", f"{args.fps}", "-i", "-",
           "-map", "0", "-c:v", "png", "-pix_fmt", "rgba", str(sd / "render.part.mov"),
           "-map", "0", "-vf", "scale=960:-2", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "34",
           "-deadline", "realtime", "-cpu-used", "8", "-auto-alt-ref", "0", str(sd / "preview.part.webm")]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)
    poster_t = min(dur - 1.0, c.t_items[-1] + c.span)
    for i in range(n):
        t = i / args.fps
        im = draw_frame(c, t)
        proc.stdin.write(im.tobytes())
        if abs(t - poster_t) < 0.5 / args.fps:
            im.save(sd / "poster.png")
        if i % 10 == 0:
            print(f"PROGRESS {i / n:.3f}", flush=True)
    proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise SystemExit("ОШИБКА ffmpeg: " + " | ".join(err.strip().splitlines()[-3:]))
    (sd / "render.part.mov").replace(sd / "render.mov")
    (sd / "preview.part.webm").replace(sd / "preview.webm")
    if not (sd / "poster.png").exists():
        draw_frame(c, poster_t).save(sd / "poster.png")
    print("PROGRESS 1.0\nDONE", flush=True)


if __name__ == "__main__":
    main()
