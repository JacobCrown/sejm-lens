from django.contrib import admin

from .models import MP, Club, ClubVotingStats, Sitting, Term, Voting


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ["number", "start_date", "end_date", "is_current"]


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "members_count", "is_current"]
    list_filter = ["term", "is_current"]
    search_fields = ["code", "name"]


@admin.register(MP)
class MPAdmin(admin.ModelAdmin):
    list_display = ["last_name", "first_name", "club", "district_name", "active", "votings_total"]
    list_filter = ["term", "active", "club"]
    search_fields = ["last_name", "first_name"]


@admin.register(Sitting)
class SittingAdmin(admin.ModelAdmin):
    list_display = ["number", "title"]


class ClubVotingStatsInline(admin.TabularInline):
    model = ClubVotingStats
    extra = 0
    fields = ["club", "yes", "no", "abstain", "absent", "present", "majority_choice", "cohesion"]
    readonly_fields = fields
    can_delete = False


@admin.register(Voting)
class VotingAdmin(admin.ModelAdmin):
    list_display = ["sitting", "number", "date", "title", "yes", "no", "abstain"]
    list_filter = ["kind", "sitting"]
    search_fields = ["title", "topic"]
    inlines = [ClubVotingStatsInline]
    readonly_fields = ["source_hash"]
