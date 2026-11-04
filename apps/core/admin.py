from django.contrib import admin

from .models import IngestionRun, RawDocument


@admin.register(RawDocument)
class RawDocumentAdmin(admin.ModelAdmin):
    list_display = ["source", "source_id", "content_type", "fetched_at"]
    list_filter = ["source"]
    search_fields = ["source_id", "url"]
    readonly_fields = ["sha256", "fetched_at"]


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = ["kind", "status", "started_at", "finished_at"]
    list_filter = ["kind", "status"]
    readonly_fields = ["kind", "status", "started_at", "finished_at", "stats", "error"]
