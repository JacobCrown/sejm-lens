from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.db.models.functions import Abs
from django.shortcuts import get_object_or_404, render

from apps.assets.services import summary as wealth

from .models import MP, Club, ClubVotingStats, Sitting, Term, Vote, Voting
from .services import stats
from .services.hemicycle import hemicycle_data


def _term() -> Term | None:
    return Term.objects.filter(number=settings.SEJM_TERM).first()


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def home(request):
    term = _term()
    votings = Voting.objects.filter(term_id=settings.SEJM_TERM).select_related("sitting")
    recent = list(votings.exclude(kind=Voting.Kind.ON_LIST)[:6])
    divisive = list(
        votings.exclude(kind=Voting.Kind.ON_LIST)
        .annotate(margin=Abs(F("yes") - F("no")), decisive=F("yes") + F("no"))
        .filter(decisive__gte=300)
        .order_by("margin", "-date")[:5]
    )
    cohesion = []
    if term:
        by_club = stats.club_cohesion(term)
        clubs = Club.objects.in_bulk(by_club.keys())
        cohesion = sorted(
            (
                {"club": clubs[cid], **d}
                for cid, d in by_club.items()
                if clubs[cid].is_current and not clubs[cid].is_independent
            ),
            key=lambda r: -r["cohesion"],
        )
    return render(request, "home.html", {
        "term": term,
        "recent": recent,
        "divisive": divisive,
        "cohesion": cohesion,
        "counts": {
            "votings": votings.count(),
            "mps": MP.objects.filter(term_id=settings.SEJM_TERM, active=True).count(),
            "clubs": Club.objects.filter(term_id=settings.SEJM_TERM, is_current=True).count(),
        },
    })


def voting_list(request):
    qs = Voting.objects.filter(term_id=settings.SEJM_TERM).select_related("sitting")
    q = request.GET.get("q", "").strip()
    sitting = request.GET.get("sitting", "")
    result = request.GET.get("result", "")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(topic__icontains=q) | Q(description__icontains=q))
    if sitting.isdigit():
        qs = qs.filter(sitting__number=int(sitting))
    if result in ("passed", "rejected"):
        simple = Q(majority_type="SIMPLE_MAJORITY") | Q(majority_votes__isnull=True) | Q(majority_votes=0)
        passed = (simple & Q(yes__gt=F("no"))) | (~simple & Q(yes__gte=F("majority_votes")))
        qs = qs.exclude(kind=Voting.Kind.ON_LIST)
        qs = qs.filter(passed) if result == "passed" else qs.exclude(passed)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    ctx = {
        "page": page,
        "q": q,
        "sitting": sitting,
        "result": result,
        "sittings": Sitting.objects.filter(term_id=settings.SEJM_TERM, votings__isnull=False).distinct(),
        "total": page.paginator.count,
    }
    template = "votings/_results.html" if _is_htmx(request) else "votings/list.html"
    return render(request, template, ctx)


def voting_detail(request, sitting: int, number: int):
    voting = get_object_or_404(
        Voting.objects.select_related("sitting"),
        term_id=settings.SEJM_TERM, sitting__number=sitting, number=number,
    )
    votes = list(voting.votes.select_related("mp", "club"))
    club_rows = sorted(
        voting.club_stats.select_related("club"),
        key=lambda s: (-s.total, s.club.code),
    )
    neighbours = Voting.objects.filter(term_id=settings.SEJM_TERM, sitting=voting.sitting)
    return render(request, "votings/detail.html", {
        "voting": voting,
        "club_rows": club_rows,
        "rebels": stats.rebels(voting),
        "hemicycle": hemicycle_data(votes) if votes else [],
        "prev": neighbours.filter(number__lt=number).order_by("-number").first(),
        "next": neighbours.filter(number__gt=number).order_by("number").first(),
    })


def mp_list(request):
    qs = MP.objects.filter(term_id=settings.SEJM_TERM).select_related("club")
    q = request.GET.get("q", "").strip()
    club = request.GET.get("club", "")
    district = request.GET.get("district", "")
    show_former = request.GET.get("former") == "1"
    if not show_former:
        qs = qs.filter(active=True)
    if q:
        qs = qs.filter(Q(last_name__icontains=q) | Q(first_name__icontains=q) | Q(profession__icontains=q))
    if club:
        qs = qs.filter(club__code=club)
    if district.isdigit():
        qs = qs.filter(district_num=int(district))
    page = Paginator(qs, 48).get_page(request.GET.get("page"))
    districts = (
        MP.objects.filter(term_id=settings.SEJM_TERM, district_num__isnull=False)
        .values_list("district_num", "district_name").distinct().order_by("district_num")
    )
    ctx = {
        "page": page,
        "q": q,
        "club": club,
        "district": district,
        "show_former": show_former,
        "clubs": Club.objects.filter(term_id=settings.SEJM_TERM, is_current=True),
        "districts": districts,
    }
    template = "mps/_results.html" if _is_htmx(request) else "mps/list.html"
    return render(request, template, ctx)


def _mp_votes_qs(mp: MP, flt: str):
    majority = ClubVotingStats.objects.filter(voting=OuterRef("voting"), club=OuterRef("club"))
    qs = (
        mp.votes.select_related("voting", "voting__sitting", "club")
        .annotate(club_majority=Subquery(majority.values("majority_choice")[:1]))
        .order_by("-voting__date", "-voting__number")
    )
    if flt in ("YES", "NO", "ABSTAIN", "ABSENT"):
        qs = qs.filter(choice=flt)
    elif flt == "against":
        qs = (
            qs.filter(choice__in=Vote.PRESENT_CHOICES)
            .exclude(club__code="niez.")
            .exclude(club_majority="")
            .exclude(choice=F("club_majority"))
        )
    return qs


def mp_detail(request, sejm_id: int):
    mp = get_object_or_404(MP.objects.select_related("club"), term_id=settings.SEJM_TERM, sejm_id=sejm_id)
    series = wealth.timeline(mp)
    choices = dict(mp.votes.values_list("choice").annotate(n=Count("id")))
    return render(request, "mps/detail.html", {
        "mp": mp,
        "wealth": series,
        "latest": series[-1] if series else None,
        "insights": wealth.insights(series),
        "processing_cost": wealth.processing_cost(series),
        "wealth_chart": wealth.chart_series(series),
        "structures": {str(s.year): s.structure for s in series},
        "has_demo": any(s.declaration.is_demo for s in series),
        "needs_review": any(s.declaration.status == "NEEDS_REVIEW" for s in series),
        "pending_declarations": mp.declarations.filter(status__in=["PENDING", "FAILED"]).count(),
        "choices": choices,
        "against": max(0, mp.loyalty_base - mp.loyalty_hits),
        "votes_page": Paginator(_mp_votes_qs(mp, ""), 20).get_page(1),
        "flt": "",
    })


def mp_votes(request, sejm_id: int):
    mp = get_object_or_404(MP, term_id=settings.SEJM_TERM, sejm_id=sejm_id)
    flt = request.GET.get("filter", "")
    page = Paginator(_mp_votes_qs(mp, flt), 20).get_page(request.GET.get("page"))
    return render(request, "mps/_votes.html", {"mp": mp, "votes_page": page, "flt": flt})


def club_list(request):
    term = _term()
    by_club = stats.club_cohesion(term) if term else {}
    clubs = list(Club.objects.filter(term_id=settings.SEJM_TERM, is_current=True))
    for c in clubs:
        c.cohesion = by_club.get(c.id, {}).get("cohesion")
    return render(request, "clubs/list.html", {"clubs": clubs})


def club_detail(request, code: str):
    club = get_object_or_404(Club, term_id=settings.SEJM_TERM, code=code)
    members = Paginator(
        club.members.filter(active=True).order_by("last_name", "first_name", "pk"),
        20,
    ).get_page(request.GET.get("page"))
    recent = (
        club.voting_stats.select_related("voting", "voting__sitting")
        .order_by("-voting__date", "-voting__number")[:15]
    )
    term = _term()
    cohesion = stats.club_cohesion(term).get(club.id, {}) if term else {}
    ctx = {
        "club": club,
        "members": members,
        "member_count": members.paginator.count,
        "recent": recent,
        "agreement": stats.club_agreement(club),
        "cohesion": cohesion.get("cohesion"),
        "cohesion_votings": cohesion.get("votings", 0),
    }
    template = "clubs/_members.html" if _is_htmx(request) else "clubs/detail.html"
    return render(request, template, ctx)
