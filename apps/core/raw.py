import hashlib
import json
from contextlib import contextmanager

from django.core.files.base import ContentFile
from django.utils import timezone

from .models import IngestionRun, RawDocument


def sha256_json(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def store_json(source: str, source_id: str, payload, url: str = "") -> tuple[RawDocument, bool]:
    """Zapisuje payload; zwraca (dokument, czy_zmieniony)."""
    digest = sha256_json(payload)
    doc = RawDocument.objects.filter(source=source, source_id=source_id).first()
    if doc and doc.sha256 == digest:
        return doc, False
    doc = doc or RawDocument(source=source, source_id=source_id)
    doc.url = url
    doc.sha256 = digest
    doc.content_type = "application/json"
    doc.payload = payload
    doc.save()
    return doc, True


def store_file(
    source: str, source_id: str, content: bytes, filename: str, url: str = "",
    content_type: str = "application/pdf",
) -> tuple[RawDocument, bool]:
    digest = sha256_bytes(content)
    doc = RawDocument.objects.filter(source=source, source_id=source_id).first()
    if doc and doc.sha256 == digest:
        return doc, False
    doc = doc or RawDocument(source=source, source_id=source_id)
    doc.url = url
    doc.sha256 = digest
    doc.content_type = content_type
    doc.file.save(filename, ContentFile(content), save=False)
    doc.save()
    return doc, True


@contextmanager
def ingestion_run(kind: str):
    run = IngestionRun.objects.create(kind=kind)
    try:
        yield run
    except BaseException as exc:
        run.status = IngestionRun.Status.FAILED
        run.error = repr(exc)
        run.finished_at = timezone.now()
        run.save()
        raise
    run.status = IngestionRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save()
