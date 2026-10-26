from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from users.models import User
from users.permissions import can_manage_bank


@login_required
def organization_access_status(request):
    """Устаревшая публичная страница статуса доступа: безопасно возвращаем в кабинет."""
    return redirect("business-cabinet")


@login_required
def review_access_request(request, request_id):
    """Доступ к старым запросам закрыт: подтверждение полномочий больше не требуется."""
    if not can_manage_bank(request.user):
        raise PermissionDenied("Раздел доступен только администратору банка")
    return redirect("bank-admin-dashboard")


@login_required
def bank_admin_dashboard(request):
    if not can_manage_bank(request.user):
        raise PermissionDenied("Раздел доступен только администратору банка")

    return render(
        request,
        "organizations/bank_admin_dashboard.html",
        {
            "business_users": User.objects.filter(role=User.Role.BUSINESS).count(),
            "bank_users": User.objects.filter(
                role__in=[
                    User.Role.BANK_REVIEWER,
                    User.Role.SENIOR_APPROVER,
                    User.Role.BANK_ADMIN,
                ]
            ).count(),
            "total_users": User.objects.count(),
        },
    )
