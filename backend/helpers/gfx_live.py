"""The live infographic layer: one full-frame RGBA layer over the film, drawn as a function of time.

Modelled on the brand reference («Делай движ», «План запуска»): a roadmap band (title, time line by
months, date capsules, the launch circle with pulse rings) whose geometry follows the shot in frame —
G_BAND over the speaker's head on the front camera, G_COMP a narrow band over the board on the top
camera, G_FULL the whole cream slide in «full» windows — with an ease-in-out morph between them
(0.7 s); step / stage cards (orange «Шаг N» plaque, «ОБЯЗАТЕЛЬНО» tags, dates in an orange outline)
that rise in with a fade (0.45 s) and keep clear of the speaker (side columns on the front, 63 % at
the sides of the board on the top); an intro (cream, title left, poster right), a CTA page and brand
subtitles (Jura 700, cream on a khaki plate, digits orange) on top.

Every time here is already resolved to seconds of the finished film (cutroom.quick.live): the layer
JSON holds the elements, the camera role per stretch of time and the subtitle phrases.

    python gfx_live.py <slice.json>        → slice["out"] (qtrle .mov RGBA, or VP9-alpha .webm for previews)
slice = {layer, w, h, fps, t0, n, out, codec: qtrle|vp9}

Frames are drawn in a process pool (6 processes, chunksize 2, maxtasksperchild — a 4K RGBA frame is
33 MB, bigger chunks ran out of memory on the reference); frames where nothing moves are drawn once.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
FONTS = HERE.parent / "assets" / "fonts"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
APPEAR, MORPH, FADE = 0.45, 0.7, 0.3
PULSE_FOR = 4.0
CREAM, ORANGE, KHAKI = "#FFF9EB", "#FF7700", "#454335"

L: dict = {}     # the layer (worker-global)
S = 1.0          # size multiplier: 1 = 1920×1080
W = H = 0


# ---------------------------------------------------------------- basics


def rgb(h: str, a: float = 1.0) -> tuple:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), max(0, min(255, int(round(255 * a)))))


class BD:
    """ImageDraw that BLENDS on an RGBA frame. Pillow writes a translucent colour's alpha straight
    into the pixels — a fading capsule or a 16 % line punched see-through holes in the cream panels.
    Here every primitive is drawn opaque into a small tile around it, the tile is multiplied by the
    colour's alpha and alpha-composited onto the frame."""

    def __init__(self, im: Image.Image):
        self.im = im
        self.m = ImageDraw.Draw(im)

    def textlength(self, s, font):
        return self.m.textlength(s, font=font)

    def textbbox(self, xy, s, font, anchor="la", stroke_width=0):
        return self.m.textbbox(xy, s, font=font, anchor=anchor, stroke_width=stroke_width)

    def _put(self, box, alpha: int, fn) -> None:
        if alpha <= 0:
            return
        x0, y0, x1, y1 = (int(math.floor(box[0])) - 2, int(math.floor(box[1])) - 2, int(math.ceil(box[2])) + 3, int(math.ceil(box[3])) + 3)
        cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(self.im.width, x1), min(self.im.height, y1)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        tile = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        fn(ImageDraw.Draw(tile), -x0, -y0)
        if alpha < 255:
            tile.putalpha(tile.getchannel("A").point(lambda v: v * alpha // 255))
        tile = tile.crop((cx0 - x0, cy0 - y0, cx1 - x0, cy1 - y0))
        self.im.alpha_composite(tile, (cx0, cy0))

    @staticmethod
    def _op(c):
        return None if c is None else tuple(c[:3]) + (255,)

    @staticmethod
    def _a(*cs) -> int:
        return max([c[3] if len(c) > 3 else 255 for c in cs if c is not None] or [0])

    @staticmethod
    def _sh(pts, dx, dy):
        return [(x + dx, y + dy) for x, y in pts]

    def line(self, xy, fill=None, width=1):
        x0, y0, x1, y1 = xy
        w = width / 2 + 1
        self._put((min(x0, x1) - w, min(y0, y1) - w, max(x0, x1) + w, max(y0, y1) + w), self._a(fill),
                  lambda d, dx, dy: d.line((x0 + dx, y0 + dy, x1 + dx, y1 + dy), fill=self._op(fill), width=width))

    def rounded_rectangle(self, box, radius=0, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = box
        if x1 < x0 or y1 < y0:
            return
        self._put(box, self._a(fill, outline), lambda d, dx, dy: d.rounded_rectangle((x0 + dx, y0 + dy, x1 + dx, y1 + dy), radius=radius,
                                                                                      fill=self._op(fill), outline=self._op(outline), width=width))

    def rectangle(self, box, fill=None, outline=None, width=1):
        self.rounded_rectangle(box, 0, fill, outline, width)

    def ellipse(self, box, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = box
        if x1 < x0 or y1 < y0:
            return
        self._put(box, self._a(fill, outline), lambda d, dx, dy: d.ellipse((x0 + dx, y0 + dy, x1 + dx, y1 + dy), fill=self._op(fill),
                                                                            outline=self._op(outline), width=width))

    def polygon(self, pts, fill=None):
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        self._put((min(xs), min(ys), max(xs), max(ys)), self._a(fill), lambda d, dx, dy: d.polygon(self._sh(pts, dx, dy), fill=self._op(fill)))

    def text(self, xy, s, font=None, fill=None, anchor="la", stroke_width=0, stroke_fill=None):
        if not s:
            return
        box = self.m.textbbox(xy, s, font=font, anchor=anchor, stroke_width=stroke_width)
        self._put(box, self._a(fill), lambda d, dx, dy: d.text((xy[0] + dx, xy[1] + dy), s, font=font, fill=self._op(fill), anchor=anchor,
                                                              stroke_width=stroke_width, stroke_fill=self._op(stroke_fill)))


def eio(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def eout(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def eback(x: float) -> float:   # capsules «grow» with a small overshoot
    x = max(0.0, min(1.0, x))
    c = 1.4
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def col(key: str) -> str:
    return (L.get("brand") or {}).get(key) or {"cream": CREAM, "orange": ORANGE, "khaki": KHAKI}[key]


@lru_cache(maxsize=256)
def font(role: str, px: int) -> ImageFont.FreeTypeFont:
    spec = ((L.get("brand") or {}).get("fonts") or {}).get(role) or {
        "head": "Unbounded[wght].ttf@700", "head5": "Unbounded[wght].ttf@500", "body": "Jura[wght].ttf@500",
        "bold": "Jura[wght].ttf@700", "caps": "Comfortaa[wght].ttf@700"}[role]
    name, _, wght = spec.partition("@")
    p = Path(name) if Path(name).is_absolute() else FONTS / name
    f = ImageFont.truetype(str(p), max(6, int(px)))
    if wght:
        try:
            f.set_variation_by_axes([int(wght)])
        except (OSError, ValueError):
            pass
    return f


@lru_cache(maxsize=64)
def _cmap(path: str) -> frozenset:
    try:
        from fontTools.ttLib import TTFont
        return frozenset(TTFont(path, lazy=True).getBestCmap())
    except Exception:  # noqa: BLE001
        return frozenset()


def text(d: ImageDraw.ImageDraw, xy, s: str, f: ImageFont.FreeTypeFont, fill, anchor: str = "la", stroke: int = 0) -> None:
    """Text with a per-glyph fallback (Jura has no «→»: drawn with Comfortaa on the same baseline)."""
    have = _cmap(f.path)
    if not have or all(ord(c) in have or c.isspace() for c in s):
        d.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=fill if stroke else None)
        return
    alt = font("caps", f.size)
    w = sum(d.textlength(c, font=f if ord(c) in have else alt) for c in s)
    x, y = xy
    if anchor[0] == "m":
        x -= w / 2
    elif anchor[0] == "r":
        x -= w
    for c in s:
        g = f if ord(c) in have or c.isspace() else alt
        d.text((x, y), c, font=g, fill=fill, anchor="l" + anchor[1], stroke_width=stroke, stroke_fill=fill if stroke else None)
        x += d.textlength(c, font=g)


def wrap(d, s: str, f, maxw: float, lines: int = 3) -> list[str]:
    out, cur = [], ""
    for w in s.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= maxw or not cur:
            cur = t
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(".,") + "…"
    return out


def shadowed_panel(im: Image.Image, box, r: float, fill, a: float, shadow: bool = True) -> None:
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 - x0 < 2 or y1 - y0 < 2 or a <= 0.01:
        return
    if shadow:
        pad = int(30 * S)
        sh = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((pad, pad + int(8 * S), pad + x1 - x0, pad + y1 - y0 + int(8 * S)), radius=int(r), fill=rgb(col("khaki"), 0.28 * a))
        sh = sh.filter(ImageFilter.GaussianBlur(14 * S))
        im.alpha_composite(sh, (x0 - pad, y0 - pad))
    BD(im).rounded_rectangle((x0, y0, x1, y1), radius=int(r), fill=fill[:3] + (int(fill[3] * a),))


# ---------------------------------------------------------------- geometry (1920×1080 units, scaled by S)

# rows inside the band, top to bottom: title · month capsules · the line (launch circle on it) · date capsules · captions
G_BAND = {"x0": 330, "y0": 28, "x1": 1590, "y1": 280, "title": 30, "line": 180, "cap": 19, "r": 26, "card": 1.0, "full": 0.0}
G_COMP = {"x0": 250, "y0": 14, "x1": 1670, "y1": 188, "title": 20, "line": 126, "cap": 15, "r": 20, "card": 0.63, "full": 0.0}
G_FULL = {"x0": 110, "y0": 140, "x1": 1810, "y1": 400, "title": 46, "line": 300, "cap": 24, "r": 0, "card": 1.0, "full": 1.0}
GEOMS = {"band": G_BAND, "comp": G_COMP, "full": G_FULL}


def lerp(a: dict, b: dict, k: float) -> dict:
    return {key: a[key] + (b[key] - a[key]) * k for key in a}


def role_at(t: float) -> str:
    for a in L.get("angles") or []:
        if a[0] <= t < a[1]:
            return a[2]
    return "front"


def window_at(t: float) -> dict | None:
    for w in L.get("windows") or []:
        if w["t"] <= t < w["t1"]:
            return w
    return None


def target(t: float) -> str:
    w = window_at(t)
    if w:
        return "full" if w["kind"] == "full" else "band"
    return "comp" if role_at(t) in ("top", "side") else "band"


def changes() -> list[float]:
    pts = {a[0] for a in L.get("angles") or []} | {w["t"] for w in L.get("windows") or []} | {w["t1"] for w in L.get("windows") or []}
    return sorted(pts)


_CH: list = []


def geom(t: float) -> tuple[dict, str, str, float]:
    """(geometry, from, to, k): the shot's geometry with an ease-in-out morph after each change."""
    if not _CH:
        _CH.append(changes())
    to = target(t)
    last = max((c for c in _CH[0] if c <= t), default=None)
    if last is None or t - last >= MORPH:
        return GEOMS[to], to, to, 1.0
    fr = target(last - 1e-3)
    k = eio((t - last) / MORPH)
    return lerp(GEOMS[fr], GEOMS[to], k), fr, to, k


def sc(v: float) -> int:
    return int(round(v * S))


# ---------------------------------------------------------------- the band


def appear(t: float, t0: float | None, dur: float = APPEAR) -> float:
    if t0 is None:
        return 0.0
    return eout((t - t0) / dur)


def band_alpha(t: float) -> float:
    st = L.get("start")
    if st is None or t < st:
        return 0.0
    a = eout((t - st) / 0.6)
    cta = L.get("cta")
    if cta and t >= cta["t0"]:
        a *= 1 - eout((t - cta["t0"]) / 0.5)
    intro = L.get("intro")
    if intro and t < intro["t1"]:
        a = 0.0
    return a


def month_cols(g: dict) -> list[tuple[float, float]]:
    ms = L.get("months") or []
    n = max(1, len(ms))
    x0, x1 = g["x0"] + (40 if g["full"] < 0.5 else 70), g["x1"] - 40
    tx = x0 + (x1 - x0) * 0.0
    w = (x1 - tx) / n
    return [(tx + i * w, tx + (i + 1) * w) for i in range(n)]


def draw_band(im: Image.Image, d: ImageDraw.ImageDraw, t: float, g: dict, a: float) -> None:
    if a <= 0.01:
        return
    cream, orange, khaki = col("cream"), col("orange"), col("khaki")
    full = g["full"]
    box = (sc(g["x0"]), sc(g["y0"]), sc(g["x1"]), sc(g["y1"]))
    if full < 0.999:
        shadowed_panel(im, box, sc(g["r"]), rgb(cream), a * (1 - full), shadow=True)
    d = BD(im)
    # title
    title = L.get("title") or ""
    ft = font("head", sc(g["title"]))
    ty = (g["y0"] + 22) * (1 - full) + (g["y0"] - 100) * full   # on the band: inside it; on the full slide: above the line block
    text(d, (sc(g["x0"] + 36), sc(ty)), title, ft, rgb(khaki, a))
    cols = month_cols(g)
    ms = L.get("months") or []
    ly = sc(g["line"])
    # the line draws left → right up to the last month that has been named
    named = [i for i, m in enumerate(ms) if m.get("t") is not None and t >= m["t"]]
    if cols:
        reach = cols[max(named)][1] if named else cols[0][0]
        if named:
            m = ms[max(named)]
            k = eout((t - m["t"]) / 1.0)
            prev = cols[max(named) - 1][1] if max(named) > 0 else cols[0][0]
            reach = prev + (cols[max(named)][1] - prev) * k
        d.line((sc(cols[0][0]), ly, sc(cols[-1][1]), ly), fill=rgb(khaki, 0.16 * a), width=max(2, sc(4)))
        if named:
            d.line((sc(cols[0][0]), ly, sc(reach), ly), fill=rgb(orange, a), width=max(2, sc(6)))
    # month capsules above the line + dashed separators (longer on the full slide)
    capf = font("caps", sc(g["cap"]))
    for i, m in enumerate(ms):
        if i >= len(cols):
            break
        k = appear(t, m.get("t"), 0.5)
        if k <= 0:
            continue
        cx = (cols[i][0] + cols[i][1]) / 2
        if i > 0:
            yb = g["y1"] + (560 if full > 0.5 else 0) * full
            for yy in range(int(g["line"] - 70 * (g["cap"] / 19)), int(yb), 16):
                d.line((sc(cols[i][0]), sc(yy), sc(cols[i][0]), sc(yy + 8)), fill=rgb(khaki, 0.3 * a * k), width=max(1, sc(2)))
        lab = m["label"].upper()
        tw = d.textlength(lab, font=capf) + sc(36)
        s = eback(k)
        h = sc(g["cap"] * 1.9) * s
        cy = sc(g["line"] - g["cap"] * 3.0)
        d.rounded_rectangle((sc(cx) - tw * s / 2, cy - h / 2, sc(cx) + tw * s / 2, cy + h / 2), radius=h / 2, fill=rgb(khaki, a * k))
        if s > 0.6:
            text(d, (sc(cx), cy), lab, capf, rgb(cream, a * k), anchor="mm")
    # marks on the line: dates (orange outline), launch circle, «now» tail
    for mk in L.get("marks") or []:
        k = appear(t, mk.get("t"), 0.5)
        if k <= 0 or not cols:
            continue
        i = max(0, min(len(cols) - 1, int(mk.get("month") or 0)))
        x = sc(cols[i][0] + (cols[i][1] - cols[i][0]) * float(mk.get("pos", 0.5)))
        s = eback(k)
        if mk["kind"] == "launch":
            draw_launch(im, d, t, x, ly, g, a * k, s, mk)
        elif mk["kind"] == "now":
            f = font("caps", sc(g["cap"]))
            lab = (mk.get("text") or "сейчас").upper()
            tw = d.textlength(lab, font=f) + sc(26)
            y = ly + sc(g["cap"] * 1.7)
            d.polygon([(x, ly + sc(6)), (x - sc(9), y - sc(12)), (x + sc(9), y - sc(12))], fill=rgb(khaki, a * k))
            d.rounded_rectangle((x - tw / 2 * s, y - sc(12), x + tw / 2 * s, y + sc(g["cap"] * 1.5) - sc(12)), radius=sc(14), fill=rgb(khaki, a * k))
            if s > 0.6:
                text(d, (x, y - sc(12) + sc(g["cap"] * 0.75)), lab, f, rgb(cream, a * k), anchor="mm")
        else:
            f = font("head5", sc(g["cap"] * 1.05))
            lab = mk.get("text") or ""
            tw = d.textlength(lab, font=f) + sc(30)
            hh = sc(g["cap"] * 1.9)
            y = ly + sc(g["cap"] * 1.6) + hh / 2
            d.ellipse((x - sc(7), ly - sc(7), x + sc(7), ly + sc(7)), fill=rgb(orange, a * k))
            d.rounded_rectangle((x - tw / 2 * s, y - hh / 2 * s, x + tw / 2 * s, y + hh / 2 * s), radius=hh / 2, fill=rgb(cream, a * k),
                                outline=rgb(orange, a * k), width=max(2, sc(3)))
            if s > 0.6:
                text(d, (x, y), lab, f, rgb(khaki, a * k), anchor="mm")
            if mk.get("caption") and g["cap"] > 16.5:
                cf = font("caps", sc(g["cap"] * 0.7))
                text(d, (x, y + hh / 2 + sc(14)), mk["caption"].upper(), cf, rgb(khaki, 0.8 * a * k), anchor="mm")


def draw_launch(im, d, t, x, y, g, a, s, mk) -> None:
    orange, cream, khaki = col("orange"), col("cream"), col("khaki")
    r = sc(g["cap"] * 1.7) * s
    hl = mk.get("ht")
    lit = hl is not None and t >= hl
    if lit:   # pulse rings for a few seconds after the moment it is named as the main thing
        dt = t - hl
        if dt < PULSE_FOR:
            for ph in (0.0, 0.5):
                p = ((dt / 1.0) + ph) % 1.0
                rr = r * (1 + 1.3 * p)
                d.ellipse((x - rr, y - rr, x + rr, y + rr), outline=rgb(orange, a * (1 - p) * 0.8), width=max(2, sc(4)))
    k = eout((t - hl) / 0.4) if lit else 0.0
    fill = tuple(int(c0 + (c1 - c0) * k) for c0, c1 in zip(rgb(cream)[:3], rgb(orange)[:3])) + (int(255 * a),)
    d.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=rgb(orange, a), width=max(2, sc(5)))
    if s > 0.5:
        f = font("head", sc(g["cap"] * 1.25))
        text(d, (x, y), mk.get("text") or "ДД", f, rgb(cream if k > 0.5 else khaki, a), anchor="mm")
        if mk.get("caption"):
            cf = font("caps", sc(g["cap"] * 0.75))
            text(d, (x, y + r + sc(16)), mk["caption"].upper(), cf, rgb(orange if k > 0.5 else khaki, a), anchor="mm")


# ---------------------------------------------------------------- cards


CARD_W, CARD_H = 400, 262   # CARD_H: the typical height, for planning the full-slide grid
_MEASURE = ImageDraw.Draw(Image.new("RGBA", (8, 8)))


def card_lines(c: dict, w: float = CARD_W) -> tuple[list[str], list[str]]:
    """Title and text lines of a card at scale 1 (1920-wide units)."""
    inner = w - 48
    return (wrap(_MEASURE, c.get("title") or "", _unit_font("bold", 33), inner, 2),
            wrap(_MEASURE, c.get("text") or "", _unit_font("body", 25), inner, 3) if c.get("text") else [])


@lru_cache(maxsize=16)
def _unit_font(role: str, px: int):
    global S
    keep, S = S, 1.0
    try:
        return font(role, px)
    finally:
        S = keep


def card_size(c: dict) -> tuple[float, float]:
    """The height follows the content: plaque, title lines, text lines, the row of date / tag / note."""
    tl, xl = card_lines(c)
    h = 24 + (48 if c.get("group") == "steps" and c.get("n") else 22) + 38 * len(tl) + (30 * len(xl) + 8 if xl else 0)
    if c.get("tag") or c.get("note") or c.get("date"):
        h += 16 + 32
    return CARD_W, h + 24


def draw_card(im: Image.Image, c: dict, x: float, y: float, scale: float, a: float) -> None:
    if a <= 0.01 or scale <= 0.05:
        return
    cream, orange, khaki = col("cream"), col("orange"), col("khaki")
    w, h = card_size(c)
    w, h = w * scale, h * scale
    shadowed_panel(im, (sc(x), sc(y), sc(x + w), sc(y + h)), sc(22 * scale), rgb(cream), a)
    d = BD(im)
    pad = 24 * scale
    cy = y + pad
    if c.get("group") == "steps" and c.get("n"):
        f = font("caps", sc(17 * scale))
        lab = f"ШАГ {c['n']}"
        tw = d.textlength(lab, font=f) / S + 30 * scale
        d.rounded_rectangle((sc(x + pad), sc(cy), sc(x + pad + tw), sc(cy + 34 * scale)), radius=sc(17 * scale), fill=rgb(orange, a))
        text(d, (sc(x + pad + tw / 2), sc(cy + 17 * scale)), lab, f, rgb(cream, a), anchor="mm")
        cy += 48 * scale
    else:
        d.rounded_rectangle((sc(x + pad), sc(cy), sc(x + pad + 46 * scale), sc(cy + 7 * scale)), radius=sc(4), fill=rgb(orange, a))
        cy += 22 * scale
    tl, xl = card_lines(c)
    ft = font("bold", sc(33 * scale))
    for ln in tl:
        text(d, (sc(x + pad), sc(cy)), ln, ft, rgb(khaki, a))
        cy += 38 * scale
    if xl:
        fb = font("body", sc(25 * scale))
        for ln in xl:
            text(d, (sc(x + pad), sc(cy + 4 * scale)), ln, fb, rgb(khaki, 0.88 * a))
            cy += 30 * scale
        cy += 8 * scale
    by = cy + 16 * scale
    bx = x + pad
    if c.get("date"):
        f = font("head5", sc(17 * scale))
        tw = d.textlength(c["date"], font=f) / S + 26 * scale
        d.rounded_rectangle((sc(bx), sc(by), sc(bx + tw), sc(by + 32 * scale)), radius=sc(16 * scale), outline=rgb(orange, a), width=max(2, sc(2.5 * scale)))
        text(d, (sc(bx + tw / 2), sc(by + 16 * scale)), c["date"], f, rgb(khaki, a), anchor="mm")
        bx += tw + 10 * scale
    if c.get("tag"):
        f = font("caps", sc(15 * scale))
        lab = c["tag"].upper()
        tw = d.textlength(lab, font=f) / S + 24 * scale
        d.rounded_rectangle((sc(bx), sc(by), sc(bx + tw), sc(by + 32 * scale)), radius=sc(16 * scale), fill=rgb(khaki, a))
        text(d, (sc(bx + tw / 2), sc(by + 16 * scale)), lab, f, rgb(cream, a), anchor="mm")
        bx += tw + 10 * scale
    if c.get("note"):
        f = font("head5", sc(18 * scale))
        text(d, (sc(bx + 2), sc(by + 16 * scale)), c["note"], f, rgb(orange, a), anchor="lm")


def card_visible(c: dict, t: float) -> float:
    if c.get("t") is None or t < c["t"]:
        return 0.0
    a = eout((t - c["t"]) / APPEAR)
    if c.get("t1") is not None and t >= c["t1"]:
        a *= 1 - eout((t - c["t1"]) / FADE)
    return a


def side_layout(cards: list[dict], g: dict) -> dict[int, tuple[float, float, float]]:
    """Cards beside the speaker (front) or beside the board (top): left column, right column."""
    out = {}
    s = g["card"]
    left = [c for c in cards if c.get("side") == "left"]
    right = [c for c in cards if c.get("side") == "right"]
    auto = [c for c in cards if c.get("side") not in ("left", "right")]
    half = (len(auto) + 1) // 2
    left += auto[:half]
    right += auto[half:]
    top = g["y1"] + 34
    for colm, xs in ((left, 36), (right, 1920 - 36 - CARD_W * s)):
        y = top
        for c in colm:
            out[id(c)] = (xs, y, s)
            y += card_size(c)[1] * s + 18
    return out


def grid_layout(cards: list[dict], g: dict) -> dict[int, tuple[float, float, float]]:
    """The full slide: stages under their months (MCOL), one size for all cards on the slide."""
    out = {}
    cols = month_cols(g)
    if not cols:
        return out
    per: dict[int, list] = {}
    for i, c in enumerate(cards):
        m = c.get("month")
        m = int(m) if m is not None else min(len(cols) - 1, i * len(cols) // max(1, len(cards)))
        per.setdefault(max(0, min(len(cols) - 1, m)), []).append(c)
    room = 1040 - g["y1"] - 30
    wcol = cols[0][1] - cols[0][0] - 28
    # one column of cards under a month when it fits large enough, two side by side otherwise
    plan = {}
    hs = {id(c): card_size(c)[1] for c in cards}
    for m, cs in per.items():
        n = len(cs)
        tall1 = sum(hs[id(c)] for c in cs) + (n - 1) * 14
        rows2 = [max(hs[id(c)] for c in cs[i:i + 2]) for i in range(0, n, 2)]
        s1 = min(1.0, wcol / CARD_W, room / tall1)
        s2 = min(1.0, (wcol - 14) / 2 / CARD_W, room / (sum(rows2) + (len(rows2) - 1) * 14))
        plan[m] = (1, s1) if s1 >= 0.72 or n == 1 else (2, s2)
    s = min(v[1] for v in plan.values())
    for m, cs in per.items():
        across = plan[m][0]
        x0 = cols[m][0] + 14
        y = g["y1"] + 30
        for i in range(0, len(cs), across):
            row = cs[i:i + across]
            for q, c in enumerate(row):
                out[id(c)] = (x0 + q * (CARD_W * s + 14), y, s)
            y += max(hs[id(c)] for c in row) * s + 14
    return out


def draw_cards(im: Image.Image, t: float, gm: tuple) -> None:
    g, fr, to, k = gm
    w = window_at(t)
    cards = L.get("cards") or []
    if w and w["kind"] == "full":
        vis = [c for c in cards if w["group"] != "none" and c.get("group") == w["group"] and c.get("t") is not None and c["t"] <= w["t1"]]
        a_full = eout((t - w["t"]) / MORPH) * (1 - eout((t - (w["t1"] - FADE)) / FADE))
        pos = grid_layout(vis, G_FULL)
        for i, c in enumerate(vis):
            x, y, s = pos[id(c)]
            a = a_full * eout((t - max(w["t"] + 0.3 + i * 0.12, c["t"])) / APPEAR)
            draw_card(im, c, x, y + (1 - a) * 24, s, a)
        return
    vis = [c for c in cards if card_visible(c, t) > 0]
    if not vis:
        return
    pos_to = side_layout(vis, GEOMS[to])
    pos_fr = side_layout(vis, GEOMS[fr]) if fr != to and fr != "full" else pos_to
    for c in vis:
        a = card_visible(c, t)
        x0, y0, s0 = pos_fr[id(c)]
        x1, y1, s1 = pos_to[id(c)]
        kk = k if fr != to else 1.0
        x, y, s = x0 + (x1 - x0) * kk, y0 + (y1 - y0) * kk, s0 + (s1 - s0) * kk
        if fr == "full" and kk < 1:
            a *= kk
        draw_card(im, c, x, y + (1 - eout((t - c["t"]) / APPEAR)) * 24, s, a * band_alpha(t))


# ---------------------------------------------------------------- full-frame pages: intro, CTA


def poster(path: str, w: int, h: int):
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    im.thumbnail((w, h))
    return im


def draw_intro(im: Image.Image, t: float) -> None:
    it = L.get("intro")
    if not it or not (it["t0"] <= t < it["t1"] + 0.5):
        return
    a = 1 - eout((t - (it["t1"] - 0.5)) / 0.5) if t > it["t1"] - 0.5 else 1.0
    cream, orange, khaki = col("cream"), col("orange"), col("khaki")
    bg = Image.new("RGBA", im.size, rgb(cream, a))
    im.alpha_composite(bg)   # alpha_composite: a plain paste would wipe the alpha and show the footage through
    d = BD(im)
    k = eout((t - it["t0"]) / 0.8)
    d.rounded_rectangle((sc(140), sc(330), sc(152), sc(560)), radius=sc(6), fill=rgb(orange, a * k))
    ft = font("head", sc(64))
    y = 330
    for ln in wrap(d, it.get("title") or "", ft, sc(860), 3):
        text(d, (sc(190), sc(y + (1 - k) * 30)), ln, ft, rgb(khaki, a * k))
        y += 82
    if it.get("subtitle"):
        fb = font("body", sc(32))
        k2 = eout((t - it["t0"] - 0.35) / 0.6)
        for ln in wrap(d, it["subtitle"], fb, sc(820), 3):
            text(d, (sc(192), sc(y + 22 + (1 - k2) * 20)), ln, fb, rgb(khaki, 0.85 * a * k2))
            y += 42
    if it.get("poster"):
        p = _poster_cache(it["poster"], sc(760), sc(760))
        if p is not None:
            k3 = eout((t - it["t0"] - 0.2) / 0.9)
            x, yy = sc(1780) - p.width, (im.height - p.height) // 2 + int((1 - k3) * sc(40))
            shadowed_panel(im, (x - sc(14), yy - sc(14), x + p.width + sc(14), yy + p.height + sc(14)), sc(24), rgb("#FFFFFF"), a * k3)
            pp = p.copy()
            pp.putalpha(pp.getchannel("A").point(lambda v: int(v * a * k3)))
            im.alpha_composite(pp, (x, yy))


@lru_cache(maxsize=4)
def _poster_cache(path: str, w: int, h: int):
    return poster(path, w, h)


def draw_cta(im: Image.Image, t: float) -> None:
    c = L.get("cta")
    if not c or t < c["t0"]:
        return
    a = eout((t - c["t0"]) / 0.6)
    cream, orange, khaki = col("cream"), col("orange"), col("khaki")
    im.alpha_composite(Image.new("RGBA", im.size, rgb(cream, a)))
    d = BD(im)
    ft = font("head", sc(58))
    y = 200
    for ln in wrap(d, c.get("title") or "", ft, sc(1500), 2):
        text(d, (sc(210), sc(y)), ln, ft, rgb(khaki, a))
        y += 76
    d.rounded_rectangle((sc(210), sc(y + 16), sc(330), sc(y + 26)), radius=sc(5), fill=rgb(orange, a))
    y += 90
    fl, fv = font("bold", sc(40)), font("head", sc(40))
    for i, ln in enumerate(c.get("lines") or []):
        k = eout((t - c["t0"] - 0.4 - i * 0.25) / APPEAR)
        if k <= 0:
            continue
        yy = y + (1 - k) * 24
        text(d, (sc(210), sc(yy)), ln.get("text") or "", fl, rgb(khaki, a * k), stroke=max(0, sc(0.6)))   # bolder Jura: Jura 700 is its maximum, so a hairline stroke
        if ln.get("value"):
            text(d, (sc(1710), sc(yy)), ln["value"], fv, rgb(orange, a * k), anchor="ra")
        d.line((sc(210), sc(yy + 66), sc(1710), sc(yy + 66)), fill=rgb(khaki, 0.15 * a * k), width=max(1, sc(2)))
        y += 96


# ---------------------------------------------------------------- subtitles


DIGIT = re.compile(r"(\d[\d\s ,.–-]*\d|\d)")


def draw_subs(im: Image.Image, t: float) -> None:
    sub = next((s for s in L.get("subs") or [] if s[0] <= t < s[1]), None)
    if not sub:
        return
    intro = L.get("intro")
    if intro and t < intro["t1"]:
        return
    cream, orange, khaki = col("cream"), col("orange"), col("khaki")
    d = BD(im)
    f = font("bold", sc(44))
    s = sub[2]
    tw = d.textlength(s, font=f)
    x = (im.width - tw) / 2
    y = im.height - sc(118)
    pad = sc(22)
    d.rounded_rectangle((x - pad, y - sc(12), x + tw + pad, y + sc(62)), radius=sc(18), fill=rgb(khaki, 0.92))
    parts = DIGIT.split(s)   # digits orange, words cream
    for i, part in enumerate(parts):
        if not part:
            continue
        text(d, (x, y), part, f, rgb(orange if i % 2 else cream))
        x += d.textlength(part, font=f)


# ---------------------------------------------------------------- one frame


def frame(t: float) -> Image.Image:
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gm = geom(t)
    g = gm[0]
    a = band_alpha(t)
    if g["full"] > 0.001 and a > 0:   # the band opens into the whole cream slide
        im.alpha_composite(Image.new("RGBA", (W, H), rgb(col("cream"), g["full"] * a)))
    d = BD(im)
    draw_band(im, d, t, g, a)
    draw_cards(im, t, gm)
    draw_intro(im, t)
    draw_cta(im, t)
    if L.get("subtitles", True):
        draw_subs(im, t)
    return im


def signature(t: float):
    """Frames with the same signature look the same: drawn once. None = something is moving."""
    def near(t0, span):
        return t0 is not None and t0 - 1e-6 <= t < t0 + span
    for c in _CH[0] if _CH else changes():
        if near(c, MORPH):
            return None
    keys = [L.get("start")] + [m.get("t") for m in L.get("months") or []] + [m.get("t") for m in L.get("marks") or []]
    if any(near(k, 1.0) for k in keys):
        return None
    for m in L.get("marks") or []:
        if near(m.get("ht"), PULSE_FOR + 0.5):
            return None
    for c in L.get("cards") or []:
        if near(c.get("t"), APPEAR + 0.4) or near(c.get("t1"), FADE + 0.05):
            return None
    for w in L.get("windows") or []:
        if near(w["t"], MORPH + 0.3 + 12 * 0.12 + APPEAR) or near(w["t1"] - FADE, FADE + 0.05):
            return None
    for x in (L.get("intro"), L.get("cta")):
        if x and (near(x["t0"], 3.0) or near(x.get("t1", -99) - 0.5, 0.6)):
            return None
    sub = next((i for i, s in enumerate(L.get("subs") or []) if s[0] <= t < s[1]), -1)
    vis = tuple(i for i, c in enumerate(L.get("cards") or []) if card_visible(c, t) > 0)
    named = tuple(i for i, m in enumerate(L.get("months") or []) if m.get("t") is not None and t >= m["t"])
    marks = tuple(i for i, m in enumerate(L.get("marks") or []) if m.get("t") is not None and t >= m["t"])
    lit = tuple(i for i, m in enumerate(L.get("marks") or []) if m.get("ht") is not None and t >= m["ht"])
    return (target(t), id(window_at(t)), sub, vis, named, marks, lit, band_alpha(t) > 0, bool(L.get("cta") and t >= L["cta"]["t0"]))


# ---------------------------------------------------------------- render


def _init(layer: dict, w: int, h: int) -> None:
    global L, S, W, H
    L, W, H, S = layer, w, h, w / 1920
    _CH.clear()
    font.cache_clear()


def _draw(t: float) -> bytes:
    return frame(t).tobytes()


def render(sl: dict) -> None:
    w, h, fps, t0, n = int(sl["w"]), int(sl["h"]), float(sl["fps"]), float(sl["t0"]), int(sl["n"])
    _init(sl["layer"], w, h)
    times = [t0 + i / fps for i in range(n)]
    # consecutive frames with the same signature are drawn once (only the last drawn frame is kept)
    todo, idx, prev = [], [], object()
    for t in times:
        sg = signature(t)
        if sg is None or sg != prev:
            todo.append(t)
        idx.append(len(todo) - 1)
        prev = sg if sg is not None else object()
    out = Path(sl["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    if sl.get("codec") == "frames":   # the live-monitor preview: PNG stills + an index (no second video over the cameras)
        tmp = out.with_name(out.name + ".part")
        tmp.mkdir(parents=True, exist_ok=True)
        procs = max(1, min(6, (os.cpu_count() or 2) - 1, len(todo)))
        with Pool(procs, initializer=_init, initargs=(sl["layer"], w, h), maxtasksperchild=40) as pool:
            for k, b in enumerate(pool.imap(_draw, todo, chunksize=2)):
                Image.frombytes("RGBA", (w, h), b).save(tmp / f"{k:05d}.png", compress_level=1)
                if k % 20 == 0:
                    print(f"PROGRESS {k / max(1, len(todo)):.3f}", flush=True)
        (tmp / "index.json").write_text(json.dumps({"fps": fps, "t0": t0, "frames": idx}), encoding="utf-8")
        if out.exists():
            import shutil
            shutil.rmtree(out, ignore_errors=True)
        tmp.replace(out)
        print(f"DONE {len(todo)}/{n} кадров нарисовано", flush=True)
        return
    part = out.with_name(out.stem + ".part" + out.suffix)
    rate = sl.get("rate") or f"{fps:.6f}"
    if sl.get("codec") == "vp9":
        venc = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "42", "-deadline", "realtime", "-cpu-used", "8", "-auto-alt-ref", "0"]
    else:
        venc = ["-c:v", "qtrle", "-pix_fmt", "argb"]
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(rate), "-i", "-",
                           *venc, str(part)], stdin=subprocess.PIPE, creationflags=NOWIN)
    procs = max(1, min(6, (os.cpu_count() or 2) - 1, len(todo)))
    done = 0
    with Pool(procs, initializer=_init, initargs=(sl["layer"], w, h), maxtasksperchild=40) as pool:
        it = pool.imap(_draw, todo, chunksize=2)
        cur_k, cur = -1, b""
        for k in idx:
            if k != cur_k:
                cur, cur_k = next(it), k
            ff.stdin.write(cur)
            done += 1
            if done % max(1, n // 20) == 0:
                print(f"PROGRESS {done / n:.3f}", flush=True)
    ff.stdin.close()
    if ff.wait() != 0:
        raise SystemExit("ffmpeg: слой инфографики не записан")
    part.replace(out)
    print(f"DONE {len(todo)}/{n} кадров нарисовано", flush=True)


def still(layer: dict, t: float, w: int, h: int, out: str) -> None:
    _init(layer, w, h)
    frame(t).save(out)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--still":
        sl = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        still(sl["layer"], float(sl["t"]), int(sl["w"]), int(sl["h"]), sl["out"])
    else:
        render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
