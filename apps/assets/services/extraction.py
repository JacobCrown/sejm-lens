"""Runs an extractor, validates the result and stores the declaration items."""

import logging
import time
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from ..extractors import DeclarationContext, DeclarationExtractor, ExtractedDeclaration, ExtractorError
from ..models import AssetCategory, AssetDeclaration, AssetItem, ExtractionRun
from .currency import declaration_date, rate_to_pln

logger = logging.getLogger(__name__)

MAX_SANE_VALUE = Decimal("1000000000")
# Informational notes: on their own they do not require a human review.
INFORMATIONAL_PREFIXES = ("Rate ", "Missing value")


def context_for(declaration: AssetDeclaration, use_ocr_cache: bool = True) -> DeclarationContext:
    mp = declaration.mp
    cached = use_ocr_cache and bool(declaration.ocr_text)
    return DeclarationContext(
        mp_sejm_id=mp.sejm_id,
        mp_name=mp.full_name,
        year=declaration.year,
        kind=declaration.kind,
        district_name=mp.district_name,
        cached_ocr_text=declaration.ocr_text if cached else "",
        cached_ocr_engine=declaration.ocr_engine if cached else "",
        cached_ocr_confidence=declaration.ocr_confidence if cached else None,
    )


def validate(result: ExtractedDeclaration, year: int) -> tuple[list[AssetItem], list[str]]:
    """Converts currencies and checks that values are plausible. Returns (items, problems)."""
    issues: list[str] = []
    items: list[AssetItem] = []
    on = declaration_date(year)
    for pos, extracted in enumerate(result.items):
        value_pln = None
        if extracted.value is not None:
            if extracted.value < 0:
                issues.append(f"Negative value: {extracted.label}")
            elif extracted.value > MAX_SANE_VALUE:
                issues.append(f"Suspiciously high value: {extracted.label}")
            rate, source = rate_to_pln(extracted.currency, on)
            if rate is None:
                issues.append(f"Nieznana waluta {extracted.currency}: {extracted.label}")
            else:
                value_pln = (extracted.value * rate).quantize(Decimal("0.01"))
                if source == "fallback":
                    issues.append(f"Rate {extracted.currency} approximated (NBP unavailable)")
        elif extracted.category != AssetCategory.OTHER:
            issues.append(f"Missing value: {extracted.label}")
        items.append(
            AssetItem(
                category=extracted.category,
                label=extracted.label[:200],
                description=extracted.description[:500],
                value=extracted.value,
                currency=extracted.currency.upper(),
                value_pln=value_pln,
                area_m2=extracted.area_m2,
                ownership=extracted.ownership[:128],
                position=pos,
            )
        )
    if not items and not result.structured:
        issues.append("Extractor returned no items")
    return items, list(dict.fromkeys(issues))


def _read_pdf(declaration: AssetDeclaration) -> bytes | None:
    if not declaration.raw or not declaration.raw.file:
        return None
    try:
        with declaration.raw.file.open("rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def run_extraction(
    declaration: AssetDeclaration, extractor: DeclarationExtractor, use_ocr_cache: bool = True
) -> ExtractionRun:
    started = time.monotonic()
    run = ExtractionRun(declaration=declaration, extractor=extractor.name)
    try:
        result = extractor.extract(_read_pdf(declaration), context_for(declaration, use_ocr_cache))
    except ExtractorError as exc:
        run.error = str(exc)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.save()
        declaration.status = AssetDeclaration.Status.FAILED
        declaration.extractor = extractor.name
        declaration.notes = str(exc)
        declaration.save(update_fields=["status", "extractor", "notes", "updated_at"])
        logger.warning("Extraction failed for %s: %s", declaration, exc)
        return run

    items, issues = validate(result, declaration.year)
    needs_review = (
        result.needs_review
        or result.confidence < settings.ASSET_REVIEW_THRESHOLD
        or any(not i.startswith(INFORMATIONAL_PREFIXES) for i in issues)
    )
    run.success = True
    run.model = result.model
    run.prompt_version = result.prompt_version
    run.confidence = result.confidence
    run.prompt_tokens = result.prompt_tokens
    run.completion_tokens = result.completion_tokens
    run.cost_usd = result.cost_usd
    run.llm_calls = result.llm_calls
    run.raw_output = result.model_dump(mode="json", exclude={"ocr_text"})
    run.duration_ms = int((time.monotonic() - started) * 1000)

    with transaction.atomic():
        run.save()
        declaration.items.all().delete()
        for item in items:
            item.declaration = declaration
        AssetItem.objects.bulk_create(items)
        declaration.status = (
            AssetDeclaration.Status.NEEDS_REVIEW if needs_review else AssetDeclaration.Status.EXTRACTED
        )
        declaration.extractor = extractor.name
        declaration.confidence = result.confidence
        declaration.is_demo = extractor.name == "mock"
        declaration.reviewed = False
        declaration.notes = "\n".join(dict.fromkeys(filter(None, [*result.notes.splitlines(), *issues])))
        declaration.structured = result.structured
        declaration.verification = result.verification
        if result.ocr_text:
            declaration.ocr_text = result.ocr_text
            declaration.ocr_engine = result.ocr_engine
            declaration.ocr_confidence = result.ocr_confidence
        declaration.save()
    return run
