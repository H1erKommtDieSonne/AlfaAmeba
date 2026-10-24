from django.contrib import admin

from .models import AnalysisRun


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "version",
        "status",
        "attempts",
        "model_version",
        "policy_version",
        "updated_at",
    )
    list_filter = ("status", "model_version", "policy_version")
    search_fields = ("id", "version__application__inn", "claimed_by")
    readonly_fields = (
        "id",
        "version",
        "status",
        "attempts",
        "max_attempts",
        "available_at",
        "claimed_at",
        "claimed_by",
        "lease_expires_at",
        "lease_token",
        "model_version",
        "policy_version",
        "policy_snapshot",
        "source_hash",
        "input_hash",
        "extraction_status",
        "result",
        "last_error",
        "completed_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False
