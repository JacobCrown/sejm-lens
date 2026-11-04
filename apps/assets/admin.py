from django.contrib import admin, messages

from .extractors import get_extractor
from .models import AssetDeclaration, AssetItem, ExtractionRun
from .services.extraction import run_extraction


class AssetItemInline(admin.TabularInline):
    model = AssetItem
    extra = 0
    fields = ["category", "label", "description", "value", "currency", "value_pln", "area_m2", "ownership"]


class ExtractionRunInline(admin.TabularInline):
    model = ExtractionRun
    extra = 0
    can_delete = False
    fields = [
        "created_at", "extractor", "model", "success", "confidence", "duration_ms",
        "cost_usd", "prompt_tokens", "completion_tokens", "llm_calls", "error",
    ]
    readonly_fields = fields


@admin.register(AssetDeclaration)
class AssetDeclarationAdmin(admin.ModelAdmin):
    list_display = ["mp", "year", "kind", "status", "confidence", "is_demo", "reviewed", "updated_at"]
    list_filter = ["status", "kind", "is_demo", "reviewed", "year"]
    search_fields = ["mp__last_name", "mp__first_name"]
    autocomplete_fields = ["mp"]
    raw_id_fields = ["raw"]
    inlines = [AssetItemInline, ExtractionRunInline]
    actions = ["extract_default", "mark_reviewed"]

    @admin.action(description="Run extraction (ASSET_EXTRACTOR)")
    def extract_default(self, request, queryset):
        extractor = get_extractor()
        ok = 0
        for declaration in queryset.select_related("mp", "raw"):
            if run_extraction(declaration, extractor).success:
                ok += 1
        self.message_user(request, f"Analysed {ok} of {queryset.count()} declarations.", messages.INFO)

    @admin.action(description="Mark as reviewed")
    def mark_reviewed(self, request, queryset):
        n = queryset.update(reviewed=True, status=AssetDeclaration.Status.EXTRACTED)
        self.message_user(request, f"Reviewed {n} declarations.", messages.SUCCESS)
