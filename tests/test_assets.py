from decimal import Decimal

import pytest

from apps.assets.extractors import DeclarationContext, ExtractedDeclaration, ExtractedItem, ExtractorError
from apps.assets.extractors.mock import MockExtractor
from apps.assets.models import AssetCategory, AssetDeclaration
from apps.assets.services import summary
from apps.assets.services.extraction import run_extraction
from apps.assets.sources import DeclarationRef, DeclarationSourceError, import_declaration
from apps.parliament.models import MP

from .conftest import make_pdf

pytestmark = pytest.mark.django_db

CTX = DeclarationContext(mp_sejm_id=7, mp_name="Anna Nowak", year=2024, kind="ANNUAL", district_name="Kraków")


def test_mock_extractor_is_deterministic():
    a = MockExtractor().extract(None, CTX)
    b = MockExtractor().extract(None, CTX)
    assert a == b
    categories = {i.category for i in a.items}
    assert AssetCategory.CASH_PLN in categories and AssetCategory.REAL_ESTATE in categories


class FixedExtractor:
    name = "fixed"

    def __init__(self, items, confidence=0.95):
        self.result = ExtractedDeclaration(items=items, confidence=confidence, model="test")

    def extract(self, pdf_bytes, context):
        return self.result


def _declaration(synced, year=2024, kind="ANNUAL"):
    mp = MP.objects.get(sejm_id=1)
    return AssetDeclaration.objects.create(mp=mp, year=year, kind=kind)


def test_run_extraction_converts_currency_and_summarizes(synced):
    d = _declaration(synced)
    extractor = FixedExtractor([
        ExtractedItem(category="CASH_PLN", label="Oszczędności", value=Decimal("100000")),
        ExtractedItem(category="CASH_FOREIGN", label="Euro", value=Decimal("1000"), currency="EUR"),
        ExtractedItem(category="REAL_ESTATE", label="Mieszkanie", value=Decimal("500000"), area_m2=Decimal("50")),
        ExtractedItem(category="LIABILITY", label="Kredyt", value=Decimal("200000")),
        ExtractedItem(category="INCOME", label="Uposażenie", value=Decimal("180000")),
    ])
    run = run_extraction(d, extractor)
    d.refresh_from_db()
    assert run.success
    assert d.status == AssetDeclaration.Status.EXTRACTED
    assert d.items.get(currency="EUR").value_pln == Decimal("4300.00")

    s = summary.summarize(d)
    assert s.savings == Decimal("104300.00")
    assert s.real_estate == Decimal("500000")
    assert s.liabilities == Decimal("200000")
    assert s.net_worth == Decimal("404300.00")
    assert s.income == Decimal("180000")
    assert summary.describe_item(d.items.get(label="Mieszkanie")) == "Mieszkanie 50 m² o wartości 500 tys. zł"


def test_low_confidence_or_bad_values_need_review(synced):
    d = _declaration(synced)
    run_extraction(d, FixedExtractor([ExtractedItem(category="CASH_PLN", label="X", value=Decimal(1))], 0.3))
    d.refresh_from_db()
    assert d.status == AssetDeclaration.Status.NEEDS_REVIEW

    d2 = _declaration(synced, year=2025)
    run_extraction(d2, FixedExtractor([ExtractedItem(category="CASH_PLN", label="X", value=Decimal(-5))]))
    d2.refresh_from_db()
    assert d2.status == AssetDeclaration.Status.NEEDS_REVIEW
    assert "Negative value" in d2.notes


def test_timeline_and_insights(synced):
    for year, cash in [(2023, 100_000), (2024, 150_000)]:
        d = _declaration(synced, year=year)
        run_extraction(d, FixedExtractor([ExtractedItem(category="CASH_PLN", label="Gotówka", value=Decimal(cash))]))
    mp = MP.objects.get(sejm_id=1)
    series = summary.timeline(mp)
    assert [s.year for s in series] == [2023, 2024]
    texts = [i["text"] for i in summary.insights(series)]
    assert any("wzrósł o 50 tys. zł (+50%)" in t for t in texts)
    assert summary.chart_series(series)["net"] == [100000.0, 150000.0]


class PricedExtractor:
    """Stands in for ocr_llm with a given cost (None means the provider reported no price)."""

    name = "ocr_llm"

    def __init__(self, cost, model="deepseek/test-1", tokens=(100, 40), calls=1):
        self.result = ExtractedDeclaration(
            items=[ExtractedItem(category="CASH_PLN", label="Gotówka", value=Decimal("10"))],
            confidence=0.9,
            model=model,
            prompt_tokens=tokens[0] if tokens else None,
            completion_tokens=tokens[1] if tokens else None,
            cost_usd=cost,
            llm_calls=calls,
        )

    def extract(self, pdf_bytes, context):
        return self.result


def test_extraction_stores_model_cost(synced):
    d = _declaration(synced, year=2022)
    run = run_extraction(d, PricedExtractor(Decimal("0.0012"), calls=2))
    assert run.cost_usd == Decimal("0.0012")
    assert (run.prompt_tokens, run.completion_tokens, run.llm_calls) == (100, 40, 2)
    assert run.usage_summary() == (
        "deepseek/test-1 · 0,0012 USD · 100 tokenów wejścia + 40 wyjścia · 2 wywołania modelu"
    )
    assert d.latest_successful_run.pk == run.pk


def _series(years_costs, mock_years=()):
    for year, cost in years_costs:
        run_extraction(_declaration_for_year(year), PricedExtractor(cost))
    for year in mock_years:
        run_extraction(_declaration_for_year(year), MockExtractor())
    return summary.timeline(MP.objects.get(sejm_id=1))


def _declaration_for_year(year):
    return AssetDeclaration.objects.create(mp=MP.objects.get(sejm_id=1), year=year, kind="ANNUAL")


def test_processing_cost_sums_runs_and_lists_models(synced):
    series = _series([(2023, Decimal("0.001")), (2024, Decimal("0.0025"))])
    cost = summary.processing_cost(series)
    assert cost.declarations == 2 and cost.priced == 2 and cost.unpriced == 0
    assert cost.total_usd == Decimal("0.0035") and cost.total_text == "0,0035 USD"
    assert (cost.prompt_tokens, cost.completion_tokens) == (200, 80)
    assert cost.models == ["deepseek/test-1"]


def test_processing_cost_flags_runs_without_price(synced):
    series = _series([(2023, None), (2024, Decimal("0.002"))])
    cost = summary.processing_cost(series)
    assert cost.total_usd == Decimal("0.002")
    assert (cost.declarations, cost.priced, cost.unpriced) == (2, 1, 1)


def test_processing_cost_without_any_price_reports_tokens_only(synced):
    cost = summary.processing_cost(_series([(2024, None)]))
    assert cost.total_usd is None and cost.total_text == ""
    assert cost.prompt_tokens == 100


def test_processing_cost_ignores_demo_data(synced):
    assert summary.processing_cost(_series([], mock_years=[2023, 2024])) is None
    mixed = summary.processing_cost(_series([(2025, Decimal("0.01"))], mock_years=[2022]))
    assert mixed.declarations == 1 and mixed.total_usd == Decimal("0.01")


def test_profile_shows_cost_and_model(synced, client):
    run_extraction(_declaration_for_year(2024), PricedExtractor(Decimal("0.0042")))
    html = client.get(MP.objects.get(sejm_id=1).get_absolute_url()).content.decode()
    assert "Koszt policzenia tych danych" in html
    assert "0,0042 USD" in html and "deepseek/test-1" in html


def test_profile_hides_cost_for_demo_data(synced, client):
    run_extraction(_declaration_for_year(2024), MockExtractor())
    html = client.get(MP.objects.get(sejm_id=1).get_absolute_url()).content.decode()
    assert "Koszt policzenia tych danych" not in html


def test_failed_extractor_marks_declaration(synced):
    class Broken:
        name = "broken"

        def extract(self, pdf_bytes, context):
            raise ExtractorError("Missing PDF file")

    d = _declaration(synced)
    run = run_extraction(d, Broken())
    d.refresh_from_db()
    assert not run.success
    assert d.status == AssetDeclaration.Status.FAILED
    assert "Missing PDF file" in d.notes


def test_import_declaration_from_file(synced, media_tmp, tmp_path):
    pdf = tmp_path / "osw.pdf"
    pdf.write_bytes(make_pdf())
    d, changed = import_declaration(DeclarationRef(mp_sejm_id=1, year=2024, file=str(pdf)))
    assert changed and d.status == AssetDeclaration.Status.PENDING
    assert d.raw.file.name.endswith(".pdf")
    _, changed_again = import_declaration(DeclarationRef(mp_sejm_id=1, year=2024, file=str(pdf)))
    assert not changed_again


def test_import_rejects_non_pdf(synced, media_tmp, tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_text("<html>challenge</html>")
    with pytest.raises(DeclarationSourceError):
        import_declaration(DeclarationRef(mp_sejm_id=1, year=2024, file=str(bad)))
    with pytest.raises(DeclarationSourceError):
        import_declaration(DeclarationRef(mp_sejm_id=999, year=2024, file=str(bad)))
