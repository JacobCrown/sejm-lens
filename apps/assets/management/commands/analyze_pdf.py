import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.assets.extractors import ExtractorError
from apps.assets.extractors.ocr_llm import OcrLlmExtractor
from apps.assets.ocr import OCRError, ocr_pdf
from apps.core.formatting import money_short


class Command(BaseCommand):
    help = (
        "Processes any declaration PDF without writing to the database: OCR -> LLM -> verification. "
        "Use it to check the .env configuration and the quality of a reading."
    )

    def add_arguments(self, parser):
        parser.add_argument("pdf", nargs="?", help="Path to a PDF")
        parser.add_argument("--ocr-text", help="File with OCR text already prepared (skips the slow OCR)")
        parser.add_argument("--save-ocr", help="Save the OCR text to this file")
        parser.add_argument("--ocr-only", action="store_true", help="OCR only, no LLM")
        parser.add_argument("--force-ocr", action="store_true", help="Run OCR even when the PDF has a text layer")
        parser.add_argument("--name", help="Expected declarant, e.g. 'Jan Kowalski' (turns on the identity check)")
        parser.add_argument("--year", type=int, help="Expected declaration year")
        parser.add_argument("--json", action="store_true", help="Print the full JSON (data and the verification report)")

    def handle(self, *args, **opts):
        confidence = None
        engine = "text file"
        if opts["ocr_text"]:
            text = Path(opts["ocr_text"]).read_text(encoding="utf-8")
        elif opts["pdf"]:
            try:
                self.stderr.write("OCR running (about 20 s per page on CPU)...")
                result = ocr_pdf(Path(opts["pdf"]).read_bytes(), force_ocr=opts["force_ocr"])
            except (OCRError, OSError) as exc:
                raise CommandError(str(exc)) from exc
            text, engine, confidence = result.text, result.engine, result.mean_confidence
            if result.low_confidence_pages():
                self.stderr.write(f"Pages with low OCR confidence: {result.low_confidence_pages()}")
        else:
            raise CommandError("Provide a PDF path or --ocr-text.")

        if opts["save_ocr"]:
            Path(opts["save_ocr"]).write_text(text, encoding="utf-8")
            self.stderr.write(f"Saved OCR text: {opts['save_ocr']}")
        if opts["ocr_only"]:
            self.stdout.write(text)
            return

        try:
            result = OcrLlmExtractor().analyze(text, engine, confidence, opts["name"], opts["year"])
        except ExtractorError as exc:
            raise CommandError(str(exc)) from exc

        if opts["json"]:
            self.stdout.write(json.dumps(
                {"structured": result.structured, "verification": result.verification}, ensure_ascii=False, indent=2
            ))
            return
        self.stdout.write(self.style.MIGRATE_HEADING(f"Model: {result.model} · OCR: {engine}"))
        for item in result.items:
            value = money_short(item.value, item.currency) if item.value is not None else "-"
            self.stdout.write(f"  {item.category.value:<13} {item.label[:48]:<48} {value:>16}  {item.description[:50]}")
        report = result.verification
        self.stdout.write(
            f"\nOverall confidence: {result.confidence:.0%} · agreement with OCR: {report['grounded_ratio']:.0%} "
            f"({report['passed']}/{report['checks']} checks)"
        )
        style = self.style.ERROR if result.needs_review else self.style.SUCCESS
        self.stdout.write(style("NEEDS REVIEW" if result.needs_review else "No verification errors"))
        for message in result.issues:
            self.stdout.write(f"  - {message}")
