"""OCR layer: a swappable engine, grouping boxes into lines, and a path for text PDFs."""

import statistics
from dataclasses import dataclass, field
from typing import Protocol

from django.conf import settings

from ..pdfutil import render_pages, text_layer

MIN_TEXT_LAYER_CHARS_PER_PAGE = 200


class OCRError(Exception):
    pass


@dataclass
class OCRLine:
    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass
class OCRPage:
    number: int
    lines: list[OCRLine] = field(default_factory=list)
    embedded_text: str | None = None

    def rows(self) -> list[list[OCRLine]]:
        """Groups detected fragments into visual rows (same y), top to bottom."""
        rows: list[list[OCRLine]] = []
        for line in sorted(self.lines, key=lambda l: l.cy):
            if rows:
                prev = rows[-1]
                ref = statistics.median(l.cy for l in prev)
                tol = 0.55 * statistics.median(l.height for l in prev)
                if abs(line.cy - ref) <= tol:
                    prev.append(line)
                    continue
            rows.append([line])
        return [sorted(r, key=lambda l: l.x0) for r in rows]

    @property
    def text(self) -> str:
        if self.embedded_text is not None:
            return self.embedded_text.strip()
        return "\n".join("  ".join(l.text for l in row) for row in self.rows())

    @property
    def mean_confidence(self) -> float | None:
        if self.embedded_text is not None:
            return 1.0
        scores = [l.score for l in self.lines]
        return sum(scores) / len(scores) if scores else None


@dataclass
class OCRResult:
    engine: str
    pages: list[OCRPage]

    @property
    def text(self) -> str:
        return "\n\n".join(f"=== STRONA {p.number} ===\n{p.text}" for p in self.pages)

    @property
    def mean_confidence(self) -> float | None:
        scores = [s for p in self.pages if (s := p.mean_confidence) is not None]
        return sum(scores) / len(scores) if scores else None

    def low_confidence_pages(self, threshold: float = 0.85) -> list[int]:
        return [p.number for p in self.pages if (p.mean_confidence or 0) < threshold]


class OCREngine(Protocol):
    name: str

    def recognize(self, image) -> list[OCRLine]:
        """image: a PIL image (RGB). Returns recognised text fragments with their boxes."""


def get_ocr_engine(name: str | None = None) -> OCREngine:
    name = (name or settings.OCR_ENGINE).lower()
    if name == "paddle":
        try:
            from .paddle import PaddleOCREngine
        except ImportError as exc:
            raise OCRError("PaddleOCR is not installed. Run: uv sync --extra ocr") from exc
        return PaddleOCREngine.shared()
    raise OCRError(f"Unknown OCR engine: {name!r} (available: paddle)")


def ocr_pdf(pdf_bytes: bytes, engine: OCREngine | None = None, force_ocr: bool = False) -> OCRResult:
    """Returns the PDF text. A text layer is used instead of OCR when the PDF has one."""
    max_pages = settings.OCR_MAX_PAGES
    if not force_ocr:
        try:
            layers = text_layer(pdf_bytes, max_pages)
        except Exception as exc:  # a broken PDF will fail again at render time
            raise OCRError(f"Cannot read the PDF: {exc}") from exc
        if layers and sum(len(t.strip()) for t in layers) >= MIN_TEXT_LAYER_CHARS_PER_PAGE * len(layers):
            return OCRResult("pdf-text", [OCRPage(i + 1, embedded_text=t) for i, t in enumerate(layers)])

    engine = engine or get_ocr_engine()
    try:
        images = render_pages(pdf_bytes, settings.OCR_RENDER_SCALE, max_pages)
    except Exception as exc:
        raise OCRError(f"Cannot render the PDF: {exc}") from exc
    if not images:
        raise OCRError("The PDF has no pages")
    min_score = settings.OCR_MIN_LINE_CONFIDENCE
    pages = []
    for i, image in enumerate(images, 1):
        lines = [l for l in engine.recognize(image) if l.text.strip() and l.score >= min_score]
        pages.append(OCRPage(i, lines))
    return OCRResult(engine.name, pages)
