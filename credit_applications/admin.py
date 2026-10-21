from django.contrib import admin

from .models import (
    Application,
    ApplicationComment,
    ApplicationEvent,
    ApplicationVersion,
    Organization,
    OrganizationAccessRequest,
    OrganizationMembership,
    Product,
    UserProfile,
)


class ApplicationVersionInline(admin.TabularInline):
    model = ApplicationVersion
    extra = 0
    readonly_fields = ("id", "version_number", "snapshot", "submitted_at", "created_at")


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

