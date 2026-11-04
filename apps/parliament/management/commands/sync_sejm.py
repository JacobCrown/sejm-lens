from django.core.management.base import BaseCommand, CommandError

from apps.core.raw import ingestion_run
from apps.parliament.sejm_api import SejmClient
from apps.parliament.services import stats as stats_service
from apps.parliament.services import sync


class Command(BaseCommand):
    help = "Fetches clubs, MPs, sittings and votings from the Sejm API and recomputes aggregates."

    def add_arguments(self, parser):
        parser.add_argument("--term", type=int, help="Term number (defaults to SEJM_TERM)")
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--sittings", type=int, default=3,
            help="How many of the latest sittings with votings to fetch (default 3)",
        )
        group.add_argument("--sitting", type=int, action="append", help="Konkretny numer posiedzenia")
        group.add_argument("--all", action="store_true", help="Wszystkie posiedzenia kadencji")
        parser.add_argument("--force", action="store_true", help="Re-fetch unchanged records too")
        parser.add_argument("--skip-votings", action="store_true", help="Clubs and MPs only")

    def handle(self, *args, **opts):
        with SejmClient(term=opts["term"]) as client, ingestion_run("sync_sejm") as run:
            term = sync.sync_term(client)
            run.stats["clubs"] = sync.sync_clubs(client, term)
            self.stdout.write(f"Clubs: {run.stats['clubs']}")
            run.stats["mps"] = sync.sync_mps(client, term)
            self.stdout.write(f"MPs: {run.stats['mps']}")
            run.stats["sittings_meta"] = sync.sync_sittings(client, term)

            if not opts["skip_votings"]:
                available = sync.sittings_with_votings(client)
                if opts["sitting"]:
                    missing = set(opts["sitting"]) - set(available)
                    if missing:
                        raise CommandError(f"No votings for sittings: {sorted(missing)}")
                    chosen = sorted(opts["sitting"])
                elif opts["all"]:
                    chosen = available
                else:
                    chosen = available[-opts["sittings"]:]
                self.stdout.write(f"Sittings to sync: {chosen}")
                run.stats.update(sync.sync_votings(client, term, chosen, force=opts["force"]))
                self.stdout.write(
                    f"Votings: {run.stats['votings_updated']} updated, "
                    f"{run.stats['votings_skipped']} unchanged"
                )

            stats_service.recompute_mp_stats(term)
            self.stdout.write(self.style.SUCCESS("Recomputed MP attendance and loyalty."))
