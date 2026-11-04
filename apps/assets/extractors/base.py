"""Shared contract for asset-declaration extractors.

Every extractor (mock, ocr_llm, a custom one) takes PDF bytes and a context and returns
an `ExtractedDeclaration`. The rest of the app depends only on that shape.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from django.conf import settings
from pydantic import BaseModel, Field

from ..models import AssetCategory


class ExtractorError(Exception):
    pass


class ExtractedItem(BaseModel):
    category: AssetCategory
    label: str = Field(description="Short name of the item, e.g. 'flat', 'mortgage'")
    description: str = Field(default="", description="Details: location, year, bank, legal title")
    value: Decimal | None = Field(default=None, description="Value in `currency`")
    currency: str = Field(default="PLN", min_length=3, max_length=3)
    area_m2: Decimal | None = None
    ownership: str = ""


class ExtractedDeclaration(BaseModel):
    items: list[ExtractedItem]
    confidence: float = Field(ge=0, le=1)
    notes: str = ""
    model: str = ""
    prompt_version: str = ""
    issues: list[str] = Field(default_factory=list, description="Czytelne komunikaty z weryfikacji")
    needs_review: bool = Field(default=False, description="The extractor found errors that need a human")
    structured: dict | None = Field(default=None, description="Full answer matching schema.DeclarationData")
    verification: dict | None = None
    ocr_text: str = ""
    ocr_engine: str = ""
    ocr_confidence: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: Decimal | None = None
    llm_calls: int = 0


@dataclass(frozen=True)
class DeclarationContext:
    mp_sejm_id: int
    mp_name: str
    year: int
    kind: str
    district_name: str = ""
    cached_ocr_text: str = ""
    cached_ocr_engine: str = ""
    cached_ocr_confidence: float | None = None


class DeclarationExtractor(Protocol):
    name: str

    def extract(self, pdf_bytes: bytes | None, context: DeclarationContext) -> ExtractedDeclaration: ...


def get_extractor(name: str | None = None) -> DeclarationExtractor:
    name = name or settings.ASSET_EXTRACTOR
    if name == "mock":
        from .mock import MockExtractor

        return MockExtractor()
    if name == "ocr_llm":
        from .ocr_llm import OcrLlmExtractor

        return OcrLlmExtractor()
    raise ExtractorError(f"Unknown extractor: {name!r} (available: mock, ocr_llm)")
