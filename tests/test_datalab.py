import httpx
import pytest
from django.core.management import call_command

from apps.assets.models import AssetDeclaration
from apps.assets.ocr.datalab import DatalabClient, usd_from_breakdown
from apps.assets.services.datalab_store import ocr_declaration
from apps.assets.sources import DeclarationRef, import_declaration
from .conftest import make_pdf


def test_cost_is_read_from_cents():
    from decimal import Decimal
    assert usd_from_breakdown({"total_cents": 12}) == Decimal("0.120000")
    assert usd_from_breakdown(None) is None


def test_convert_polls_and_downloads_the_result_without_sending_the_key_twice():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("x-api-key")))
        if request.method == "POST":
            return httpx.Response(200, json={
                "success": True,
                "request_id": "abc",
                "request_check_url": "https://www.datalab.to/api/v1/convert/abc",
            })
        if request.url.path.endswith("/abc"):
            return httpx.Response(200, json={
                "status": "complete",
                "success": True,
                "result_url": "https://cdn.example/result.json",
                "cost_breakdown": {"total_cents": 8},
            })
        if request.url.host == "cdn.example":
            return httpx.Response(200, json={
                "markdown": "=== page ===\n593000 PLN",
                "page_count": 5,
                "parse_quality_score": 4.5,
                "cost_breakdown": {"total_cents": 8},
            })
        return httpx.Response(404)

    client = DatalabClient("secret", transport=httpx.MockTransport(handler), poll_interval=0)
    payload = client.convert_pdf(b"%PDF", "osw.pdf")
    client.close()
    assert payload["markdown"].endswith("593000 PLN")
    assert seen[0][2] == "secret"
    assert seen[-1][2] is None or seen[-1][1] == "/result.json"


@pytest.mark.django_db
def test_stored_ocr_is_not_requested_again(synced, media_tmp, tmp_path):
    pdf = tmp_path / "osw.pdf"
    pdf.write_bytes(make_pdf())
    declaration, _ = import_declaration(DeclarationRef(mp_sejm_id=1, year=2024, file=str(pdf)))

    class Fake:
        calls = 0

        def convert_pdf(self, content, filename):
            self.calls += 1
            return {
                "markdown": "593000 PLN",
                "page_count": 5,
                "parse_quality_score": 4,
                "cost_breakdown": {"total_cents": 15},
                "request_id": "abc",
            }

    fake = Fake()
    assert ocr_declaration(declaration, fake) == "stored"
    declaration.refresh_from_db()
    assert declaration.ocr_engine == "datalab"
    assert declaration.ocr_text == "593000 PLN"
    assert declaration.ocr_pages == 5
    assert declaration.ocr_response["request_id"] == "abc"
    assert ocr_declaration(declaration, fake) == "skipped"
    assert fake.calls == 1


@pytest.mark.django_db
def test_limit_counts_mps_and_years_can_be_several(synced, media_tmp, tmp_path):
    from apps.assets.services.datalab_store import select_declarations

    for sejm_id, year in [(1, 2024), (1, 2025), (2, 2025), (2, 2026)]:
        path = tmp_path / f"{sejm_id}-{year}.pdf"
        path.write_bytes(make_pdf(f"{sejm_id} {year}"))
        import_declaration(DeclarationRef(mp_sejm_id=sejm_id, year=year, file=str(path)))

    chosen = select_declarations(years=[2025, 2026], limit=1)
    assert {(d.mp.sejm_id, d.year) for d in chosen} == {(1, 2025)}

    both = select_declarations(years=[2025, 2026], limit=2)
    assert {(d.mp.sejm_id, d.year) for d in both} == {(1, 2025), (2, 2025), (2, 2026)}

    pending = select_declarations(
        years=[2025, 2026],
        limit=1,
        qs=AssetDeclaration.objects.filter(status=AssetDeclaration.Status.PENDING),
    )
    assert {(d.mp.sejm_id, d.year) for d in pending} == {(1, 2025)}


@pytest.mark.django_db
def test_a_new_pdf_drops_the_stored_ocr(synced, media_tmp, tmp_path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(make_pdf("pierwszy"))
    second.write_bytes(make_pdf("drugi"))
    declaration, _ = import_declaration(DeclarationRef(mp_sejm_id=1, year=2024, file=str(first)))
    declaration.ocr_text = "stary odczyt"
    declaration.ocr_engine = "datalab"
    declaration.save(update_fields=["ocr_text", "ocr_engine"])
    again, changed = import_declaration(DeclarationRef(mp_sejm_id=1, year=2024, file=str(second)))
    assert changed
    again.refresh_from_db()
    assert again.ocr_text == ""
    assert again.ocr_engine == ""


@pytest.mark.django_db
def test_clear_ocr_keeps_the_pdf_and_the_llm_result(synced, media_tmp, tmp_path):
    from apps.assets.models import AssetItem
    from apps.assets.services.datalab_store import clear_extractions, clear_ocr

    pdf = tmp_path / "osw.pdf"
    pdf.write_bytes(make_pdf())
    declaration, _ = import_declaration(DeclarationRef(mp_sejm_id=1, year=2025, file=str(pdf)))
    declaration.ocr_text = "593000 PLN"
    declaration.ocr_engine = "datalab"
    declaration.ocr_response = {"request_id": "abc"}
    declaration.structured = {"declarant": {"full_name": "JAN"}}
    declaration.status = AssetDeclaration.Status.EXTRACTED
    declaration.save()
    AssetItem.objects.create(declaration=declaration, category="CASH_PLN", label="gotówka", value="1")
    raw_id = declaration.raw_id

    assert clear_ocr([declaration]) == 1
    declaration.refresh_from_db()
    assert declaration.ocr_text == "" and declaration.ocr_response is None
    assert declaration.raw_id == raw_id
    assert declaration.structured["declarant"]["full_name"] == "JAN"
    assert declaration.items.count() == 1

    assert clear_extractions([declaration]) == 1
    declaration.refresh_from_db()
    assert declaration.ocr_text == ""
    assert declaration.structured is None and declaration.items.count() == 0
    assert declaration.status == AssetDeclaration.Status.PENDING
    assert declaration.raw_id == raw_id


@pytest.mark.django_db
def test_clear_extractions_skips_demo_rows(synced):
    from apps.assets.services.datalab_store import clear_extractions
    from apps.parliament.models import MP

    mp = MP.objects.get(sejm_id=1)
    demo = AssetDeclaration.objects.create(
        mp=mp, year=2025, kind=AssetDeclaration.Kind.ANNUAL, is_demo=True,
        status=AssetDeclaration.Status.EXTRACTED, structured={"demo": True},
    )
    assert clear_extractions([demo]) == 0
    demo.refresh_from_db()
    assert demo.structured == {"demo": True} and demo.is_demo


@pytest.mark.django_db
def test_delete_declarations_removes_the_row_and_the_pdf(synced, media_tmp, tmp_path):
    import os

    from apps.assets.services.datalab_store import delete_declarations
    from apps.core.models import RawDocument

    pdf = tmp_path / "osw.pdf"
    pdf.write_bytes(make_pdf())
    declaration, _ = import_declaration(DeclarationRef(mp_sejm_id=1, year=2025, file=str(pdf)))
    path = declaration.raw.file.path
    raw_id = declaration.raw_id
    assert delete_declarations([declaration]) == 1
    assert not AssetDeclaration.objects.filter(mp__sejm_id=1, year=2025).exists()
    assert not RawDocument.objects.filter(pk=raw_id).exists()
    assert not os.path.exists(path)


@pytest.mark.django_db
def test_ocr_command_skips_a_deleted_pdf(synced, media_tmp, tmp_path, settings, monkeypatch):
    import os

    settings.DATALAB_API_KEY = "test-key"
    old = tmp_path / "old.pdf"
    new = tmp_path / "new.pdf"
    old.write_bytes(make_pdf("stary"))
    new.write_bytes(make_pdf("nowy"))
    gone, _ = import_declaration(DeclarationRef(mp_sejm_id=1, year=2023, file=str(old)))
    import_declaration(DeclarationRef(mp_sejm_id=1, year=2025, file=str(new)))
    os.remove(gone.raw.file.path)
    seen = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def convert_pdf(self, content, filename):
            seen.append(filename)
            return {
                "markdown": "tekst",
                "page_count": 1,
                "parse_quality_score": 5,
                "cost_breakdown": {"total_cents": 1},
            }

        def close(self):
            pass

    monkeypatch.setattr(
        "apps.assets.management.commands.ocr_declarations.DatalabClient", FakeClient
    )
    call_command("ocr_declarations", "--mp", "1")
    assert seen == ["osw_1_2025_annual.pdf"]
    gone.refresh_from_db()
    assert gone.ocr_text == ""


@pytest.mark.django_db
def test_command_requires_an_api_key(synced, settings):
    settings.DATALAB_API_KEY = ""
    AssetDeclaration.objects.all().delete()
    with pytest.raises(Exception, match="DATALAB_API_KEY"):
        call_command("ocr_declarations")
