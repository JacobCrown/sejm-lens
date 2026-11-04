"""Extractor: local OCR (PaddleOCR) -> any LLM -> verification -> declaration items.

Flow:
1. PDF -> text (the text layer, or OCR; the OCR result is cached in the database).
2. Text -> LLM, which returns JSON matching `schema.DeclarationData`.
3. Schema validation; on errors the model gets the problem list and fixes the answer.
4. Check against the OCR text (`verification.verify`); fixable errors go back to the model.
5. The best answer is mapped to flat items and scored with an overall confidence.
"""

import json
import logging
from dataclasses import dataclass

from django.conf import settings
from pydantic import ValidationError

from ..llm import LLMClient, LLMError, extract_json
from ..mapping import to_items
from ..ocr import OCRError, ocr_pdf
from ..prompts import PROMPT_VERSION, schema_feedback, system_prompt, user_prompt, verification_feedback
from ..schema import DeclarationData, json_schema
from ..verification import VerificationReport, verify
from .base import DeclarationContext, ExtractedDeclaration, ExtractorError

logger = logging.getLogger(__name__)


@dataclass
class Attempt:
    data: DeclarationData
    report: VerificationReport

    @property
    def score(self) -> tuple[int, float]:
        return (-len(self.report.errors), self.report.confidence)


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors()[:25]:
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"- {loc}: {err['msg']}")
    return "\n".join(lines)


class OcrLlmExtractor:
    name = "ocr_llm"

    def __init__(self, llm: LLMClient | None = None, ocr_engine=None, max_repairs: int | None = None):
        self._llm = llm
        self._ocr_engine = ocr_engine
        self.max_repairs = settings.LLM_MAX_REPAIRS if max_repairs is None else max_repairs

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient.from_settings()
        return self._llm

    def read_text(self, pdf_bytes: bytes | None, context: DeclarationContext | None = None):
        """Returns (text, engine, OCR confidence). Uses the cached text from the context when present."""
        if context and context.cached_ocr_text:
            return context.cached_ocr_text, context.cached_ocr_engine, context.cached_ocr_confidence
        if not pdf_bytes:
            raise ExtractorError("The ocr_llm extractor requires the declaration PDF.")
        try:
            result = ocr_pdf(pdf_bytes, self._ocr_engine)
        except OCRError as exc:
            raise ExtractorError(str(exc)) from exc
        return result.text, result.engine, result.mean_confidence

    def extract(self, pdf_bytes: bytes | None, context: DeclarationContext) -> ExtractedDeclaration:
        ocr_text, engine, ocr_conf = self.read_text(pdf_bytes, context)
        return self.analyze(ocr_text, engine, ocr_conf, context.mp_name, context.year)

    def analyze(
        self, ocr_text: str, engine: str, ocr_conf: float | None, mp_name: str | None, year: int | None
    ) -> ExtractedDeclaration:
        messages = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": user_prompt(ocr_text, mp_name, year)},
        ]
        schema = json_schema() if settings.LLM_RESPONSE_FORMAT == "json_schema" else None
        best: Attempt | None = None
        model = self.llm.model
        schema_failure = ""
        prompt_tokens = completion_tokens = None
        cost_usd = None
        llm_calls = 0

        for attempt in range(self.max_repairs + 1):
            try:
                response = self.llm.chat(messages, schema)
            except LLMError as exc:
                raise ExtractorError(str(exc)) from exc
            model = response.model
            llm_calls += 1
            if response.prompt_tokens is not None:
                prompt_tokens = (prompt_tokens or 0) + response.prompt_tokens
            if response.completion_tokens is not None:
                completion_tokens = (completion_tokens or 0) + response.completion_tokens
            if response.cost_usd is not None:
                cost_usd = response.cost_usd if cost_usd is None else cost_usd + response.cost_usd
            try:
                data = DeclarationData.model_validate(extract_json(response.text))
            except (ValueError, ValidationError) as exc:
                detail = _format_validation_error(exc) if isinstance(exc, ValidationError) else f"- {exc}"
                schema_failure = detail
                logger.warning("LLM, attempt %s: answer does not match the schema:\n%s", attempt + 1, detail)
                messages += [
                    {"role": "assistant", "content": response.text},
                    {"role": "user", "content": schema_feedback(detail)},
                ]
                continue

            report = verify(
                data, ocr_text, ocr_confidence=ocr_conf, expected_name=mp_name, expected_year=year
            )
            current = Attempt(data, report)
            if best is None or current.score > best.score:
                best = current
            if not report.fixable_errors:
                break
            logger.info("LLM, attempt %s: %s verification errors, asking for a fix", attempt + 1, len(report.errors))
            messages += [
                {"role": "assistant", "content": json.dumps(data.model_dump(mode="json"), ensure_ascii=False)},
                {
                    "role": "user",
                    "content": verification_feedback([f"{i.message} [{i.path}]" for i in report.fixable_errors]),
                },
            ]

        if best is None:
            raise ExtractorError(
                f"The model did not return a schema-valid answer after {self.max_repairs + 1} attempts:\n{schema_failure}"
            )

        report = best.report
        notes = [n for n in [best.data.notes] if n] + report.messages()
        return ExtractedDeclaration(
            items=to_items(best.data),
            confidence=report.confidence,
            notes="\n".join(notes),
            model=model,
            prompt_version=PROMPT_VERSION,
            issues=report.messages(),
            needs_review=bool(report.errors),
            structured=best.data.model_dump(mode="json"),
            verification=report.to_dict(),
            ocr_text=ocr_text,
            ocr_engine=engine,
            ocr_confidence=ocr_conf,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            llm_calls=llm_calls,
        )
