from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("", views.business_cabinet, name="business-cabinet"),
    path("applications/<uuid:application_id>/success/", views.application_success, name="application-success"),
    path("bank/", views.bank_queue, name="bank-queue"),
    path("bank/admin-dashboard/", views.bank_admin_dashboard, name="bank-admin-dashboard"),
]

