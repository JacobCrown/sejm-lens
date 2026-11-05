#!/usr/bin/env python
"""Draw what Chandra reads on a PDF.

Left side of each page is the scan with rectangles. Right side is the text
Chandra read in those rectangles. Also writes a text file and one PDF.

    uv run python scripts/preview_ocr.py oswiadczenie.pdf --out /tmp/ocr-preview
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from apps.assets.ocr.base import OCRPage, OCRResult  # noqa: E402
from ocr_draw import draw_boxes, save_pdf  # noqa: E402


def save_preview(stem: str, out_dir: Path, images: list, pages: list[OCRPage]) -> tuple[Path, list[Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    text_path = out_dir / f"{stem}.txt"
    text_path.write_text(OCRResult("chandra", pages).text, encoding="utf-8")
    images_out = []
    sheets = []
    for image, page in zip(images, pages, strict=True):
        path = out_dir / f"{stem}-p{page.number}.png"
        sheet = draw_boxes(image, page.lines)
        sheet.save(path)
        images_out.append(path)
        sheets.append(sheet)
    if sheets:
        save_pdf(out_dir / f"{stem}-podglad.pdf", sheets)
    return text_path, images_out


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview OCR detections on a PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="Directory for the txt, PNG files and the preview PDF")
    args = parser.parse_args()
    if not args.pdf.is_file():
        sys.exit(f"No such file: {args.pdf}")

    import django

    django.setup()
    from django.conf import settings

    from apps.assets.ocr import get_ocr_engine
    from apps.assets.pdfutil import render_pages

    pdf_bytes = args.pdf.read_bytes()
    images = render_pages(pdf_bytes, settings.OCR_RENDER_SCALE, settings.OCR_MAX_PAGES)
    if not images:
        sys.exit("The PDF has no pages")
    engine = get_ocr_engine("chandra")
    print(f"engine: {engine.name}", file=sys.stderr)
    min_score = settings.OCR_MIN_LINE_CONFIDENCE
    pages = []
    for number, image in enumerate(images, 1):
        lines = [line for line in engine.recognize(image) if line.text.strip() and line.score >= min_score]
        pages.append(OCRPage(number, lines))
        print(f"page {number}: {len(lines)} detections", file=sys.stderr)
    text_path, image_paths = save_preview(args.pdf.stem, args.out, images, pages)
    print(text_path)
    print(args.out / f"{args.pdf.stem}-podglad.pdf")
    for path in image_paths:
        print(path)


if __name__ == "__main__":
    main()
