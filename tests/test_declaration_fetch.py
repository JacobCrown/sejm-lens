from dataclasses import dataclass

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.assets.models import AssetDeclaration
from apps.assets.sources import (
    DeclarationSourceError,
    SejmSiteClient,
    download_pdf,
    parse_declaration_table,
)
from apps.core.models import IngestionRun, RawDocument
from apps.parliament.models import MP

from .conftest import make_pdf

HOLDER = "view:_id1:_id2:facetMain:_id191:holdMajatek"
ORKA = "https://orka.sejm.gov.pl/osw10.nsf/0"


def pdf_url(unid: str, name: str) -> str:
    return f"{ORKA}/{unid}/%24File/{name}"


def table(mp_id: int, rows=None, holder=HOLDER) -> str:
    rows = rows or [
        ("Początek kadencji", "2023-11-09", pdf_url("AAA1", f"OSW10P_{mp_id:03d}.pdf")),
        ("I rok kadencji", "2024-04-30", pdf_url("AAA2", f"OSW101_{mp_id:03d}.pdf")),
        ("II rok kadencji", "2025-04-30", pdf_url("AAA3", f"OSW102_{mp_id:03d}.pdf")),
    ]
    body = "".join(
        f'<tr><td>{label}</td><td><a id="x:{i}" href="{url}" class="icon" title="skan" target="_blank">{day}</a></td></tr>'
        for i, (label, day, url) in enumerate(rows)
    )
    return (
        f'<div id="{holder}" class="holder"><div id="{holder}Inner">'
        '<table class="osw table table-condensed"><caption class="wcag-hide">Lista o&#347;wiadcze&#324;</caption>'
        f"<tbody>{body}</tbody></table></div></div>"
    )


CARD_PAGE = """<html><form id="view:_id1" method="post">
<input type="hidden" name="$$viewid" id="view:_id1__VUID" value="!abc!">
</form><script>eventId = 'view:_id1:_id2:facetMain:_id191:loadData';
dojo.connect(dojo.byId("osw"), "onclick", function(e) { poselLoadContent(e, 'Majatek', '%s'); });
</script></html>""" % HOLDER


@dataclass
class FakeResponse:
    status_code: int = 200
    text: str = ""
    content: bytes = b""


class FakeSession:
    def __init__(self, tables=None, pdfs=None, fail_cards=()):
        self.tables = tables or {}
        self.pdfs = pdfs or {}
        self.fail_cards = set(fail_cards)
        self.requests: list[tuple[str, str]] = []
        self.posted: list[dict] = []

    def _mp_id(self, url):
        return int(url.split("id=")[1].split("&")[0])

    def get(self, url, **kwargs):
        self.requests.append(("GET", url))
        if "posel.xsp" in url:
            if self._mp_id(url) in self.fail_cards:
                return FakeResponse(403, "Human Verification")
            return FakeResponse(text=CARD_PAGE)
        if url in self.pdfs:
            return FakeResponse(content=self.pdfs[url])
        return FakeResponse(404, "nie ma")

    def post(self, url, data=None, **kwargs):
        self.requests.append(("POST", url))
        self.posted.append(data)
        return FakeResponse(text=self.tables[self._mp_id(url)])


def test_parse_maps_labels_to_kind_and_year():
    card = parse_declaration_table(table(7), 7)
    assert [(r.kind, r.year) for r in card.refs] == [("START", 2023), ("ANNUAL", 2023), ("ANNUAL", 2024)]
    assert card.refs[2].url == pdf_url("AAA3", "OSW102_007.pdf")
    assert card.skipped == []


def test_parse_end_of_term_and_roman_numerals():
    rows = [
        ("III rok kadencji", "2026-04-29", pdf_url("B1", "OSW103_007.pdf")),
        ("Koniec kadencji", "2027-11-10", pdf_url("B2", "OSW10K_007.pdf")),
    ]
    refs = parse_declaration_table(table(7, rows), 7).refs
    assert [(r.kind, r.year) for r in refs] == [("ANNUAL", 2025), ("END", 2027)]


def test_parse_skips_unusable_rows_but_keeps_the_rest():
    rows = [
        ("Początek kadencji", "2023-11-09", pdf_url("A", "OSW10P_007.pdf")),
        ("I rok kadencji", "2024-04-30", pdf_url("B", "OSW101_999.pdf")),
        ("II rok kadencji", "wkrótce", pdf_url("C", "OSW102_007.pdf")),
        ("Korekta", "2025-06-01", pdf_url("D", "OSW103_007.pdf")),
        ("III rok kadencji", "2026-04-30", "https://evil.example/osw10.nsf/OSW103_007.pdf"),
        ("IV rok kadencji", "2027-04-30", "http://orka.sejm.gov.pl/osw10.nsf/x/OSW104_007.pdf"),
    ]
    card = parse_declaration_table(table(7, rows), 7)
    assert [(r.kind, r.year) for r in card.refs] == [("START", 2023)]
    assert len(card.skipped) == 5
    assert any("does not belong to MP 7" in s for s in card.skipped)
    assert any("invalid filing date" in s for s in card.skipped)


def test_parse_row_without_link_is_skipped():
    fragment = "<table><tbody><tr><td>I rok kadencji</td><td>2024-04-30</td></tr></tbody></table>"
    card = parse_declaration_table(fragment, 7)
    assert card.refs == [] and len(card.skipped) == 1


def test_parse_duplicate_year_keeps_latest_filing():
    rows = [
        ("I rok kadencji", "2024-04-30", pdf_url("OLD", "OSW101_007.pdf")),
        ("II rok kadencji", "2024-06-15", pdf_url("NEW", "OSW102_007.pdf")),
    ]
    card = parse_declaration_table(table(7, rows), 7)
    assert len(card.refs) == 1 and card.refs[0].url == pdf_url("NEW", "OSW102_007.pdf")
    assert card.refs[0].year == 2023 and any("duplicate" in s for s in card.skipped)


def test_parse_empty_table_yields_nothing():
    card = parse_declaration_table('<div id="x"><table><tbody></tbody></table></div>', 7)
    assert card.refs == [] and card.skipped == []


def test_client_replays_xpages_partial_refresh():
    session = FakeSession(tables={7: table(7)})
    card = SejmSiteClient(term=10, session=session, delay=0).declarations(7)
    assert len(card.refs) == 3
    assert session.requests[0] == ("GET", "https://www.sejm.gov.pl/sejm10.nsf/posel.xsp?id=007&type=A")
    form = session.posted[0]
    assert form["$$viewid"] == "!abc!"
    assert form["$$xspsubmitid"] == "view:_id1:_id2:facetMain:_id191:loadData"
    assert form["$$xspsubmitvalue"] == "Majatek" and form["$$ajaxid"] == HOLDER


def test_client_reports_challenge_page_and_missing_section():
    blocked = SejmSiteClient(term=10, session=FakeSession(fail_cards={7}), delay=0)
    with pytest.raises(DeclarationSourceError, match="HTTP 403"):
        blocked.declarations(7)
    wrong = SejmSiteClient(term=10, session=FakeSession(tables={7: "<div>nic</div>"}), delay=0)
    with pytest.raises(DeclarationSourceError, match="no declarations section"):
        wrong.declarations(7)


def test_download_pdf_validates_content():
    ok, html_page = pdf_url("OK", "OSW10P_001.pdf"), pdf_url("BAD", "OSW10P_002.pdf")
    session = FakeSession(pdfs={ok: make_pdf(), html_page: b"<html>challenge</html>"})
    assert download_pdf(ok, session).startswith(b"%PDF")
    with pytest.raises(DeclarationSourceError, match="did not return a PDF"):
        download_pdf(html_page, session)
    with pytest.raises(DeclarationSourceError, match="HTTP 404"):
        download_pdf(pdf_url("X", "nope.pdf"), session)


@pytest.fixture
def fetch_env(synced, media_tmp, monkeypatch, settings):
    settings.DECLARATION_FETCH_DELAY = 0
    pdfs = {}
    tables = {}
    for mp_id in (1, 2, 3, 4, 5, 6):
        rows = [
            ("Początek kadencji", "2023-11-09", pdf_url(f"S{mp_id}", f"OSW10P_{mp_id:03d}.pdf")),
            ("I rok kadencji", "2024-04-30", pdf_url(f"T{mp_id}", f"OSW101_{mp_id:03d}.pdf")),
        ]
        tables[mp_id] = table(mp_id, rows)
        for _, _, url in rows:
            pdfs[url] = make_pdf(f"{mp_id} {url}")
    session = FakeSession(tables=tables, pdfs=pdfs)
    monkeypatch.setattr("apps.assets.management.commands.sync_declarations.new_session", lambda: session)
    return session


def test_fetch_imports_declarations_for_all_mps(fetch_env):
    call_command("sync_declarations", "--fetch")
    assert AssetDeclaration.objects.count() == 12
    d = AssetDeclaration.objects.get(mp__sejm_id=2, year=2023, kind="ANNUAL")
    assert d.status == AssetDeclaration.Status.PENDING and not d.is_demo
    assert d.source_url == pdf_url("T2", "OSW101_002.pdf")
    assert d.raw.file.name.endswith(".pdf")
    assert RawDocument.objects.filter(source="declaration_pdf").count() == 12


def test_fetch_second_run_downloads_nothing_new(fetch_env):
    call_command("sync_declarations", "--fetch")
    fetch_env.requests.clear()
    call_command("sync_declarations", "--fetch")
    assert not [r for r in fetch_env.requests if "orka" in r[1]]
    assert AssetDeclaration.objects.count() == 12
    call_command("sync_declarations", "--fetch", "--force", "--mp", "1")
    assert len([r for r in fetch_env.requests if "orka" in r[1]]) == 2


def test_fetch_filters_mp_year_and_limit(fetch_env):
    call_command("sync_declarations", "--fetch", "--mp", "3", "--year", "2023")
    assert fetch_env.requests.count(("GET", "https://www.sejm.gov.pl/sejm10.nsf/posel.xsp?id=003&type=A")) == 1
    assert set(AssetDeclaration.objects.values_list("mp__sejm_id", "kind")) == {(3, "ANNUAL"), (3, "START")}
    AssetDeclaration.objects.all().delete()
    call_command("sync_declarations", "--fetch", "--limit", "2")
    assert set(AssetDeclaration.objects.values_list("mp__sejm_id", flat=True)) == {1, 2}


def test_fetch_error_for_one_mp_does_not_stop_the_rest(fetch_env):
    fetch_env.fail_cards = {1}
    call_command("sync_declarations", "--fetch", "--limit", "3")
    assert set(AssetDeclaration.objects.values_list("mp__sejm_id", flat=True)) == {2, 3}
    stats = IngestionRun.objects.filter(kind="sync_declarations").latest("id").stats
    assert stats["errors"] == 1 and stats["cards"] == 2 and stats["imported"] == 4


def test_fetch_with_extract_analyses_new_files(fetch_env):
    call_command("sync_declarations", "--fetch", "--mp", "1", "--extract", "--extractor", "mock")
    assert AssetDeclaration.objects.filter(mp__sejm_id=1).exclude(status="PENDING").count() == 2


def test_fetch_without_mps_asks_for_sync_sejm(db, settings):
    with pytest.raises(CommandError, match="sync_sejm"):
        call_command("sync_declarations", "--fetch")


def test_fetch_cannot_combine_with_manual_sources(fetch_env):
    with pytest.raises(CommandError, match="--fetch"):
        call_command("sync_declarations", "--fetch", "--url", "https://x")


def test_unknown_mp_ids_are_not_queried(fetch_env):
    assert not MP.objects.filter(sejm_id=999).exists()
    with pytest.raises(CommandError):
        call_command("sync_declarations", "--fetch", "--mp", "999")
    assert fetch_env.requests == []

