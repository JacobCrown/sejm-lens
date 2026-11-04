import copy
import io

import httpx
import pytest

from apps.assets.services import currency
from apps.parliament.sejm_api import SejmClient

TERM = {"num": 10, "from": "2023-11-13", "current": True}

CLUBS = [
    {"id": "KO", "name": "Klub Parlamentarny Koalicja Obywatelska", "membersCount": 2},
    {"id": "PiS", "name": "Klub Parlamentarny Prawo i Sprawiedliwość", "membersCount": 2},
    {"id": "niez.", "name": "Posłowie niezrzeszeni", "membersCount": 1},
]

MPS = [
    {"id": 1, "firstName": "Anna", "lastName": "Nowak", "club": "KO", "districtNum": 19,
     "districtName": "Warszawa", "active": True},
    {"id": 2, "firstName": "Jan", "lastName": "Kowalski", "club": "KO", "districtNum": 19,
     "districtName": "Warszawa", "active": True},
    {"id": 3, "firstName": "Ewa", "lastName": "Wiśniewska", "club": "PiS", "districtNum": 13,
     "districtName": "Kraków", "active": True},
    {"id": 4, "firstName": "Piotr", "lastName": "Zieliński", "club": "PiS", "districtNum": 13,
     "districtName": "Kraków", "active": True},
    {"id": 6, "firstName": "Tomasz", "lastName": "Lewandowski", "club": "PiS", "districtNum": 13,
     "districtName": "Kraków", "active": True},
    {"id": 5, "firstName": "Marek", "lastName": "Wójcik", "club": "niez.", "districtNum": 1,
     "districtName": "Legnica", "active": True},
]


def _vote(mp_id, club, vote):
    mp = next(m for m in MPS if m["id"] == mp_id)
    return {"MP": mp_id, "club": club, "firstName": mp["firstName"], "lastName": mp["lastName"], "vote": vote}


VOTINGS = {
    1: {
        "sitting": 5, "votingNumber": 1, "date": "2026-10-22T10:00:00", "kind": "ELECTRONIC",
        "title": "Pkt. 3 Projekt ustawy o podatku (druk nr 100)", "topic": "głosowanie nad całością projektu",
        "majorityType": "SIMPLE_MAJORITY", "majorityVotes": 1,
        "yes": 4, "no": 1, "abstain": 0, "notParticipating": 1, "present": 0, "totalVoted": 5,
        "links": [{"href": "https://example.org/1.pdf", "rel": "pdf"}],
        "votes": [
            _vote(1, "KO", "YES"), _vote(2, "KO", "YES"),
            _vote(3, "PiS", "YES"), _vote(4, "PiS", "NO"), _vote(6, "PiS", "YES"),
            _vote(5, "niez.", "ABSENT"),
        ],
    },
    2: {
        "sitting": 5, "votingNumber": 2, "date": "2026-10-22T10:05:00", "kind": "ELECTRONIC",
        "title": "Pkt. 3 Projekt ustawy o podatku (druk nr 100)", "topic": "poprawka 1",
        "majorityType": "SIMPLE_MAJORITY", "majorityVotes": 1,
        "yes": 2, "no": 3, "abstain": 1, "notParticipating": 0, "present": 0, "totalVoted": 6,
        "links": [],
        "votes": [
            _vote(1, "KO", "NO"), _vote(2, "KO", "NO"),
            _vote(3, "PiS", "YES"), _vote(4, "PiS", "ABSTAIN"), _vote(6, "PiS", "YES"),
            _vote(5, "niez.", "NO"),
        ],
    },
}


def summary(v):
    return {k: val for k, val in v.items() if k != "votes"}


class FakeSejmAPI:
    def __init__(self):
        self.calls: list[str] = []
        self.votings = copy.deepcopy(VOTINGS)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/sejm/term10").rstrip("/")
        self.calls.append(path)
        routes = {
            "": TERM,
            "/clubs": CLUBS,
            "/MP": MPS,
            "/proceedings": [{"number": 5, "title": "5. Posiedzenie", "dates": ["2026-10-22"]},
                             {"number": 0, "title": "Planowane"}],
            "/votings": [{"date": "2026-10-22", "proceeding": 5, "votingsNum": 2}],
            "/votings/5": [summary(v) for v in self.votings.values()],
        }
        if path in routes:
            return httpx.Response(200, json=routes[path])
        if path.startswith("/votings/5/"):
            num = int(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=self.votings[num])
        return httpx.Response(404, json={})


@pytest.fixture
def fake_api():
    return FakeSejmAPI()


@pytest.fixture
def client_api(fake_api, settings):
    settings.SEJM_API_URL = "https://api.test/sejm"
    return SejmClient(term=10, transport=httpx.MockTransport(fake_api.handler), retries=1)


@pytest.fixture
def synced(db, client_api):
    from apps.parliament.services import stats, sync

    term = sync.sync_term(client_api)
    sync.sync_clubs(client_api, term)
    sync.sync_mps(client_api, term)
    sync.sync_sittings(client_api, term)
    sync.sync_votings(client_api, term, [5], workers=2)
    stats.recompute_mp_stats(term)
    return term


@pytest.fixture(autouse=True)
def offline_nbp(monkeypatch):
    """Tests do not call NBP: rates come from the fallback table."""
    currency._nbp_rate.cache_clear()
    monkeypatch.setattr(currency, "_nbp_rate", lambda code, on: None)


@pytest.fixture
def media_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def make_pdf(text: str = "Oświadczenie majątkowe") -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(img).text((40, 40), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()
