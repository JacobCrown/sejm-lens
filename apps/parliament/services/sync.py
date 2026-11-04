"""Synchronizacja danych z API Sejmu do bazy: surowa kopia, potem upsert do modeli domenowych."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction

from apps.core.raw import sha256_json, store_json

from ..models import MP, Club, Sitting, Term, Vote, Voting
from ..sejm_api import SejmClient
from .stats import compute_club_stats

logger = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=WARSAW)


def sync_term(client: SejmClient) -> Term:
    info = client.term_info()
    store_json("sejm_term", str(client.term), info)
    term, _ = Term.objects.update_or_create(
        number=info["num"],
        defaults={
            "start_date": _date(info.get("from")),
            "end_date": _date(info.get("to")),
            "is_current": bool(info.get("current")),
        },
    )
    return term


def sync_clubs(client: SejmClient, term: Term) -> int:
    payload = client.clubs()
    store_json("sejm_clubs", str(term.number), payload)
    codes = []
    for c in payload:
        codes.append(c["id"])
        Club.objects.update_or_create(
            term=term,
            code=c["id"],
            defaults={
                "name": c.get("name", c["id"]),
                "email": c.get("email", "") or "",
                "phone": c.get("phone", "") or "",
                "members_count": c.get("membersCount", 0) or 0,
                "is_current": True,
            },
        )
    Club.objects.filter(term=term).exclude(code__in=codes).update(is_current=False, members_count=0)
    return len(payload)


def _club(term: Term, code: str | None, cache: dict[str, Club]) -> Club | None:
    if not code:
        return None
    if code not in cache:
        cache[code], _ = Club.objects.get_or_create(
            term=term, code=code, defaults={"name": code, "is_current": False}
        )
    return cache[code]


def sync_mps(client: SejmClient, term: Term) -> int:
    payload = client.mps()
    store_json("sejm_mps", str(term.number), payload)
    clubs = {c.code: c for c in Club.objects.filter(term=term)}
    for m in payload:
        MP.objects.update_or_create(
            term=term,
            sejm_id=m["id"],
            defaults={
                "first_name": m.get("firstName", ""),
                "second_name": m.get("secondName", "") or "",
                "last_name": m.get("lastName", ""),
                "genitive_name": m.get("genitiveName", "") or "",
                "club": _club(term, m.get("club"), clubs),
                "district_num": m.get("districtNum"),
                "district_name": m.get("districtName", "") or "",
                "voivodeship": m.get("voivodeship", "") or "",
                "profession": m.get("profession", "") or "",
                "education_level": m.get("educationLevel", "") or "",
                "birth_date": _date(m.get("birthDate")),
                "birth_location": m.get("birthLocation", "") or "",
                "email": m.get("email", "") or "",
                "election_votes": m.get("numberOfVotes"),
                "active": bool(m.get("active", True)),
                "inactive_cause": m.get("inactiveCause", "") or "",
                "mandate_expiry_date": _date(m.get("mandateExpiryDate")),
            },
        )
    return len(payload)


def sync_sittings(client: SejmClient, term: Term) -> int:
    payload = client.proceedings()
    store_json("sejm_proceedings", str(term.number), payload)
    count = 0
    for p in payload:
        if not p.get("number"):
            continue
        Sitting.objects.update_or_create(
            term=term,
            number=p["number"],
            defaults={"title": p.get("title", "")[:255], "dates": p.get("dates", [])},
        )
        count += 1
    return count


def sittings_with_votings(client: SejmClient) -> list[int]:
    return sorted({d["proceeding"] for d in client.votings_index()})


def upsert_voting(
    term: Term,
    payload: dict,
    clubs: dict[str, Club],
    mps: dict[int, MP],
    source_hash: str = "",
) -> Voting:
    sitting, _ = Sitting.objects.get_or_create(term=term, number=payload["sitting"])
    pdf = next((link["href"] for link in payload.get("links", []) if link.get("rel") == "pdf"), "")
    with transaction.atomic():
        voting, _ = Voting.objects.update_or_create(
            term=term,
            sitting=sitting,
            number=payload["votingNumber"],
            defaults={
                "sitting_day": payload.get("sittingDay"),
                "date": _datetime(payload["date"]),
                "title": payload.get("title", ""),
                "topic": payload.get("topic", "") or "",
                "description": payload.get("description", "") or "",
                "kind": payload.get("kind", Voting.Kind.ELECTRONIC),
                "majority_type": payload.get("majorityType", "") or "",
                "majority_votes": payload.get("majorityVotes"),
                "yes": payload.get("yes", 0) or 0,
                "no": payload.get("no", 0) or 0,
                "abstain": payload.get("abstain", 0) or 0,
                "not_participating": payload.get("notParticipating", 0) or 0,
                "present": payload.get("present", 0) or 0,
                "total_voted": payload.get("totalVoted", 0) or 0,
                "options": payload.get("votingOptions", []) or [],
                "pdf_url": pdf,
                "source_hash": source_hash,
                "votes_loaded": "votes" in payload,
            },
        )
        if "votes" in payload:
            votes = []
            for v in payload["votes"]:
                mp = mps.get(v["MP"])
                if mp is None:
                    mp, _ = MP.objects.get_or_create(
                        term=term,
                        sejm_id=v["MP"],
                        defaults={
                            "first_name": v.get("firstName", ""),
                            "last_name": v.get("lastName", ""),
                            "active": False,
                        },
                    )
                    mps[mp.sejm_id] = mp
                votes.append(
                    Vote(
                        voting=voting,
                        mp=mp,
                        club=_club(term, v.get("club"), clubs),
                        choice=v.get("vote", ""),
                        list_votes=v.get("listVotes"),
                    )
                )
            Vote.objects.filter(voting=voting).delete()
            Vote.objects.bulk_create(votes, batch_size=1000)
            compute_club_stats(voting)
    return voting


def sync_votings(
    client: SejmClient,
    term: Term,
    sittings: list[int],
    force: bool = False,
    workers: int | None = None,
) -> dict:
    stats = {"sittings": 0, "votings_seen": 0, "votings_updated": 0, "votings_skipped": 0}
    clubs = {c.code: c for c in Club.objects.filter(term=term)}
    mps = {m.sejm_id: m for m in MP.objects.filter(term=term)}
    existing = {
        (s, n): (h, loaded)
        for s, n, h, loaded in Voting.objects.filter(term=term).values_list(
            "sitting__number", "number", "source_hash", "votes_loaded"
        )
    }
    workers = workers or settings.SEJM_HTTP_WORKERS

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sitting_no in sittings:
            summaries = client.sitting_votings(sitting_no)
            store_json("sejm_sitting_votings", f"{term.number}/{sitting_no}", summaries)
            stats["sittings"] += 1
            todo = {}
            for summary in summaries:
                stats["votings_seen"] += 1
                key = (summary["sitting"], summary["votingNumber"])
                digest = sha256_json(summary)
                prev = existing.get(key)
                if prev and prev[0] == digest and prev[1] and not force:
                    stats["votings_skipped"] += 1
                    continue
                todo[key] = digest

            futures = {pool.submit(client.voting, s, n): (s, n) for (s, n) in todo}
            for fut in as_completed(futures):
                s, n = futures[fut]
                full = fut.result()
                store_json("sejm_voting", f"{term.number}/{s}/{n}", full)
                upsert_voting(term, full, clubs, mps, source_hash=todo[(s, n)])
                stats["votings_updated"] += 1
            logger.info("Sitting %s: %s votings updated", sitting_no, len(todo))
    return stats
