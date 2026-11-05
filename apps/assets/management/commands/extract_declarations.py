from django.core.management.base import BaseCommand, CommandError

from apps.assets.extractors import ExtractorError, get_extractor
from apps.assets.models import AssetDeclaration
from apps.assets.services.datalab_store import select_declarations
from apps.assets.services.extraction import run_extraction


class Command(BaseCommand):
    help = "Analyses asset declarations with the chosen extractor (pending ones by default)."

    def add_arguments(self, parser):
        parser.add_argument("--extractor", help="mock | ocr_llm (defaults to ASSET_EXTRACTOR)")
        parser.add_argument("--mp", type=int, help="Only declarations of the MP with this id")
        parser.add_argument(
            "--year", type=int, nargs="+",
            help="Only these years, e.g. --year 2025 or --year 2025 2026",
        )
        parser.add_argument("--all", action="store_true", help="Include declarations already analysed")
        parser.add_argument("--retry-failed", action="store_true", help="Also retry the ones that failed")
        parser.add_argument("--review", action="store_true", help="Only the ones that need review")
        parser.add_argument("--reocr", action="store_true", help="Ignore the stored OCR text and run OCR again")
        parser.add_argument("--limit", type=int, help="How many MPs, lowest id first")

    def handle(self, *args, **opts):
        try:
            extractor = get_extractor(opts["extractor"])
        except ExtractorError as exc:
            raise CommandError(str(exc)) from exc
        qs = AssetDeclaration.objects.select_related("mp", "raw").order_by("mp__sejm_id", "year")
        S = AssetDeclaration.Status
        if opts["review"]:
            qs = qs.filter(status=S.NEEDS_REVIEW)
        elif not opts["all"]:
            statuses = [S.PENDING] + ([S.FAILED] if opts["retry_failed"] else [])
            qs = qs.filter(status__in=statuses)
        if opts["mp"]:
            qs = qs.filter(mp__sejm_id=opts["mp"])
        declarations = select_declarations(years=opts["year"], limit=opts["limit"], qs=qs)
        counts: dict[str, int] = {}
        try:
            for declaration in declarations:
                run = run_extraction(declaration, extractor, use_ocr_cache=not opts["reocr"])
                declaration.refresh_from_db()
                counts[declaration.status] = counts.get(declaration.status, 0) + 1
                suffix = f" - {run.error.splitlines()[0]}" if run.error else ""
                self.stdout.write(f"{declaration}: {declaration.status}{suffix}")
        except KeyboardInterrupt:
            self.stderr.write("Stopped. Declarations already written stay saved.")
        self.stdout.write(self.style.SUCCESS(f"Extractor {extractor.name}: {counts or 'no declarations'}"))
