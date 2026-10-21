from django.contrib import admin

from documents.admin import ApplicationDocumentInline

from .models import Application, ApplicationComment, ApplicationEvent, ApplicationVersion


class ApplicationVersionInline(admin.TabularInline):
    model = ApplicationVersion
    extra = 0
    readonly_fields = ("id", "version_number", "snapshot", "submitted_at", "created_at")


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


@admin.register(ApplicationComment)
class ApplicationCommentAdmin(admin.ModelAdmin):
    list_display = ("application", "author", "audience", "created_at")
    list_filter = ("audience",)


@admin.register(ApplicationEvent)
class ApplicationEventAdmin(admin.ModelAdmin):
    list_display = ("application", "event_type", "actor", "from_status", "to_status", "created_at")
    list_filter = ("event_type", "to_status")
    readonly_fields = ("application", "actor", "event_type", "from_status", "to_status", "message", "metadata", "created_at")
