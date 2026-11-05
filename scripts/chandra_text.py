"""Turn Chandra layout chunks into text plus pixel boxes. No Django, no model."""

import html
import re

_TAG = re.compile(r"<[^>]+>")


def text_from_html(content: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", content or "")).split())


def chunk_boxes(chunks) -> list[tuple[str, float, float, float, float]]:
    boxes = []
    for chunk in chunks:
        bbox = chunk.get("bbox") if isinstance(chunk, dict) else getattr(chunk, "bbox", None)
        content = chunk.get("content") if isinstance(chunk, dict) else getattr(chunk, "content", "")
        if not bbox or len(bbox) < 4:
            continue
        text = text_from_html(str(content or ""))
        if not text:
            continue
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
        boxes.append((text, x0, y0, x1, y1))
    return boxes
