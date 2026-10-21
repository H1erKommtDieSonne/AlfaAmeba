from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("", include("users.urls")),
    path("", include("organizations.urls")),
    path("", include("applications.urls")),
    path("", include("documents.urls")),
]
