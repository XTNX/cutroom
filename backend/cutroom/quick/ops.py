"""Block operations used by AI requests (the timeline does the same ones client-side, src/quickOps.ts).

Frames are integers on the project clock. A video block and its linked sound are split together,
so the pair keeps moving as one.
"""

from __future__ import annotations

from .model import fps_of, new_id

MEDIA = ("video", "audio")


def _split_block(doc: dict, track: str, b: dict, f: int) -> dict | None:
    if not (b["start"] < f < b["start"] + b["len"]):
        return None
    k = f - b["start"]
    right = {**b, "id": new_id(b["id"][0]), "start": f, "len": b["len"] - k}
    if track in MEDIA:
        right["in"] = round(b["in"] + k / fps_of(doc), 4)
    b["len"] = k
    lst = doc["tracks"][track]
    lst.insert(lst.index(b) + 1, right)
    return right


def split(doc: dict, track: str, f: int) -> None:
    for b in list(doc["tracks"][track]):
        r = _split_block(doc, track, b, f)
        if r and track == "video":   # the linked sound splits with the picture
            for a in list(doc["tracks"]["audio"]):
                if a.get("link") == b["id"]:
                    ra = _split_block(doc, "audio", a, f)
                    if ra:
                        ra["link"] = r["id"]


def within(doc: dict, track: str, f0: int, f1: int) -> list[dict]:
    """Blocks of `track` inside [f0, f1) after splitting at both edges."""
    split(doc, track, f0)
    split(doc, track, f1)
    return [b for b in doc["tracks"][track] if b["start"] >= f0 and b["start"] + b["len"] <= f1]


def ripple_delete(doc: dict, f0: int, f1: int) -> None:
    """Cut [f0, f1) out of the film: every track loses that span and closes the gap."""
    n = f1 - f0
    if n <= 0:
        return
    for t in ("video", "audio", "slides", "overlays"):
        if t in MEDIA:
            split(doc, t, f0)
            split(doc, t, f1)
        out = []
        for b in doc["tracks"][t]:
            s, e = b["start"], b["start"] + b["len"]
            if t in MEDIA and f0 <= s and e <= f1:
                continue
            if t not in MEDIA:   # slides / cards: trim to what's left, drop if nothing is
                keep = max(0, min(e, f0) - s) + max(0, e - max(s, f1))
                if keep <= 0:
                    continue
                b["start"] = s if s < f0 else max(f0, s - n)
                b["len"] = keep
            elif s >= f1:
                b["start"] = s - n
            out.append(b)
        doc["tracks"][t] = out


def add_block(doc: dict, track: str, block: dict) -> dict:
    """Place a slide / card: it replaces whatever it covers on its own track (overwrite)."""
    f0, f1 = block["start"], block["start"] + block["len"]
    lst = []
    for b in doc["tracks"][track]:
        s, e = b["start"], b["start"] + b["len"]
        if e <= f0 or s >= f1:
            lst.append(b)
            continue
        if s < f0:
            lst.append({**b, "len": f0 - s})
        if e > f1:   # the tail keeps its id unless the block was cut in two
            lst.append({**b, "id": new_id(b["id"][0]) if s < f0 else b["id"], "start": f1, "len": e - f1})
    lst.append(block)
    lst.sort(key=lambda x: x["start"])
    doc["tracks"][track] = lst
    return block
