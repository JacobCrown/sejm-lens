from django.conf import settings
from django.core.management.base import BaseCommand

from apps.parliament.models import Term, Voting
from apps.parliament.services.stats import compute_club_stats, recompute_mp_stats


class Command(BaseCommand):
    help = "Recomputes club results, attendance and loyalty without calling the API."

    def add_arguments(self, parser):
        parser.add_argument("--term", type=int, default=None)

    def handle(self, *args, **opts):
        term = Term.objects.get(number=opts["term"] or settings.SEJM_TERM)
        votings = Voting.objects.filter(term=term, votes_loaded=True).select_related("sitting")
        for i, voting in enumerate(votings.iterator(chunk_size=200), 1):
            compute_club_stats(voting)
            if i % 200 == 0:
                self.stdout.write(f"  {i} votings...")
        recompute_mp_stats(term)
        self.stdout.write(self.style.SUCCESS("Agregaty przeliczone."))
