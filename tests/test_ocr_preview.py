import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from preview_ocr import draw_boxes, save_preview  # noqa: E402

from apps.assets.ocr.base import OCRLine, OCRPage  # noqa: E402


def test_draw_boxes_marks_the_rectangle():
    image = Image.new("RGB", (120, 60), "white")
    line = OCRLine(text="Kowalski", score=0.95, x0=10, y0=16, x1=70, y1=36)
    marked = draw_boxes(image, [line])
    assert image.getpixel((10, 16)) == (255, 255, 255)
    assert marked.width > image.width
    assert marked.getpixel((10, 16)) != (255, 255, 255)
    right = marked.crop((image.width + 18, 0, marked.width, marked.height))
    assert any(pixel != (255, 255, 255) for pixel in right.getdata())


def test_save_preview_writes_text_and_png(tmp_path):
    image = Image.new("RGB", (120, 60), "white")
    line = OCRLine(text="Kowalski", score=0.95, x0=10, y0=16, x1=70, y1=36)
    page = OCRPage(1, [line])
    text_path, images = save_preview("oswiadczenie", tmp_path, [image], [page])
    assert "Kowalski" in text_path.read_text(encoding="utf-8")
    assert images[0].name == "oswiadczenie-p1.png"
    assert images[0].is_file()


def test_chandra_chunks_drop_html_and_keep_the_box():
    from apps.assets.ocr.chandra import lines_from_chandra_chunks

    lines = lines_from_chandra_chunks([
        {"bbox": [5, 6, 40, 18], "label": "Text", "content": "<p>593000 PLN</p>"},
        {"bbox": [0, 0, 1, 1], "label": "Image", "content": "<img>"},
        {"bbox": [1], "label": "Text", "content": "x"},
    ])
    assert len(lines) == 1
    assert lines[0].text == "593000 PLN"
    assert (lines[0].x0, lines[0].y0, lines[0].x1, lines[0].y1) == (5, 6, 40, 18)
