from django.contrib import admin

from .models import Organization, OrganizationAccessRequest, OrganizationMembership


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
