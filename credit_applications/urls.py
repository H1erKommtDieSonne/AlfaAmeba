from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("", views.business_cabinet, name="business-cabinet"),
    path("applications/", views.business_applications, name="business-applications"),
    path(
        "organization/access/",
        views.organization_access_status,
        name="organization-access-status",
    ),
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
    path("bank/admin-dashboard/", views.bank_admin_dashboard, name="bank-admin-dashboard"),
    path(
        "bank/access-requests/<int:request_id>/review/",
        views.review_access_request,
        name="review-access-request",
    ),
]

