import pytest

from apps.core.models import RawDocument
from apps.parliament.models import MP, Club, ClubVotingStats, Sitting, Vote, Voting
from apps.parliament.services import stats, sync

pytestmark = pytest.mark.django_db


def test_sync_creates_domain_objects(synced):
    assert Club.objects.count() == 3
    assert MP.objects.count() == 6
    assert Sitting.objects.filter(number=5).exists()
    assert not Sitting.objects.filter(number=0).exists()
    assert Voting.objects.count() == 2
    assert Vote.objects.count() == 12
    v = Voting.objects.get(number=1)
    assert v.pdf_url == "https://example.org/1.pdf"
    assert v.passed is True
    assert v.display_title == "głosowanie nad całością projektu"
    assert RawDocument.objects.filter(source="sejm_voting").count() == 2


def test_sync_is_idempotent(synced, client_api, fake_api):
    fake_api.calls.clear()
    result = sync.sync_votings(client_api, synced, [5], workers=1)
    assert result["votings_updated"] == 0
    assert result["votings_skipped"] == 2
    assert not any(c.startswith("/votings/5/") for c in fake_api.calls)
    assert Vote.objects.count() == 12


def test_changed_voting_is_refetched(synced, client_api, fake_api):
    fake_api.votings[2]["yes"] = 3
    fake_api.votings[2]["no"] = 2
    fake_api.votings[2]["votes"][1]["vote"] = "YES"
    result = sync.sync_votings(client_api, synced, [5], workers=1)
    assert result["votings_updated"] == 1
    assert Vote.objects.get(voting__number=2, mp__sejm_id=2).choice == "YES"


def test_club_stats_percentages(synced):
    v1 = Voting.objects.get(number=1)
    ko = ClubVotingStats.objects.get(voting=v1, club__code="KO")
    pis = ClubVotingStats.objects.get(voting=v1, club__code="PiS")
    assert (ko.yes, ko.no, ko.yes_pct) == (2, 0, 100.0)
    assert ko.majority_choice == "YES" and ko.cohesion == 1.0
    assert (pis.yes, pis.no) == (2, 1)
    assert pis.yes_pct == pytest.approx(66.67, abs=0.01)
    assert pis.majority_choice == "YES"
    assert pis.cohesion == pytest.approx(0.5)


def test_tie_has_no_majority():
    assert stats.majority_choice(2, 2, 0) == ""
    assert stats.majority_choice(0, 0, 0) == ""
    assert stats.majority_choice(1, 3, 2) == "NO"


def test_rebels(synced):
    v2 = Voting.objects.get(number=2)
    rebels = stats.rebels(v2)
    assert [r.mp.sejm_id for r in rebels] == [4]
    assert rebels[0].club_majority == "YES"


def test_mp_stats(synced):
    anna = MP.objects.get(sejm_id=1)
    assert anna.votings_total == 2
    assert anna.attendance_pct == 100.0
    assert anna.loyalty_pct == 100.0
    piotr = MP.objects.get(sejm_id=4)
    # In both votings the PiS majority was YES, while Piotr voted NO and then abstained.
    assert (piotr.loyalty_base, piotr.loyalty_hits) == (2, 0)
    assert MP.objects.get(sejm_id=3).loyalty_pct == 100.0
    marek = MP.objects.get(sejm_id=5)
    assert marek.attendance_pct == 50.0
    assert marek.loyalty_pct is None


def test_club_agreement(synced):
    ko = Club.objects.get(code="KO")
    rows = stats.club_agreement(ko)
    assert [(r["club"].code, r["total"], r["agree"]) for r in rows] == [("PiS", 2, 1)]


@pytest.mark.parametrize(
    "counts, expected",
    [((10, 0, 0), 1.0), ((5, 5, 0), 0.25), ((0, 0, 0), None), ((4, 4, 4), 0.0)],
)
def test_agreement_index(counts, expected):
    assert stats.agreement_index(*counts) == expected
