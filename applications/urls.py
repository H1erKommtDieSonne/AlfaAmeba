from django.urls import path

from . import views

urlpatterns = [
    path("", views.business_cabinet, name="business-cabinet"),
    path("applications/", views.business_applications, name="business-applications"),
    path("applications/<uuid:application_id>/success/", views.application_success, name="application-success"),
    path(
        "applications/<uuid:application_id>/",
        views.business_application_detail,
        name="business-application-detail",
    ),
    path("bank/", views.bank_queue, name="bank-queue"),
    path(
        "bank/applications/<uuid:application_id>/",
        views.bank_application_detail,
        name="bank-application-detail",
    ),
]
