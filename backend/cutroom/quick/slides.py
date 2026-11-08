"""Presentation → slide images (PDF via PyMuPDF) and their on-screen layouts.

PPTX is not rendered here (no PowerPoint/LibreOffice on the machine): the user saves it as PDF.
Layouts are pre-composed 1920×1080 RGBA images, so the segment renderer only overlays a picture:
  side — the slide as a panel on the right, the speaker stays in frame (default);
  full — the slide over the whole frame on the brand's cream background.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .model import qdir

W, H = 1920, 1080


class SlideError(RuntimeError):
    pass


def import_presentation(project, path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise SlideError(f"Файл не найден: {p.name}")
    ext = p.suffix.lower()
    if ext in (".pptx", ".ppt", ".key", ".odp"):
        raise SlideError("Презентацию нужно сохранить как PDF: в PowerPoint «Файл → Экспорт → PDF», "
                         "в Google Slides «Файл → Скачать → PDF». Потом перетащите PDF сюда.")
    if ext != ".pdf":
        raise SlideError("Нужен PDF-файл презентации.")
    import pymupdf
    h = hashlib.sha256(p.read_bytes()).hexdigest()[:10]
    d = qdir(project) / "slides" / h
    d.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(p))
    pages = []
    for i, page in enumerate(doc, start=1):
        img, thumb = d / f"p{i:02d}.png", d / f"t{i:02d}.jpg"
        if not img.exists():
            zoom = W / page.rect.width
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(str(img))
            im = Image.open(img).convert("RGB")
            im.thumbnail((320, 320))
            im.save(thumb, quality=85)
        text = " ".join(page.get_text().split())[:1200]
        pages.append({"n": i, "image": str(img), "thumb": str(thumb), "text": text})
    if not pages:
        raise SlideError("В PDF нет страниц.")
    # slides that are pictures (no text layer): the text the team wrote down in the knowledge base
    note = None
    if not any(x["text"] for x in pages):
        note, texts = kb_slide_texts(p)
        for x in pages:
            if texts.get(x["n"]):
                x["text"], x["text_src"] = texts[x["n"]][:1200], "kb"
    return {"file": str(p), "name": p.name, "dir": str(d), "pages": pages, "text_note": note}


def _vault_files(pattern: str) -> list[Path]:
    from ..brand import vault_path
    v = vault_path()
    if not v:
        return []
    return [f for f in v.rglob(pattern) if not ({".obsidian", ".trash", "99 Архив"} & set(f.parts))]


def kb_presentations() -> list[dict]:
    """PDF presentations in the knowledge base (the Obsidian vault), newest first."""
    # a «Презентации» folder first, then the newest
    fs = sorted(_vault_files("*.pdf"), key=lambda f: (not any("резентац" in x for x in f.parts), -f.stat().st_mtime))
    return [{"path": str(f), "name": f.name, "folder": f.parent.name} for f in fs[:40]]


_ROW = __import__("re").compile(r"^\|\s*(\d{1,3})\s*\|(.+)\|\s*$")


def kb_slide_texts(pdf: Path) -> tuple[str | None, dict[int, str]]:
    """A vault note that mentions this PDF and lists its slides as a table «| № | title | content |»
    → {slide number: "title. content"}. The note with the most numbered rows wins."""
    import re
    best: tuple[str | None, dict[int, str]] = (None, {})
    keys = {pdf.name.lower(), pdf.stem.lower()}
    for f in _vault_files("*.md"):
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not any(k in t.lower() for k in keys):
            continue
        rows: dict[int, str] = {}
        for line in t.splitlines():
            m = _ROW.match(line.strip())
            if not m:
                continue
            cells = [re.sub(r"\*\*|\[\[|\]\]|`", "", c).strip() for c in m.group(2).split("|")]
            cells = [c for c in cells if c]
            n = int(m.group(1))
            if cells and n not in rows:
                rows[n] = ". ".join(cells)
        if len(rows) > len(best[1]):
            best = (f.stem, rows)
    return best


def page_of(doc: dict, n: int) -> dict | None:
    return next((x for x in ((doc.get("presentation") or {}).get("pages") or []) if x["n"] == n), None)


def compose(doc: dict, n: int, layout: str, brand: dict) -> Path | None:
    """The slide placed on a transparent 1920×1080 canvas (cached next to the page image)."""
    pg = page_of(doc, n)
    if not pg or not Path(pg["image"]).exists():
        return None
    src = Path(pg["image"])
    cream, khaki = brand.get("cream", "#FFF9EB"), brand.get("khaki", "#454335")
    tag = hashlib.sha1(f"{layout}{cream}{khaki}{src.stat().st_mtime_ns}".encode()).hexdigest()[:8]
    out = src.with_name(f"c{n:02d}_{layout}_{tag}.png")
    if out.exists():
        return out
    slide = Image.open(src).convert("RGBA")
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if layout == "full":
        canvas.paste(Image.new("RGBA", (W, H), cream), (0, 0))
        slide.thumbnail((W - 160, H - 90))
        canvas.alpha_composite(slide, ((W - slide.width) // 2, (H - slide.height) // 2))
    else:
        slide.thumbnail((880, 700))
        frame = 12
        x = W - slide.width - 70 - frame
        y = (H - slide.height) // 2
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((x - frame + 10, y - frame + 14, x + slide.width + frame + 10, y + slide.height + frame + 14),
                                                 radius=18, fill=_rgba(khaki, 110))
        canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
        ImageDraw.Draw(canvas).rounded_rectangle((x - frame, y - frame, x + slide.width + frame, y + slide.height + frame),
                                                 radius=18, fill=_rgba(cream, 255))
        canvas.alpha_composite(slide, (x, y))
    canvas.save(out)
    return out


def _rgba(h: str, a: int) -> tuple:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)
