from django.contrib import admin

from .models import (
    Application,
    ApplicationComment,
    ApplicationDocument,
    ApplicationEvent,
    ApplicationVersion,
    DocumentProcessingJob,
    Organization,
    OrganizationAccessRequest,
    OrganizationMembership,
    Product,
    SerializedDocument,
    UserProfile,
)


class ApplicationVersionInline(admin.TabularInline):
    model = ApplicationVersion
    extra = 0
    readonly_fields = ("id", "version_number", "snapshot", "submitted_at", "created_at")


class ApplicationDocumentInline(admin.TabularInline):
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


@admin.register(ApplicationVersion)
class ApplicationVersionAdmin(admin.ModelAdmin):
    list_display = ("application", "version_number", "product", "submitted_at")
    readonly_fields = ("snapshot", "submitted_at", "created_at")
    inlines = (ApplicationDocumentInline,)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "organization_name",
        "inn",
        "status",
        "assigned_to",
        "current_version",
        "updated_at",
    )
    list_filter = ("status",)
    search_fields = ("organization_name", "inn")
    inlines = (ApplicationVersionInline,)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "min_amount", "max_amount", "active")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)


class OrganizationMembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "inn", "verification_status", "updated_at")
    list_filter = ("verification_status",)
    search_fields = ("name", "inn")
    inlines = (OrganizationMembershipInline,)


@admin.register(OrganizationAccessRequest)
class OrganizationAccessRequestAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "status", "reviewed_by", "created_at")
    list_filter = ("status",)
    search_fields = ("organization__name", "organization__inn", "user__email")


@admin.register(ApplicationComment)
class ApplicationCommentAdmin(admin.ModelAdmin):
    list_display = ("application", "author", "audience", "created_at")
    list_filter = ("audience",)


@admin.register(ApplicationEvent)
class ApplicationEventAdmin(admin.ModelAdmin):
    list_display = ("application", "event_type", "actor", "from_status", "to_status", "created_at")
    list_filter = ("event_type", "to_status")
    readonly_fields = ("application", "actor", "event_type", "from_status", "to_status", "message", "metadata", "created_at")


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

