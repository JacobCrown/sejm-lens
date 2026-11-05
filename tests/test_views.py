import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.parliament.models import MP, Club

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


def test_club_members_are_paginated(client, synced):
    club = Club.objects.get(term=synced, code="KO")
    MP.objects.bulk_create([
        MP(term=synced, sejm_id=1000 + i, first_name="Poseł", last_name=f"A{i:02d}", club=club)
        for i in range(25)
    ])

    page1 = client.get(reverse("club_detail", args=["KO"]))
    body = page1.content.decode()
    assert page1.status_code == 200
    assert body.count("zgodność z klubem") == 20
    assert "Członkowie (27)" in body
    assert "Poseł A00" in body and "Poseł A19" in body
    assert "Jan Kowalski" not in body
    assert "Strona 1 z 2" in body

    page2 = client.get(reverse("club_detail", args=["KO"]) + "?page=2")
    body2 = page2.content.decode()
    assert body2.count("zgodność z klubem") == 7
    assert "Jan Kowalski" in body2 and "Anna Nowak" in body2 and "Poseł A20" in body2
    assert "Poseł A00" not in body2
    assert "Członkowie (27)" in body2
    assert "Strona 2 z 2" in body2

    partial = client.get(reverse("club_detail", args=["KO"]) + "?page=2", headers={"HX-Request": "true"})
    fragment = partial.content.decode()
    assert partial.status_code == 200
    assert "<html" not in fragment
    assert "Jan Kowalski" in fragment
    assert "Klub Parlamentarny" not in fragment


def test_404(client, synced):
    assert client.get(reverse("mp_detail", args=[999])).status_code == 404
    assert client.get(reverse("voting_detail", args=[5, 99])).status_code == 404
