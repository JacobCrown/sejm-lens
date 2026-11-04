import pytest
from django.core.management import call_command
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_empty_database_pages(client):
    for name in ["home", "voting_list", "mp_list", "club_list"]:
        resp = client.get(reverse(name))
        assert resp.status_code == 200
    assert "sync_sejm" in client.get(reverse("home")).content.decode()


def test_pages_with_data(client, synced):
    call_command("seed_demo_declarations", "--years", "2023", "2024")
    pages = [
        reverse("home"),
        reverse("voting_list") + "?q=podatku&result=passed",
        reverse("voting_detail", args=[5, 1]),
        reverse("voting_detail", args=[5, 2]),
        reverse("mp_list") + "?club=KO",
        reverse("mp_detail", args=[1]),
        reverse("club_list"),
        reverse("club_detail", args=["KO"]),
        reverse("club_detail", args=["niez."]),
    ]
    for url in pages:
        assert client.get(url).status_code == 200, url

    detail = client.get(reverse("mp_detail", args=[1])).content.decode()
    assert "Majątek netto" in detail and "Dane demonstracyjne" in detail

    voting = client.get(reverse("voting_detail", args=[5, 2])).content.decode()
    assert "Zieliński" in voting


def test_htmx_partials(client, synced):
    headers = {"HX-Request": "true"}
    resp = client.get(reverse("voting_list") + "?result=rejected", headers=headers)
    assert resp.status_code == 200
    assert "<html" not in resp.content.decode()
    assert "poprawka 1" in resp.content.decode()

    resp = client.get(reverse("mp_votes", args=[4]) + "?filter=against", headers=headers)
    body = resp.content.decode()
    assert "wbrew klubowi" in body and "poprawka 1" in body


def test_404(client, synced):
    assert client.get(reverse("mp_detail", args=[999])).status_code == 404
    assert client.get(reverse("voting_detail", args=[5, 99])).status_code == 404
