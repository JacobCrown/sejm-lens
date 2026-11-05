import json
from decimal import Decimal

import httpx
import pytest
from django.urls import reverse

from apps.assets.extractors import ExtractorError, get_extractor
from apps.assets.extractors.ocr_llm import OcrLlmExtractor
from apps.assets.llm import LLMClient
from apps.assets.mapping import to_items
from apps.assets.models import AssetCategory, AssetDeclaration
from apps.assets.ocr import OCRError, OCRLine, OCRPage, ocr_pdf
from apps.assets.ocr import base as ocr_base
from apps.assets.schema import DeclarationData
from apps.assets.services.extraction import run_extraction
from apps.assets.services.summary import summarize
from apps.assets.sources import DeclarationRef, import_declaration
from apps.parliament.models import MP

from .conftest import make_pdf
from .synthetic import OCR_TEXT, bad_payload, good_payload

pytestmark = pytest.mark.django_db


class ScriptedLLM:
    """Stand-in provider: returns the next reply and remembers the conversations."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.conversations: list[list[dict]] = []

    def _handler(self, request):
        body = json.loads(request.content)
        self.conversations.append(body["messages"])
        reply = self.replies[min(len(self.conversations) - 1, len(self.replies) - 1)]
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return httpx.Response(200, json={"model": "scripted-1", "choices": [{"message": {"content": content}}]})

    def client(self) -> LLMClient:
        return LLMClient(base_url="https://llm.test/v1/chat/completions", model="scripted",
                         transport=httpx.MockTransport(self._handler))

    @property
    def calls(self):
        return len(self.conversations)


def analyze(llm: ScriptedLLM, name="Jan Testowy", max_repairs=2, ocr_conf=0.97):
    ex = OcrLlmExtractor(llm=llm.client(), max_repairs=max_repairs)
    return ex.analyze(OCR_TEXT, "chandra", ocr_conf, name, 2024)


# ---------- ekstraktor ----------

def test_clean_answer_needs_one_call():
    llm = ScriptedLLM(good_payload())
    result = analyze(llm)
    assert llm.calls == 1 and not result.needs_review and not result.issues
    assert result.model == "scripted-1" and result.confidence > 0.9
    assert result.structured["declarant"]["full_name"] == "JAN TESTOWY"
    assert result.verification["grounded_ratio"] == 1.0
    system = llm.conversations[0][0]["content"]
    assert "DO NOT SUM" in system and '"savings_pln"' in system  # the schema is part of the prompt
    assert "Expected declarant: Jan Testowy" in llm.conversations[0][1]["content"]


def test_invalid_json_triggers_repair_with_error_details():
    broken = good_payload()
    broken["real_estate"][0]["kind"] = "CASTLE"
    llm = ScriptedLLM("to nie jest json", broken, good_payload())
    result = analyze(llm)
    assert llm.calls == 3 and not result.needs_review
    assert "does not contain a JSON object" in llm.conversations[1][-1]["content"]
    assert "real_estate.0.kind" in llm.conversations[2][-1]["content"]


def test_verification_errors_are_fed_back_and_fixed():
    llm = ScriptedLLM(bad_payload(), good_payload())
    result = analyze(llm)
    assert llm.calls == 2 and not result.needs_review
    feedback = llm.conversations[1][-1]["content"]
    assert "909000" in feedback and "777 ORLEN" in feedback and "Waluta EUR" in feedback


def test_best_effort_result_is_flagged_when_model_never_fixes_it():
    llm = ScriptedLLM(bad_payload())
    result = analyze(llm, max_repairs=1)
    assert llm.calls == 2
    assert result.needs_review and result.confidence < 0.7
    assert any("909000" in m for m in result.issues)
    assert "BŁĄD" in result.notes


def test_picks_the_better_of_several_attempts():
    worse = bad_payload()
    worse["savings_pln"][0]["money"]["amount"] = 1
    llm = ScriptedLLM(bad_payload(), worse)
    result = analyze(llm, max_repairs=1)
    assert not any("amount': 1" in m for m in result.issues)
    assert len(result.verification["issues"]) <= 5


def test_empty_repair_keeps_the_earlier_answer():
    llm = ScriptedLLM(bad_payload(), "")
    result = analyze(llm, max_repairs=1)
    assert llm.calls == 2 and result.needs_review
    assert any("909000" in message for message in result.issues)


def test_schema_failure_on_every_attempt_raises():
    llm = ScriptedLLM("{\"declarant\": {}}")
    with pytest.raises(ExtractorError, match="schema"):
        analyze(llm, max_repairs=1)
    assert llm.calls == 2


def test_wrong_declarant_is_flagged_without_pointless_retries():
    llm = ScriptedLLM(good_payload())
    result = analyze(llm, name="Anna Nowak")
    assert llm.calls == 1 and result.needs_review
    assert any("Możliwy zły plik" in m for m in result.issues)


def test_provider_failure_becomes_extractor_error():
    ex = OcrLlmExtractor(llm=LLMClient(
        base_url="https://llm.test/v1/chat/completions", model="m",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")),
    ))
    with pytest.raises(ExtractorError, match="refused access"):
        ex.analyze(OCR_TEXT, "chandra", 0.97, None, None)


def test_get_extractor_registry():
    assert get_extractor("ocr_llm").name == "ocr_llm"
    with pytest.raises(ExtractorError):
        get_extractor("nieznany")


# ---------- mapping to items ----------

def test_mapping_to_items():
    items = to_items(DeclarationData.model_validate(good_payload()))
    by_cat: dict[str, list] = {}
    for item in items:
        by_cat.setdefault(item.category.value, []).append(item)

    assert [i.currency for i in by_cat["CASH_FOREIGN"]] == ["CHF", "USD", "EUR"]
    assert {i.label for i in by_cat["SECURITIES"]} == {"IKE", "IKZE"}
    house, flat, plot = by_cat["REAL_ESTATE"]
    assert house.label == "Dom" and house.ownership == "współwłasność · udział 1/2" and house.area_m2 == 115
    assert plot.area_m2 == 14946 and plot.value == Decimal("45000")
    shares = by_cat["SHARES"]
    assert shares[0].label == "Akcje (spółka z udziałem państwa): PZU" and shares[0].description == "15400 szt."
    assert shares[0].value is None
    assert {i.label for i in by_cat["INCOME"]} == {"Uposażenie poselskie", "Wynagrodzenie miesięczne"}
    car = by_cat["VEHICLE"][0]
    assert car.label == "Skoda Octavia" and "rok produkcji 2019" in car.description
    mortgage, card = by_cat["LIABILITY"]
    assert mortgage.value == Decimal("47145.19") and mortgage.currency == "CHF"
    assert card.value is None and "limit 5000 EUR" in card.description


# ---------- database integration ----------

class FakeOCR:
    name = "fake-ocr"

    def __init__(self, text=OCR_TEXT, score=0.97):
        self.lines = [l for l in text.splitlines() if not l.startswith("===")]
        self.score = score
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        return [OCRLine(t, self.score, 10, i * 30, 500, i * 30 + 20) for i, t in enumerate(self.lines)]


@pytest.fixture
def pending(synced, media_tmp, tmp_path):
    MP.objects.filter(sejm_id=1).update(first_name="Jan", last_name="Testowy")
    pdf = tmp_path / "osw.pdf"
    pdf.write_bytes(make_pdf())
    declaration, _ = import_declaration(DeclarationRef(mp_sejm_id=1, year=2024, file=str(pdf)))
    return declaration


def test_full_pipeline_saves_structured_data_and_caches_ocr(pending):
    ocr = FakeOCR()
    llm = ScriptedLLM(good_payload())
    extractor = OcrLlmExtractor(llm=llm.client(), ocr_engine=ocr)

    run = run_extraction(pending, extractor)
    pending.refresh_from_db()
    assert run.success and run.extractor == "ocr_llm" and run.prompt_version == "ocr-llm-2"
    assert pending.status == AssetDeclaration.Status.EXTRACTED and not pending.is_demo
    assert pending.extractor == "ocr_llm" and pending.confidence > 0.9
    assert pending.structured["declarant"]["full_name"] == "JAN TESTOWY"
    assert pending.verification["grounded_ratio"] == 1.0
    assert pending.ocr_engine == "fake-ocr" and "107728PLN" in pending.ocr_text
    assert "ocr_text" not in run.raw_output
    assert ocr.calls == 1 and llm.calls == 1

    # Amounts in foreign currencies are converted with the fallback rate (NBP is disabled in tests).
    s = summarize(pending)
    assert s.real_estate == Decimal("1094000")
    expected_savings = (
        Decimal("107728") + Decimal("5954") * Decimal("4.55") + Decimal("2815") * Decimal("3.95")
        + Decimal("195141") * Decimal("4.30") + Decimal("62691.41") + Decimal("56480.68")
    )
    assert s.savings == expected_savings.quantize(Decimal("0.01"))
    assert s.liabilities == (Decimal("47145.19") * Decimal("4.55")).quantize(Decimal("0.01"))  # the card limit is excluded

    # Analysing again (for example after changing the model) does not repeat the slow OCR.
    run_extraction(pending, extractor)
    assert ocr.calls == 1 and llm.calls == 2
    run_extraction(pending, extractor, use_ocr_cache=False)
    assert ocr.calls == 2


def test_errors_put_declaration_in_review(pending):
    extractor = OcrLlmExtractor(llm=ScriptedLLM(bad_payload()).client(), ocr_engine=FakeOCR(), max_repairs=0)
    run_extraction(pending, extractor)
    pending.refresh_from_db()
    assert pending.status == AssetDeclaration.Status.NEEDS_REVIEW
    assert "BŁĄD" in pending.notes and pending.verification["issues"]
    assert summarize(pending).issues


def test_wrong_file_is_sent_to_review(pending):
    MP.objects.filter(sejm_id=1).update(first_name="Anna", last_name="Nowak")
    pending.mp.refresh_from_db()
    extractor = OcrLlmExtractor(llm=ScriptedLLM(good_payload()).client(), ocr_engine=FakeOCR())
    run_extraction(pending, extractor)
    pending.refresh_from_db()
    assert pending.status == AssetDeclaration.Status.NEEDS_REVIEW
    assert "Możliwy zły plik" in pending.notes


def test_llm_outage_marks_declaration_failed_but_keeps_ocr_text_for_retry(pending):
    ocr = FakeOCR()
    dead = LLMClient(base_url="https://llm.test/v1/chat/completions", model="m", max_retries=0,
                     transport=httpx.MockTransport(lambda r: httpx.Response(500, text="down")))
    run = run_extraction(pending, OcrLlmExtractor(llm=dead, ocr_engine=ocr))
    pending.refresh_from_db()
    assert not run.success and pending.status == AssetDeclaration.Status.FAILED
    assert "not responding correctly" in pending.notes


def test_profile_page_shows_structured_extras_and_issues(client, synced):
    mp = MP.objects.get(sejm_id=1)
    d = AssetDeclaration.objects.create(
        mp=mp, year=2024, status=AssetDeclaration.Status.NEEDS_REVIEW, extractor="ocr_llm", ocr_engine="chandra",
        confidence=0.62,
        notes="BŁĄD [real_estate[1].value]: Kwoty 909000 PLN nie ma w tekście OCR\nKurs EUR przybliżony",
        structured={
            "business": [{"description": "Indywidualna praktyka lekarska", "managed_only": False,
                          "mode": "osobiście", "suspended": True}],
            "board_roles": [{"entity": "TRANSREM sp. z o.o.", "body": "SUPERVISORY", "since": "kwiecień 2011"}],
        },
    )
    d.items.create(category="CASH_PLN", label="Środki", value=1000, value_pln=1000)
    html = client.get(reverse("mp_detail", args=[1])).content.decode()
    assert "Indywidualna praktyka lekarska" in html and "zawieszona" in html
    assert "TRANSREM sp. z o.o.: rada nadzorcza, od kwiecień 2011" in html
    assert "Wykryte problemy z odczytem (1)" in html and "909000" in html
    assert "Kurs EUR" not in html
    assert "OCR (chandra) + model językowy" in html


# ---------- OCR ----------

def line(text, x0, y, score=0.95, h=20):
    return OCRLine(text, score, x0, y, x0 + 100, y + h)


def test_rows_merge_fragments_on_the_same_visual_line():
    page = OCRPage(1, [
        line("5954CHF; 2815USD", 300, 102), line("- środki w walucie obcej:", 10, 100), line("Zasoby pieniężne:", 10, 40),
        line("następna linia", 10, 140),
    ])
    assert page.text.splitlines() == [
        "Zasoby pieniężne:", "- środki w walucie obcej:  5954CHF; 2815USD", "następna linia",
    ]


def test_ocr_pdf_uses_engine_and_drops_low_confidence_lines():
    class Engine:
        name = "stub"

        def recognize(self, image):
            return [line("Dobra linia", 10, 10, 0.95), line("śmieci", 10, 50, 0.1), line("  ", 10, 90, 0.99)]

    result = ocr_pdf(make_pdf(), Engine())
    assert result.engine == "stub" and result.pages[0].text == "Dobra linia"
    assert result.text.startswith("=== STRONA 1 ===") and result.mean_confidence == 0.95


def test_ocr_pdf_prefers_embedded_text_layer(monkeypatch):
    monkeypatch.setattr(ocr_base, "text_layer", lambda pdf, n: ["x" * 300, "y" * 300])

    class NeverCalled:
        name = "never"

        def recognize(self, image):
            raise AssertionError("OCR should not have run")

    result = ocr_pdf(b"%PDF", NeverCalled())
    assert result.engine == "pdf-text" and len(result.pages) == 2 and result.mean_confidence == 1.0
    forced = ocr_pdf(make_pdf(), type("E", (), {"name": "stub", "recognize": lambda self, i: [line("tekst", 0, 0)]})(),
                     force_ocr=True)
    assert forced.engine == "stub"


def test_ocr_pdf_errors():
    with pytest.raises(OCRError):
        ocr_pdf(b"to nie pdf", type("E", (), {"name": "e", "recognize": lambda s, i: []})())


@pytest.mark.ocr
def test_missing_chandra_gives_helpful_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "chandra" or name.startswith("chandra."):
            raise ImportError("no chandra")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from apps.assets.ocr.chandra import ChandraEngine

    ChandraEngine._instance = None
    with pytest.raises(OCRError, match="uv sync --extra ocr"):
        ChandraEngine("cpu")


@pytest.mark.ocr
@pytest.mark.skipif(not __import__("os").environ.get("RUN_OCR_TESTS"), reason="wolny test; ustaw RUN_OCR_TESTS=1")
def test_real_chandra_reports_cuda_when_requested():
    from apps.assets.ocr import get_ocr_engine

    engine = get_ocr_engine("chandra")
    assert engine.name == "chandra"
