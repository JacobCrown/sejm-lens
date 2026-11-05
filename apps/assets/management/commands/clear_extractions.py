from django.core.management.base import BaseCommand

from apps.assets.models import AssetDeclaration
from apps.assets.services.datalab_store import clear_extractions, select_declarations


class Command(BaseCommand):
    help = (
        "Deletes LLM extraction results and sets the declaration back to pending. "
        "Stored OCR text stays. Without --yes, only prints the count."
    )

    def add_arguments(self, parser):
        parser.add_argument("--mp", type=int)
        parser.add_argument("--year", type=int, nargs="+", help="e.g. --year 2025 or --year 2025 2026")
        parser.add_argument("--limit", type=int, help="How many MPs, lowest id first")
        parser.add_argument("--yes", action="store_true", help="Actually delete. Without this, nothing is written.")

    def handle(self, *args, **opts):
        declarations = select_declarations(
            mp=opts["mp"], years=opts["year"], limit=opts["limit"],
            qs=AssetDeclaration.objects.filter(is_demo=False),
        )
        pending = [
            d for d in declarations
            if d.structured is not None or d.verification is not None
            or d.status != AssetDeclaration.Status.PENDING or d.items.exists() or d.runs.exists()
        ]
        self.stdout.write(
            f"{len(pending)} real declarations with an LLM result. Demo data (is_demo) is left alone."
        )
        if not opts["yes"]:
            self.stdout.write("Nothing deleted. Pass --yes to clear.")
            return
        self.stdout.write(self.style.SUCCESS(f"Cleared LLM results on {clear_extractions(pending)} declarations."))
