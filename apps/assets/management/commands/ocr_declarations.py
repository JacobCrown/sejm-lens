from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.assets.ocr.datalab import DatalabClient, DatalabError
from apps.assets.services.datalab_store import cost_label, ocr_declaration, select_declarations
from apps.core.raw import stored_file_exists


class Command(BaseCommand):
    help = (
        "Sends declaration PDFs to the Datalab convert API and stores the markdown. "
        "A declaration that already has OCR text is skipped, so the call is not paid twice."
    )

    def add_arguments(self, parser):
        parser.add_argument("--mp", type=int, help="Only this Sejm MP id")
        parser.add_argument(
            "--year", type=int, nargs="+",
            help="Only these years, e.g. --year 2025 or --year 2025 2026",
        )
        parser.add_argument("--limit", type=int, help="How many MPs, lowest id first")
        parser.add_argument("--force", action="store_true", help="Call Datalab again even if OCR text is stored")

    def handle(self, *args, **opts):
        if not settings.DATALAB_API_KEY:
            raise CommandError("DATALAB_API_KEY is empty.")
        declarations = select_declarations(mp=opts["mp"], years=opts["year"], limit=opts["limit"])
        missing = [item for item in declarations if not stored_file_exists(item.raw)]
        declarations = [item for item in declarations if stored_file_exists(item.raw)]
        if missing:
            self.stdout.write(f"Skipped {len(missing)} declarations whose PDF is missing from disk.")
        if not declarations:
            self.stdout.write("No declarations with a PDF.")
            return
        try:
            client = DatalabClient(
                settings.DATALAB_API_KEY,
                base_url=settings.DATALAB_API_URL,
                mode=settings.DATALAB_MODE,
            )
        except DatalabError as exc:
            raise CommandError(str(exc)) from exc
        stored = skipped = failed = 0
        try:
            for declaration in declarations:
                try:
                    outcome = ocr_declaration(declaration, client, force=opts["force"])
                except DatalabError as exc:
                    failed += 1
                    self.stdout.write(self.style.ERROR(f"{declaration}: {exc}"))
                    continue
                if outcome == "skipped":
                    skipped += 1
                    self.stdout.write(f"{declaration}: already stored")
                    continue
                stored += 1
                declaration.refresh_from_db()
                self.stdout.write(self.style.SUCCESS(
                    f"{declaration}: stored {declaration.ocr_pages or '?'} pages, {cost_label(declaration.ocr_cost_usd)}"
                ))
        finally:
            client.close()
        self.stdout.write(f"Stored {stored}, skipped {skipped}, failed {failed}.")
