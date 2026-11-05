import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.parliament.models import MP
from apps.parliament.portraits import fetch_portraits


class Command(BaseCommand):
    help = (
        "Downloads MP portraits into static/portraits/. "
        "Commit that directory so production serves the photos itself "
        "instead of hotlinking api.sejm.gov.pl."
    )

    def add_arguments(self, parser):
        parser.add_argument("--term", type=int, help="Term number (defaults to SEJM_TERM)")
        parser.add_argument("--force", action="store_true", help="Download again even if the file exists")
        parser.add_argument("--workers", type=int, default=4)

    def handle(self, *args, **opts):
        term_id = opts["term"] or settings.SEJM_TERM
        sejm_ids = list(
            MP.objects.filter(term_id=term_id).order_by("sejm_id").values_list("sejm_id", flat=True)
        )
        if not sejm_ids:
            self.stdout.write(self.style.WARNING(f"No MPs for term {term_id}. Run sync_sejm first."))
            return
        self.stdout.write(f"Portraits for {len(sejm_ids)} MPs (term {term_id})...")
        timeout = httpx.Timeout(30.0)
        limits = httpx.Limits(max_connections=opts["workers"], max_keepalive_connections=opts["workers"])
        headers = {"User-Agent": "SejmWatch/0.1 (+https://github.com/JacobCrown/sejm-lens)", "Accept": "image/jpeg"}
        with httpx.Client(timeout=timeout, limits=limits, headers=headers, follow_redirects=True) as client:
            stats = fetch_portraits(
                term_id,
                sejm_ids,
                client=client,
                force=opts["force"],
                workers=opts["workers"],
                on_progress=lambda done, total, saved, failed: self.stdout.write(
                    f"  {done}/{total}  saved {saved}  failed {failed}"
                ),
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Saved {stats['saved']}, skipped {stats['skipped']}, failed {stats['failed']}."
            )
        )
