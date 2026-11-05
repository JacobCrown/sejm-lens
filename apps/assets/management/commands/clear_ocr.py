from django.core.management.base import BaseCommand

from apps.assets.services.datalab_store import clear_ocr, select_declarations


class Command(BaseCommand):
    help = (
        "Deletes stored OCR text. The declaration row, its PDF and the LLM result stay. "
        "Without --yes, only prints the count."
    )

    def add_arguments(self, parser):
        parser.add_argument("--mp", type=int)
        parser.add_argument("--year", type=int, nargs="+", help="e.g. --year 2025 or --year 2025 2026")
        parser.add_argument("--limit", type=int, help="How many MPs, lowest id first")
        parser.add_argument("--yes", action="store_true", help="Actually delete. Without this, nothing is written.")

    def handle(self, *args, **opts):
        declarations = select_declarations(mp=opts["mp"], years=opts["year"], limit=opts["limit"])
        pending = [d for d in declarations if d.ocr_text or d.ocr_response is not None or d.ocr_engine]
        self.stdout.write(f"{len(pending)} declarations with stored OCR.")
        if not opts["yes"]:
            self.stdout.write("Nothing deleted. Pass --yes to clear.")
            return
        self.stdout.write(self.style.SUCCESS(f"Cleared OCR on {clear_ocr(pending)} declarations."))
