"""Fetching declaration PDFs.

The list lives on the MP's page on sejm.gov.pl, loaded by an XPages partial refresh of the
"Oświadczenia majątkowe" section. The site and the ORKA PDFs reject clients whose TLS fingerprint
is not a browser's, so we use curl_cffi impersonating Chrome (no JavaScript). PDFs can also be
imported by hand from a URL, a file or a CSV.
"""

import csv
import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as curl_requests
from django.conf import settings

from apps.core.raw import store_file
from apps.parliament.models import MP

from .models import AssetDeclaration

logger = logging.getLogger(__name__)

SEJM_SITE_URL = "https://www.sejm.gov.pl"
ALLOWED_PDF_HOSTS = {"orka.sejm.gov.pl", "www.sejm.gov.pl", "sejm.gov.pl"}


class DeclarationSourceError(Exception):
    pass


@dataclass
class DeclarationRef:
    mp_sejm_id: int
    year: int
    kind: str = AssetDeclaration.Kind.ANNUAL
    url: str = ""
    file: str = ""


@dataclass
class ParsedCard:
    refs: list[DeclarationRef] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def new_session() -> curl_requests.Session:
    return curl_requests.Session(impersonate="chrome")


def download_pdf(url: str, session=None) -> bytes:
    try:
        resp = (session or new_session()).get(url, timeout=60)
    except curl_requests.exceptions.RequestException as exc:
        raise DeclarationSourceError(f"Failed to fetch {url}: {exc}") from exc
    if resp.status_code != 200:
        raise DeclarationSourceError(f"{url} returned HTTP {resp.status_code}")
    if not resp.content.startswith(b"%PDF"):
        raise DeclarationSourceError(
            f"{url} did not return a PDF (probably an anti-bot challenge). "
            "Download the file in a browser and import it with --file."
        )
    return resp.content


def import_declaration(
    ref: DeclarationRef, term: int | None = None, session=None
) -> tuple[AssetDeclaration, bool]:
    """Stores the PDF as a RawDocument and creates or updates the declaration. Returns (object, changed)."""
    mp = MP.objects.filter(term_id=term or settings.SEJM_TERM, sejm_id=ref.mp_sejm_id).first()
    if mp is None:
        raise DeclarationSourceError(f"No MP with id {ref.mp_sejm_id}. Run sync_sejm first.")
    if ref.file:
        path = Path(ref.file)
        if not path.exists():
            raise DeclarationSourceError(f"File does not exist: {path}")
        content = path.read_bytes()
        if not content.startswith(b"%PDF"):
            raise DeclarationSourceError(f"{path} is not a PDF")
    elif ref.url:
        content = download_pdf(ref.url, session)
    else:
        raise DeclarationSourceError("Provide a url or a PDF file")

    source_id = f"{mp.term_id}/{mp.sejm_id:03d}/{ref.year}/{ref.kind}"
    filename = f"osw_{mp.sejm_id:03d}_{ref.year}_{ref.kind.lower()}.pdf"
    raw, changed = store_file("declaration_pdf", source_id, content, filename, url=ref.url)

    declaration, created = AssetDeclaration.objects.get_or_create(mp=mp, year=ref.year, kind=ref.kind)
    if created or changed or declaration.raw_id != raw.id:
        declaration.raw = raw
        declaration.source_url = ref.url
        declaration.status = AssetDeclaration.Status.PENDING
        declaration.is_demo = False
        declaration.save()
        return declaration, True
    return declaration, False


def find_imported(ref: DeclarationRef, term: int | None = None) -> AssetDeclaration | None:
    """Returns the stored declaration for the same file (the URL contains the document id)."""
    return AssetDeclaration.objects.filter(
        mp__term_id=term or settings.SEJM_TERM,
        mp__sejm_id=ref.mp_sejm_id,
        year=ref.year,
        kind=ref.kind,
        source_url=ref.url,
        is_demo=False,
        raw__isnull=False,
    ).first()


def read_manifest(path: str) -> list[DeclarationRef]:
    """CSV with columns: mp_id, year, kind (optional), url or file."""
    refs = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            refs.append(
                DeclarationRef(
                    mp_sejm_id=int(row["mp_id"]),
                    year=int(row["year"]),
                    kind=(row.get("kind") or AssetDeclaration.Kind.ANNUAL).upper(),
                    url=(row.get("url") or "").strip(),
                    file=(row.get("file") or "").strip(),
                )
            )
    return refs


_ROW_RE = re.compile(r"<tr>\s*<td>(?P<label>.*?)</td>\s*<td>(?P<cell>.*?)</td>\s*</tr>", re.S)
_LINK_RE = re.compile(r'<a\b[^>]*\bhref="(?P<href>[^"]+)"[^>]*>(?P<text>.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ANNUAL_RE = re.compile(r"^\S+\s+rok\s+kadencji$", re.I)
_FILE_ID_RE = re.compile(r"_(\d+)\.pdf$", re.I)


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub("", fragment)).split())


def _kind_and_year(label: str, filed: date) -> tuple[str, int] | None:
    """An annual declaration describes the state at the end of the previous year (filed by 30 April)."""
    norm = label.casefold()
    if norm == "początek kadencji":
        return AssetDeclaration.Kind.START, filed.year
    if norm == "koniec kadencji":
        return AssetDeclaration.Kind.END, filed.year
    if _ANNUAL_RE.match(label):
        return AssetDeclaration.Kind.ANNUAL, filed.year - 1
    return None


def parse_declaration_table(fragment: str, mp_sejm_id: int, base_url: str = SEJM_SITE_URL) -> ParsedCard:
    """Pure parser of the "Lista oświadczeń majątkowych" table on an MP page."""
    result = ParsedCard()
    chosen: dict[tuple[int, str], tuple[date, str]] = {}
    for row in _ROW_RE.finditer(fragment):
        label = _text(row["label"])
        link = _LINK_RE.search(row["cell"])
        if not link:
            result.skipped.append(f"{label}: no link to the scan")
            continue
        try:
            filed = date.fromisoformat(_text(link["text"]))
        except ValueError:
            result.skipped.append(f"{label}: invalid filing date {_text(link['text'])!r}")
            continue
        url = urljoin(base_url, html.unescape(link["href"]))
        parsed_url = urlparse(url)
        file_match = _FILE_ID_RE.search(parsed_url.path)
        if parsed_url.scheme != "https" or parsed_url.hostname not in ALLOWED_PDF_HOSTS:
            result.skipped.append(f"{label}: unexpected URL {url}")
        elif not file_match or int(file_match[1]) != mp_sejm_id:
            result.skipped.append(f"{label}: file does not belong to MP {mp_sejm_id} ({url})")
        elif (kind_year := _kind_and_year(label, filed)) is None:
            result.skipped.append(f"{label}: unrecognised declaration kind")
        else:
            kind, year = kind_year
            previous = chosen.get((year, kind))
            if previous is not None:
                result.skipped.append(f"{label}: duplicate for {year} ({kind}), keeping the later filing")
            if previous is None or filed > previous[0]:
                chosen[(year, kind)] = (filed, url)
    result.refs = [
        DeclarationRef(mp_sejm_id=mp_sejm_id, year=year, kind=kind, url=url)
        for (year, kind), (_, url) in sorted(chosen.items(), key=lambda item: (item[1][0], item[0]))
    ]
    return result


class SejmSiteClient:
    """Fetches the declaration list from an MP page on sejm.gov.pl, pausing between requests."""

    def __init__(self, term: int | None = None, session=None, delay: float | None = None):
        self.term = term or settings.SEJM_TERM
        self.session = session or new_session()
        self.delay = settings.DECLARATION_FETCH_DELAY if delay is None else delay
        self._last_request = 0.0

    def pause(self):
        wait = self._last_request + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _request(self, method: str, url: str, **kwargs):
        self.pause()
        try:
            resp = getattr(self.session, method)(url, timeout=30, **kwargs)
        except curl_requests.exceptions.RequestException as exc:
            raise DeclarationSourceError(f"Failed to fetch {url}: {exc}") from exc
        if resp.status_code != 200:
            raise DeclarationSourceError(f"{url} returned HTTP {resp.status_code}")
        return resp

    def card_url(self, mp_sejm_id: int) -> str:
        return f"{SEJM_SITE_URL}/sejm{self.term}.nsf/posel.xsp?id={mp_sejm_id:03d}&type=A"

    def declarations(self, mp_sejm_id: int) -> ParsedCard:
        url = self.card_url(mp_sejm_id)
        page = self._request("get", url).text
        view_id = re.search(r'name="\$\$viewid"[^>]*value="([^"]+)"', page)
        event_id = re.search(r"eventId = '([^']+)'", page)
        holder = re.search(r"poselLoadContent\(e, 'Majatek', '([^']+)'", page)
        if not (view_id and event_id and holder):
            raise DeclarationSourceError(
                f"MP {mp_sejm_id} page has no declarations section "
                "(the layout changed, or an anti-bot challenge was returned)"
            )
        form = {
            "$$viewid": view_id[1],
            "$$xspexecid": "",
            "$$xspsubmitid": event_id[1],
            "$$xspsubmitvalue": "Majatek",
            "$$xspsubmitscroll": "0|0",
            "view:_id1": "view:_id1",
            "$$ajaxid": holder[1],
        }
        fragment = self._request(
            "post", url, data=form, headers={"X-Requested-With": "XMLHttpRequest", "Referer": url}
        ).text
        if holder[1] not in fragment:
            raise DeclarationSourceError(f"MP {mp_sejm_id}: response has no declarations section")
        return parse_declaration_table(fragment, mp_sejm_id)
