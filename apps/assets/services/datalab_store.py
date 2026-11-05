"""Save a Datalab OCR result on the declaration it belongs to, and skip ones already stored."""

from decimal import Decimal

from django.db import transaction

from apps.assets.models import AssetDeclaration
from apps.assets.ocr.datalab import DatalabError, usd_from_breakdown
from apps.assets.services.extraction import _read_pdf

STORED_KEYS = (
    "markdown", "metadata", "page_count", "parse_quality_score", "cost_breakdown",
    "checkpoint_id", "request_id", "versions", "output_format", "status", "success",
)


def apply_datalab_result(declaration: AssetDeclaration, payload: dict) -> None:
    quality = payload.get("parse_quality_score")
    stored = {key: payload.get(key) for key in STORED_KEYS if key in payload}
    declaration.ocr_text = payload["markdown"]
    declaration.ocr_engine = "datalab"
    declaration.ocr_confidence = (float(quality) / 5.0) if quality is not None else None
    declaration.ocr_pages = payload.get("page_count")
    declaration.ocr_cost_usd = usd_from_breakdown(payload.get("cost_breakdown"))
    declaration.ocr_response = stored
    declaration.save(update_fields=[
        "ocr_text", "ocr_engine", "ocr_confidence", "ocr_pages", "ocr_cost_usd",
        "ocr_response", "updated_at",
    ])


def ocr_declaration(declaration: AssetDeclaration, client, *, force: bool = False) -> str:
    """Returns 'skipped' or 'stored'. Raises DatalabError when the call fails."""
    if declaration.ocr_text.strip() and not force:
        return "skipped"
    pdf = _read_pdf(declaration)
    if not pdf:
        raise DatalabError(f"{declaration} has no PDF")
    filename = f"osw_{declaration.mp.sejm_id}_{declaration.year}_{declaration.kind.lower()}.pdf"
    payload = client.convert_pdf(pdf, filename)
    with transaction.atomic():
        apply_datalab_result(declaration, payload)
    return "stored"


def select_declarations(*, mp: int | None = None, years: list[int] | None = None, limit: int | None = None, qs=None):
    """`--limit` is how many MPs (lowest id first), not how many declarations."""
    if qs is None:
        qs = AssetDeclaration.objects.filter(is_demo=False, raw__isnull=False)
    qs = qs.select_related("mp", "raw").order_by("mp__sejm_id", "year", "kind")
    if mp:
        qs = qs.filter(mp__sejm_id=mp)
    if years:
        qs = qs.filter(year__in=years)
    if not limit:
        return list(qs)
    chosen: list[int] = []
    for sejm_id in qs.values_list("mp__sejm_id", flat=True):
        if sejm_id not in chosen:
            chosen.append(sejm_id)
        if len(chosen) == limit:
            break
    return list(qs.filter(mp__sejm_id__in=chosen))


def cost_label(amount: Decimal | None) -> str:
    if amount is None:
        return "cost unknown"
    return f"${amount:.4f}"


def delete_declarations(declarations) -> int:
    """Deletes real declaration rows. A raw PDF is removed when nothing else points at it."""
    deleted = 0
    for declaration in declarations:
        if declaration.is_demo:
            continue
        raw = declaration.raw
        declaration.delete()
        if raw is not None and not AssetDeclaration.objects.filter(raw=raw).exists():
            if raw.file:
                raw.file.delete(save=False)
            raw.delete()
        deleted += 1
    return deleted


def clear_ocr(declarations) -> int:
    """Drops stored Datalab text. The PDF and the LLM result stay."""
    cleared = 0
    for declaration in declarations:
        if not declaration.ocr_text and declaration.ocr_response is None and not declaration.ocr_engine:
            continue
        declaration.ocr_text = ""
        declaration.ocr_engine = ""
        declaration.ocr_confidence = None
        declaration.ocr_pages = None
        declaration.ocr_cost_usd = None
        declaration.ocr_response = None
        declaration.save(update_fields=[
            "ocr_text", "ocr_engine", "ocr_confidence", "ocr_pages", "ocr_cost_usd",
            "ocr_response", "updated_at",
        ])
        cleared += 1
    return cleared


def clear_extractions(declarations) -> int:
    """Drops the LLM result. Demo rows and the stored OCR text stay."""
    cleared = 0
    for declaration in declarations:
        if declaration.is_demo:
            continue
        has_result = (
            declaration.structured is not None
            or declaration.verification is not None
            or declaration.status != AssetDeclaration.Status.PENDING
            or declaration.items.exists()
            or declaration.runs.exists()
        )
        if not has_result:
            continue
        declaration.items.all().delete()
        declaration.runs.all().delete()
        declaration.structured = None
        declaration.verification = None
        declaration.status = AssetDeclaration.Status.PENDING
        declaration.extractor = ""
        declaration.confidence = None
        declaration.notes = ""
        declaration.reviewed = False
        declaration.save(update_fields=[
            "structured", "verification", "status", "extractor", "confidence",
            "notes", "reviewed", "updated_at",
        ])
        cleared += 1
    return cleared
