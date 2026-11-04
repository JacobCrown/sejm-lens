import pypdfium2 as pdfium


def render_pages(pdf_bytes: bytes, scale: float, max_pages: int):
    """Returns a list of PIL images, one per page."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return [doc[i].render(scale=scale).to_pil().convert("RGB") for i in range(min(len(doc), max_pages))]
    finally:
        doc.close()


def text_layer(pdf_bytes: bytes, max_pages: int) -> list[str]:
    """Text embedded in the PDF (empty for scans)."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(min(len(doc), max_pages))]
    finally:
        doc.close()
