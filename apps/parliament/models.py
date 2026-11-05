from django.conf import settings
from django.db import models
from django.urls import reverse

from .constants import INDEPENDENT_CODES, club_color
from .portraits import local_url, remote_url


class Term(models.Model):
    number = models.PositiveSmallIntegerField(primary_key=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)

    class Meta:
        verbose_name = "term"
        verbose_name_plural = "terms"

    def __str__(self):
        return f"term {self.number}"


class Club(models.Model):
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="clubs")
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    email = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=64, blank=True)
    members_count = models.PositiveSmallIntegerField(default=0)
    is_current = models.BooleanField(default=True)

    class Meta:
        verbose_name = "club"
        verbose_name_plural = "clubs"
        ordering = ["-members_count", "code"]
        constraints = [models.UniqueConstraint(fields=["term", "code"], name="uniq_club_code")]

    def __str__(self):
        return self.code

    @property
    def color(self) -> str:
        return club_color(self.code)

    @property
    def is_independent(self) -> bool:
        return self.code in INDEPENDENT_CODES

    @property
    def logo_url(self) -> str:
        return f"{settings.SEJM_API_URL}/term{self.term_id}/clubs/{self.code}/logo"

    def get_absolute_url(self):
        return reverse("club_detail", args=[self.code])


class MP(models.Model):
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="mps")
    sejm_id = models.PositiveIntegerField()
    first_name = models.CharField(max_length=100)
    second_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100)
    genitive_name = models.CharField(max_length=200, blank=True)
    club = models.ForeignKey(Club, null=True, blank=True, on_delete=models.SET_NULL, related_name="members")
    district_num = models.PositiveSmallIntegerField(null=True, blank=True)
    district_name = models.CharField(max_length=100, blank=True)
    voivodeship = models.CharField(max_length=64, blank=True)
    profession = models.CharField(max_length=255, blank=True)
    education_level = models.CharField(max_length=64, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    birth_location = models.CharField(max_length=128, blank=True)
    email = models.CharField(max_length=255, blank=True)
    election_votes = models.PositiveIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    inactive_cause = models.CharField(max_length=128, blank=True)
    mandate_expiry_date = models.DateField(null=True, blank=True)

    # Indicators recomputed by apps.parliament.services.stats.recompute_mp_stats
    votings_total = models.PositiveIntegerField(default=0)
    votings_present = models.PositiveIntegerField(default=0)
    loyalty_base = models.PositiveIntegerField(default=0)
    loyalty_hits = models.PositiveIntegerField(default=0)
    stats_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "MP"
        verbose_name_plural = "MPs"
        ordering = ["last_name", "first_name"]
        constraints = [models.UniqueConstraint(fields=["term", "sejm_id"], name="uniq_mp")]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def photo_url(self) -> str:
        return local_url(self.term_id, self.sejm_id, "full") or remote_url(self.term_id, self.sejm_id, "full")

    @property
    def photo_mini_url(self) -> str:
        return local_url(self.term_id, self.sejm_id, "mini") or remote_url(self.term_id, self.sejm_id, "mini")

    @property
    def attendance_pct(self) -> float | None:
        if not self.votings_total:
            return None
        return 100 * self.votings_present / self.votings_total

    @property
    def loyalty_pct(self) -> float | None:
        if not self.loyalty_base:
            return None
        return 100 * self.loyalty_hits / self.loyalty_base

    def get_absolute_url(self):
        return reverse("mp_detail", args=[self.sejm_id])


class Sitting(models.Model):
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="sittings")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=255, blank=True)
    dates = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "sitting"
        verbose_name_plural = "sittings"
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["term", "number"], name="uniq_sitting")]

    def __str__(self):
        return f"Sitting {self.number}"


class Voting(models.Model):
    class Kind(models.TextChoices):
        ELECTRONIC = "ELECTRONIC", "Elektroniczne"
        TRADITIONAL = "TRADITIONAL", "Tradycyjne"
        ON_LIST = "ON_LIST", "Na listę kandydatów"

    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="votings")
    sitting = models.ForeignKey(Sitting, on_delete=models.CASCADE, related_name="votings")
    sitting_day = models.PositiveSmallIntegerField(null=True, blank=True)
    number = models.PositiveIntegerField()
    date = models.DateTimeField()
    title = models.TextField()
    topic = models.TextField(blank=True)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.ELECTRONIC)
    majority_type = models.CharField(max_length=32, blank=True)
    majority_votes = models.PositiveIntegerField(null=True, blank=True)
    yes = models.PositiveIntegerField(default=0)
    no = models.PositiveIntegerField(default=0)
    abstain = models.PositiveIntegerField(default=0)
    not_participating = models.PositiveIntegerField(default=0)
    present = models.PositiveIntegerField(default=0)
    total_voted = models.PositiveIntegerField(default=0)
    options = models.JSONField(default=list, blank=True)
    pdf_url = models.URLField(max_length=500, blank=True)
    source_hash = models.CharField(max_length=64, blank=True)
    votes_loaded = models.BooleanField(default=False)

    class Meta:
        verbose_name = "voting"
        verbose_name_plural = "votings"
        ordering = ["-date", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["term", "sitting", "number"], name="uniq_voting"),
        ]
        indexes = [models.Index(fields=["-date"])]

    def __str__(self):
        return f"{self.sitting.number}/{self.number}: {self.title[:60]}"

    @property
    def is_list_vote(self) -> bool:
        return self.kind == self.Kind.ON_LIST

    @property
    def passed(self) -> bool | None:
        if self.is_list_vote:
            return None
        if self.majority_type == "SIMPLE_MAJORITY" or not self.majority_votes:
            return self.yes > self.no
        return self.yes >= self.majority_votes

    @property
    def divisiveness(self) -> float:
        """1.0 = a perfect tie between yes and no, 0.0 = unanimous."""
        decisive = self.yes + self.no
        if not decisive:
            return 0.0
        return 1 - abs(self.yes - self.no) / decisive

    @property
    def display_title(self) -> str:
        return self.topic or self.description or self.title

    def get_absolute_url(self):
        return reverse("voting_detail", args=[self.sitting.number, self.number])


class Vote(models.Model):
    class Choice(models.TextChoices):
        YES = "YES", "Za"
        NO = "NO", "Przeciw"
        ABSTAIN = "ABSTAIN", "Wstrzymał się"
        ABSENT = "ABSENT", "Nieobecny"
        PRESENT = "PRESENT", "Obecny, nie głosował"
        VOTE_VALID = "VOTE_VALID", "Głos na liście"
        VOTE_INVALID = "VOTE_INVALID", "Głos nieważny"

    PRESENT_CHOICES = (Choice.YES, Choice.NO, Choice.ABSTAIN)

    voting = models.ForeignKey(Voting, on_delete=models.CASCADE, related_name="votes")
    mp = models.ForeignKey(MP, on_delete=models.CASCADE, related_name="votes")
    club = models.ForeignKey(Club, null=True, blank=True, on_delete=models.SET_NULL, related_name="votes")
    choice = models.CharField(max_length=16, choices=Choice.choices)
    list_votes = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "vote"
        verbose_name_plural = "votes"
        constraints = [models.UniqueConstraint(fields=["voting", "mp"], name="uniq_vote")]
        indexes = [models.Index(fields=["mp", "voting"])]


class ClubVotingStats(models.Model):
    voting = models.ForeignKey(Voting, on_delete=models.CASCADE, related_name="club_stats")
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="voting_stats")
    yes = models.PositiveIntegerField(default=0)
    no = models.PositiveIntegerField(default=0)
    abstain = models.PositiveIntegerField(default=0)
    absent = models.PositiveIntegerField(default=0)
    present = models.PositiveIntegerField(default=0, help_text="Present members who did not cast a vote")
    majority_choice = models.CharField(max_length=16, blank=True)
    cohesion = models.FloatField(null=True, blank=True)

    class Meta:
        verbose_name = "club result in a voting"
        verbose_name_plural = "club results in votings"
        constraints = [models.UniqueConstraint(fields=["voting", "club"], name="uniq_club_voting")]

    @property
    def total(self) -> int:
        return self.yes + self.no + self.abstain + self.absent + self.present

    @property
    def not_voted(self) -> int:
        return self.absent + self.present

    def _pct(self, n: int) -> float:
        return 100 * n / self.total if self.total else 0.0

    @property
    def yes_pct(self):
        return self._pct(self.yes)

    @property
    def no_pct(self):
        return self._pct(self.no)

    @property
    def abstain_pct(self):
        return self._pct(self.abstain)

    @property
    def not_voted_pct(self):
        return self._pct(self.not_voted)
