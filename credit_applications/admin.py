from django.contrib import admin

from .models import Application, ApplicationVersion, Product, UserProfile


class ApplicationVersionInline(admin.TabularInline):
    model = ApplicationVersion
    extra = 0
    readonly_fields = ("id", "version_number", "snapshot", "submitted_at", "created_at")


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("organization_name", "inn", "status", "current_version", "updated_at")
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

