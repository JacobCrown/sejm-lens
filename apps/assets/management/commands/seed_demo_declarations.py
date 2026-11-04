from django.conf import settings
from django.core.management.base import BaseCommand

from apps.assets.extractors.mock import MockExtractor
from apps.assets.models import AssetDeclaration
from apps.assets.services.extraction import run_extraction
from apps.parliament.models import MP


class Command(BaseCommand):
    help = (
        "Creates demo declarations (MockExtractor) for MPs who have no real data. "
        "The UI labels them as demo."
    )

    def add_arguments(self, parser):
        parser.add_argument("--years", type=int, nargs="+", default=[2023, 2024, 2025])
        parser.add_argument("--limit", type=int, help="Only the first N active MPs")

    def handle(self, *args, **opts):
        extractor = MockExtractor()
        mps = MP.objects.filter(term_id=settings.SEJM_TERM, active=True).order_by("sejm_id")
        if opts["limit"]:
            mps = mps[: opts["limit"]]
        created = 0
        for mp in mps:
            if mp.declarations.filter(is_demo=False).exists():
                continue
            for year in opts["years"]:
                kind = AssetDeclaration.Kind.START if year == 2023 else AssetDeclaration.Kind.ANNUAL
                declaration, _ = AssetDeclaration.objects.get_or_create(
                    mp=mp, year=year, kind=kind, defaults={"is_demo": True}
                )
                run_extraction(declaration, extractor)
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Created or refreshed {created} demo declarations."))
