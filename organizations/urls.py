from django.urls import path

from . import views

urlpatterns = [
    path(
        "organization/access/",
        views.organization_access_status,
        name="organization-access-status",
    ),
    path("bank/admin-dashboard/", views.bank_admin_dashboard, name="bank-admin-dashboard"),
    path(
        "bank/access-requests/<int:request_id>/review/",
        views.review_access_request,
        name="review-access-request",
    ),
]
