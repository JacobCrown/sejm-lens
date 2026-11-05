import httpx
import pytest

from apps.parliament.models import MP, Term
from apps.parliament.portraits import fetch_portraits, file_path, remote_url


def _jpeg_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/999001/photo") or request.url.path.endswith("/999001/photo-mini"):
        return httpx.Response(200, content=b"\xff\xd8\xff\xd9")
    return httpx.Response(404)


@pytest.mark.django_db
def test_fetch_portraits_saves_jpeg_and_mp_uses_local_url():
    term = Term.objects.create(number=10)
    mp = MP.objects.create(term=term, sejm_id=999001, first_name="Anna", last_name="Testowa")
    paths = [file_path(10, 999001, "mini"), file_path(10, 999001, "full")]
    for path in paths:
        path.unlink(missing_ok=True)
    client = httpx.Client(transport=httpx.MockTransport(_jpeg_handler))
    try:
        stats = fetch_portraits(10, [999001], client=client, workers=1)
        assert stats == {"saved": 2, "skipped": 0, "failed": 0}
        assert mp.photo_mini_url.startswith("/static/") and mp.photo_mini_url.endswith("999001-mini.jpg")
        assert mp.photo_url.startswith("/static/") and mp.photo_url.endswith("999001.jpg")
        assert "api.test" not in mp.photo_mini_url
        again = fetch_portraits(10, [999001], client=client, workers=1)
        assert again["saved"] == 0 and again["skipped"] == 2
    finally:
        client.close()
        for path in paths:
            path.unlink(missing_ok=True)


@pytest.mark.django_db
def test_photo_url_falls_back_to_sejm_when_file_missing(settings):
    settings.SEJM_API_URL = "https://api.test/sejm"
    term = Term.objects.create(number=10)
    mp = MP.objects.create(term=term, sejm_id=999002, first_name="Jan", last_name="Brak")
    assert mp.photo_mini_url == remote_url(10, 999002, "mini")
    assert mp.photo_mini_url == "https://api.test/sejm/term10/MP/999002/photo-mini"
