from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.assets.extractors import get_extractor
from apps.assets.models import AssetDeclaration
from apps.assets.services.extraction import run_extraction
from apps.assets.sources import (
    DeclarationRef,
    DeclarationSourceError,
    SejmSiteClient,
    find_imported,
    import_declaration,
    new_session,
    read_manifest,
)
from apps.core.raw import ingestion_run
from apps.parliament.models import MP


class Command(BaseCommand):
    help = (
        "Imports asset-declaration PDFs: automatically from the Sejm website (--fetch) or "
        "from a URL, a file or a CSV. Optionally analyses them straight away."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fetch", action="store_true",
            help="Find and download MPs' declarations from sejm.gov.pl (limit with --mp, --year, --limit)",
        )
        parser.add_argument(
            "--limit", type=int,
            help="With --fetch: the first this many MPs, lowest id first",
        )
        parser.add_argument("--force", action="store_true", help="With --fetch: download files that are already stored")
        parser.add_argument("--manifest", help="CSV: mp_id,year,kind,url,file")
        parser.add_argument("--mp", type=int, help="MP id from the Sejm API")
        parser.add_argument("--year", type=int, help="Only this declaration year")
        parser.add_argument(
            "--latest", action="store_true",
            help="With --fetch: only the newest year on each MP's page",
        )
        parser.add_argument("--kind", default="ANNUAL", choices=[k for k, _ in AssetDeclaration.Kind.choices])
        parser.add_argument("--url")
        parser.add_argument("--file")
        parser.add_argument("--extract", action="store_true", help="Run extraction after importing")
        parser.add_argument("--extractor", help="mock | ocr_llm (defaults to ASSET_EXTRACTOR)")

    def handle(self, *args, **opts):
        extractor = get_extractor(opts["extractor"]) if opts["extract"] else None
        if opts["fetch"]:
            if opts["manifest"] or opts["url"] or opts["file"]:
                raise CommandError("--fetch cannot be combined with --manifest, --url or --file")
            return self.handle_fetch(opts, extractor)

        if opts["manifest"]:
            refs = read_manifest(opts["manifest"])
        elif opts["mp"] and opts["year"] and (opts["url"] or opts["file"]):
            refs = [DeclarationRef(opts["mp"], opts["year"], opts["kind"], opts["url"] or "", opts["file"] or "")]
        else:
            raise CommandError("Provide --fetch, --manifest, or --mp, --year and --url or --file")

        with ingestion_run("sync_declarations") as run:
            run.stats = {"imported": 0, "unchanged": 0, "errors": 0}
            for ref in refs:
                self.import_ref(ref, run, extractor)
        self.stdout.write(self.style.SUCCESS(f"Done: {run.stats}"))

    def handle_fetch(self, opts, extractor):
        if opts["latest"] and opts["year"]:
            raise CommandError("--latest cannot be combined with --year")
        mps = MP.objects.filter(term_id=settings.SEJM_TERM).order_by("sejm_id")
        if opts["mp"]:
            mps = mps.filter(sejm_id=opts["mp"])
        ids = list(mps.values_list("sejm_id", flat=True))
        if opts["limit"]:
            ids = ids[: opts["limit"]]
        if not ids:
            raise CommandError("No MPs in the database. Run sync_sejm first.")

        session = new_session()
        client = SejmSiteClient(session=session)
        with ingestion_run("sync_declarations") as run:
            run.stats = {"cards": 0, "imported": 0, "unchanged": 0, "skipped_rows": 0, "errors": 0}
            for sejm_id in ids:
                try:
                    card = client.declarations(sejm_id)
                except DeclarationSourceError as exc:
                    if "anti-bot" in str(exc):
                        raise CommandError(str(exc)) from exc
                    run.stats["errors"] += 1
                    self.stderr.write(self.style.WARNING(f"MP {sejm_id}: {exc}"))
                    continue
                refs = self.refs_for(card.refs, opts)
                run.stats["cards"] += 1
                run.stats["skipped_rows"] += len(card.skipped)
                for reason in card.skipped:
                    self.stderr.write(self.style.WARNING(f"MP {sejm_id}, skipped row: {reason}"))
                for ref in refs:
                    existing = None if opts["force"] else find_imported(ref)
                    if existing is None:
                        client.pause()
                    self.import_ref(ref, run, extractor, session=session, existing=existing)
        self.stdout.write(self.style.SUCCESS(f"Done: {run.stats}"))

    @staticmethod
    def refs_for(refs, opts):
        if opts["latest"]:
            if not refs:
                return []
            newest = max(ref.year for ref in refs)
            return [ref for ref in refs if ref.year == newest]
        if opts["year"]:
            return [ref for ref in refs if ref.year == opts["year"]]
        return list(refs)

    def import_ref(self, ref, run, extractor, session=None, existing=None):
        if existing is not None:
            declaration, changed = existing, False
        else:
            try:
                declaration, changed = import_declaration(ref, session=session)
            except DeclarationSourceError as exc:
                run.stats["errors"] += 1
                self.stderr.write(self.style.WARNING(str(exc)))
                return
        run.stats["imported" if changed else "unchanged"] += 1
        self.stdout.write(
            f"{'Imported' if changed else 'Unchanged'}: {declaration.mp.sejm_id:03d} {declaration}"
        )
        if extractor and (changed or declaration.status == AssetDeclaration.Status.PENDING):
            result = run_extraction(declaration, extractor)
            declaration.refresh_from_db()
            self.stdout.write(f"  -> {declaration.get_status_display()} {result.error}")
