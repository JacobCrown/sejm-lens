"""Aggregates computed deterministically in the database: club results, cohesion, loyalty, attendance."""

from collections import Counter, defaultdict

from django.db import connection, transaction
from django.utils import timezone

from ..constants import INDEPENDENT_CODES
from ..models import MP, Club, ClubVotingStats, Term, Vote, Voting

PRESENT = ("YES", "NO", "ABSTAIN")


def agreement_index(yes: int, no: int, abstain: int) -> float | None:
    """Hix-Noury-Roland agreement index: 1 means the whole club voted the same way."""
    total = yes + no + abstain
    if not total:
        return None
    top = max(yes, no, abstain)
    return (top - 0.5 * (total - top)) / total


def majority_choice(yes: int, no: int, abstain: int) -> str:
    counts = {"YES": yes, "NO": no, "ABSTAIN": abstain}
    top = max(counts.values())
    if not top:
        return ""
    leaders = [k for k, v in counts.items() if v == top]
    return leaders[0] if len(leaders) == 1 else ""


def compute_club_stats(voting: Voting) -> list[ClubVotingStats]:
    if voting.is_list_vote:
        ClubVotingStats.objects.filter(voting=voting).delete()
        return []
    per_club: dict[int, Counter] = defaultdict(Counter)
    for club_id, choice in voting.votes.exclude(club=None).values_list("club_id", "choice"):
        per_club[club_id][choice] += 1
    rows = []
    for club_id, c in per_club.items():
        y, n, a = c["YES"], c["NO"], c["ABSTAIN"]
        rows.append(
            ClubVotingStats(
                voting=voting,
                club_id=club_id,
                yes=y,
                no=n,
                abstain=a,
                absent=c["ABSENT"],
                present=c["PRESENT"],
                majority_choice=majority_choice(y, n, a),
                cohesion=agreement_index(y, n, a),
            )
        )
    with transaction.atomic():
        ClubVotingStats.objects.filter(voting=voting).delete()
        ClubVotingStats.objects.bulk_create(rows)
    return rows


_MP_STATS_SQL = """
SELECT v.mp_id,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE v.choice IN ('YES', 'NO', 'ABSTAIN', 'PRESENT')) AS present,
       COUNT(*) FILTER (
           WHERE v.choice IN ('YES', 'NO', 'ABSTAIN')
             AND s.majority_choice <> '' AND NOT (c.code = ANY(%(independent)s))
       ) AS loyalty_base,
       COUNT(*) FILTER (
           WHERE v.choice = s.majority_choice AND NOT (c.code = ANY(%(independent)s))
       ) AS loyalty_hits
FROM parliament_vote v
JOIN parliament_voting g ON g.id = v.voting_id
LEFT JOIN parliament_club c ON c.id = v.club_id
LEFT JOIN parliament_clubvotingstats s ON s.voting_id = v.voting_id AND s.club_id = v.club_id
WHERE g.term_id = %(term)s AND g.kind <> 'ON_LIST'
GROUP BY v.mp_id
"""


def recompute_mp_stats(term: Term) -> int:
    with connection.cursor() as cur:
        cur.execute(_MP_STATS_SQL, {"term": term.number, "independent": list(INDEPENDENT_CODES)})
        rows = {mp_id: (total, present, base, hits) for mp_id, total, present, base, hits in cur.fetchall()}
    now = timezone.now()
    mps = list(MP.objects.filter(term=term))
    for mp in mps:
        total, present, base, hits = rows.get(mp.id, (0, 0, 0, 0))
        mp.votings_total, mp.votings_present = total, present
        mp.loyalty_base, mp.loyalty_hits = base, hits
        mp.stats_updated_at = now
    MP.objects.bulk_update(
        mps,
        ["votings_total", "votings_present", "loyalty_base", "loyalty_hits", "stats_updated_at"],
        batch_size=500,
    )
    return len(mps)


def club_cohesion(term: Term) -> dict[int, dict]:
    """Average cohesion and number of votings for each club."""
    sql = """
        SELECT s.club_id, AVG(s.cohesion), COUNT(*)
        FROM parliament_clubvotingstats s
        JOIN parliament_voting g ON g.id = s.voting_id
        WHERE g.term_id = %s AND s.cohesion IS NOT NULL
        GROUP BY s.club_id
    """
    with connection.cursor() as cur:
        cur.execute(sql, [term.number])
        return {cid: {"cohesion": avg, "votings": n} for cid, avg, n in cur.fetchall()}


def club_agreement(club: Club) -> list[dict]:
    """Share of votings where this club's majority matched another club's majority."""
    sql = """
        SELECT o.club_id, COUNT(*) AS total,
               COUNT(*) FILTER (WHERE o.majority_choice = s.majority_choice) AS agree
        FROM parliament_clubvotingstats s
        JOIN parliament_clubvotingstats o ON o.voting_id = s.voting_id AND o.club_id <> s.club_id
        WHERE s.club_id = %s AND s.majority_choice <> '' AND o.majority_choice <> ''
        GROUP BY o.club_id
    """
    with connection.cursor() as cur:
        cur.execute(sql, [club.id])
        rows = cur.fetchall()
    clubs = Club.objects.in_bulk([r[0] for r in rows])
    result = [
        {"club": clubs[cid], "total": total, "agree": agree, "pct": 100 * agree / total}
        for cid, total, agree in rows
        if total and not clubs[cid].is_independent
    ]
    return sorted(result, key=lambda r: -r["pct"])


def rebels(voting: Voting) -> list[Vote]:
    """MPs who voted differently from their club's majority."""
    majority = {
        s.club_id: s.majority_choice
        for s in voting.club_stats.all()
        if s.majority_choice and s.club.code not in INDEPENDENT_CODES
    }
    votes = voting.votes.filter(choice__in=PRESENT, club_id__in=majority.keys()).select_related("mp", "club")
    out = [v for v in votes if v.choice != majority[v.club_id]]
    for v in out:
        v.club_majority = majority[v.club_id]
    return sorted(out, key=lambda v: (v.club.code, v.mp.last_name))
