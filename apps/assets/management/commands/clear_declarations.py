from django.core.management.base import BaseCommand

from apps.assets.models import AssetDeclaration
from apps.assets.services.datalab_store import delete_declarations, select_declarations
from apps.core.raw import stored_file_exists


class Command(BaseCommand):
    help = (
        "Deletes real declaration rows and their PDF when nothing else uses it. "
        "Demo rows stay. Without --yes, only prints the count."
    )

    def add_arguments(self, parser):
        parser.add_argument("--mp", type=int)
        parser.add_argument("--year", type=int, nargs="+", help="e.g. --year 2025 or --year 2025 2026")
        parser.add_argument("--limit", type=int, help="How many MPs, lowest id first")
        parser.add_argument(
            "--missing", action="store_true",
            help="Only rows whose PDF is gone from disk",
        )
        parser.add_argument("--yes", action="store_true", help="Actually delete. Without this, nothing is written.")

    def handle(self, *args, **opts):
        declarations = select_declarations(
            mp=opts["mp"], years=opts["year"], limit=opts["limit"],
            qs=AssetDeclaration.objects.filter(is_demo=False),
        )
        if opts["missing"]:
            declarations = [item for item in declarations if not stored_file_exists(item.raw)]
        self.stdout.write(f"{len(declarations)} real declarations would be deleted. Demo data stays.")
        if not opts["yes"]:
            self.stdout.write("Nothing deleted. Pass --yes to clear.")
            return
        deleted = delete_declarations(declarations)
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} declarations."))
