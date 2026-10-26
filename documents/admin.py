from django.contrib import admin

from .models import ApplicationDocument, DocumentProcessingJob, SerializedDocument


class ApplicationDocumentInline(admin.TabularInline):
    """Документы версии — встраиваются в карточку версии заявки (applications/admin.py)."""

    model = ApplicationDocument
    extra = 0
    readonly_fields = (
        "id",
        "original_name",
        "content_type",
        "size",
        "sha256",
        "created_at",
    )


@admin.register(DocumentProcessingJob)
class DocumentProcessingJobAdmin(admin.ModelAdmin):
    list_display = ("id", "version", "status", "attempts", "claimed_by", "updated_at")
    list_filter = ("status",)
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
        "completed_at",
        "result_summary",
        "last_error",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(SerializedDocument)
class SerializedDocumentAdmin(admin.ModelAdmin):
    list_display = ("source_document", "document_type", "schema_version", "confidence", "created_at")
    readonly_fields = (
        "id",
        "job",
        "source_document",
        "document_type",
        "schema_version",
        "confidence",
        "payload",
        "created_at",
    )

    def has_add_permission(self, request):
        return False
