"""Brand book from the Obsidian vault + colour presets.

The vault stays the source of truth: a note with a ```yaml cutroom-brand``` block (palette, fonts,
grade). Cutroom only reads it — the vault is found from settings["vault_path"] or Obsidian's own
list of vaults. Without the note the brand is `[NEED: брендбук]` and neutral defaults apply.

Custom colour presets (the user's own) live in %APPDATA%/Cutroom/color_presets.json.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import yaml

from .config import BACKEND_DIR, CONFIG_PATH, load_settings

FONTS_DIR = BACKEND_DIR / "assets" / "fonts"
NOTE_NAME = "Брендбук — визуал.md"
MARK = re.compile(r"```yaml cutroom-brand\s*\n(.*?)```", re.S)
PRESETS_PATH = CONFIG_PATH.parent / "color_presets.json"

NEUTRAL = {"cream": "#F3F2EE", "orange": "#E8A33D", "khaki": "#14161C"}

_cache: dict = {"t": 0.0, "brand": None}


def vault_path() -> Path | None:
    p = load_settings().get("vault_path")
    if p and Path(p).is_dir():
        return Path(p)
    obs = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
    try:
        vaults = json.loads(obs.read_text(encoding="utf-8")).get("vaults", {})
        best = max(vaults.values(), key=lambda v: (bool(v.get("open")), v.get("ts", 0)), default=None)
        if best and Path(best["path"]).is_dir():
            return Path(best["path"])
    except Exception:
        pass
    return None


def _find_note(vault: Path) -> Path | None:
    direct = next(iter(vault.glob(f"*/{NOTE_NAME}")), None) or (vault / NOTE_NAME if (vault / NOTE_NAME).exists() else None)
    if direct:
        return direct
    for f in vault.rglob("*.md"):
        if ".obsidian" in f.parts or "99 Архив" in f.parts:
            continue
        try:
            if "cutroom-brand" in f.read_text(encoding="utf-8", errors="ignore"):
                return f
        except OSError:
            continue
    return None


def load(force: bool = False) -> dict:
    """{found, note, colors, fonts, grade, need} — re-read at most every 5 s (the note may be edited)."""
    if not force and _cache["brand"] and time.time() - _cache["t"] < 5:
        return _cache["brand"]
    out = {"found": False, "note": None, "vault": None, "colors": dict(NEUTRAL), "fonts": {}, "grade": None,
           "need": "[NEED: брендбук] — в хранилище Obsidian нет заметки с блоком cutroom-brand"}
    v = vault_path()
    if v:
        out["vault"] = str(v)
        note = _find_note(v)
        if note:
            m = MARK.search(note.read_text(encoding="utf-8", errors="ignore"))
            if m:
                try:
                    data = yaml.safe_load(m.group(1)) or {}
                    out.update({"found": True, "note": str(note), "need": None,
                                "colors": {**NEUTRAL, **(data.get("colors") or {})},
                                "fonts": data.get("fonts") or {}, "grade": data.get("grade")})
                except yaml.YAMLError as e:
                    out["need"] = f"[NEED: брендбук] — блок cutroom-brand не читается: {e}"
    _cache.update(t=time.time(), brand=out)
    return out


def font_path(spec: str | None, fallback: str) -> str:
    """'File.ttf@800' from the note → absolute path (+ weight) when the bundled font exists."""
    if not spec:
        return fallback
    name, _, weight = spec.partition("@")
    p = FONTS_DIR / name
    return (str(p) + (f"@{weight}" if weight else "")) if p.exists() else fallback


# card styles offered in the variants dialog — all from the brand palette and its pairing rules
STYLES = {
    "cream": "Крем",
    "orange": "Оранжевый",
    "khaki": "Хаки",
    "carton": "Картон",
}


def gfx_brand(style: str = "cream") -> dict:
    b = load()
    c, f = b["colors"], b["fonts"]
    head = font_path(f.get("heading"), "seguisb.ttf")
    body = font_path(f.get("body"), "segoeui.ttf")
    accent_font = font_path(f.get("accent"), body)
    cream, orange, khaki = c["cream"], c["orange"], c["khaki"]
    base = {"font": body, "font_bold": head, "font_accent": accent_font, "upper": bool(f.get("heading")), "confirmed": b["found"]}
    if style == "orange":   # on orange: khaki, bold
        return {**base, "panel": orange, "fg": khaki, "muted": _mix(khaki, orange, 0.72), "accent": khaki, "accent2": cream}
    if style == "khaki":    # on khaki: cream text, orange only large
        return {**base, "panel": khaki, "fg": cream, "muted": _mix(cream, khaki, 0.7), "accent": orange, "accent2": cream}
    if style == "carton":   # corrugated orange board with a torn edge
        return {**base, "panel": orange, "fg": khaki, "muted": _mix(khaki, orange, 0.72), "accent": khaki, "accent2": cream,
                "texture": "stripes", "edge": "torn"}
    # cream: khaki text, orange only as shapes
    return {**base, "panel": cream, "fg": khaki, "muted": _mix(khaki, cream, 0.68), "accent": orange, "accent2": khaki}


def _mix(a: str, b: str, k: float) -> str:
    pa = [int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    pb = [int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x * k + y * (1 - k)):02x}" for x, y in zip(pa, pb))


# ---------------------------------------------------------------- colour presets


def brand_grade() -> dict | None:
    g = load().get("grade")
    if not g:
        return None
    return {"cdl": {"slope": g.get("slope", [1, 1, 1]), "offset": g.get("offset", [0, 0, 0]), "power": g.get("power", [1, 1, 1]),
                    "sat": g.get("sat", 1.0)}, "temperature": g.get("temperature", 0)}


def presets() -> list[dict]:
    """Ready presets (brand first, then video-use's) + the user's own."""
    from .color import PRESET_NAMES
    out = []
    b = load()
    bg = brand_grade()
    if bg:
        out.append({"id": "brand", "name": (b.get("grade") or {}).get("name") or "Бренд", "grade": bg, "kind": "brand"})
    from .kb import ref_preset
    ref = ref_preset()   # the grade of the last render in the knowledge base
    if ref:
        out.append(ref)
    for k, name in PRESET_NAMES.items():
        out.append({"id": k, "name": name, "grade": None if k == "none" else {"preset": k}, "kind": "ready"})
    for p in _user_presets():
        out.append({**p, "kind": "user"})
    return out


def _user_presets() -> list[dict]:
    try:
        return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_preset(name: str, grade: dict) -> dict:
    items = [p for p in _user_presets() if p["name"] != name]
    p = {"id": "u" + str(int(time.time() * 1000)), "name": name[:40], "grade": grade}
    items.append(p)
    PRESETS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def delete_preset(pid: str) -> None:
    PRESETS_PATH.write_text(json.dumps([p for p in _user_presets() if p["id"] != pid], ensure_ascii=False, indent=1), encoding="utf-8")


def grade_of(pid: str) -> dict | None:
    return next((p["grade"] for p in presets() if p["id"] == pid), None)
