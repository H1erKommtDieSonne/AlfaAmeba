from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from users.models import User
from users.permissions import can_manage_bank, can_submit_applications

from .models import OrganizationAccessRequest
from .services import review_organization_access


@login_required
def organization_access_status(request):
    if not can_submit_applications(request.user):
        return redirect("dashboard")
    access_requests = request.user.organization_access_requests.select_related(
        "organization", "reviewed_by"
    )
    return render(
        request,
        "organizations/access_status.html",
        {"access_requests": access_requests},
    )


@login_required
def review_access_request(request, request_id):
    if not can_manage_bank(request.user):
        raise PermissionDenied("Раздел доступен только администратору банка")
    if request.method != "POST":
        raise PermissionDenied("Используйте форму подтверждения")
    access_request = get_object_or_404(OrganizationAccessRequest, pk=request_id)
    try:
        review_organization_access(
            access_request,
            reviewer=request.user,
            approve=request.POST.get("action") == "approve",
            note=request.POST.get("note", ""),
        )
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Запрос на доступ обработан")
    return redirect("bank-admin-dashboard")


@login_required
def bank_admin_dashboard(request):
    if not can_manage_bank(request.user):
        raise PermissionDenied("Раздел доступен только администратору банка")

    pending_access_requests = OrganizationAccessRequest.objects.filter(
        status=OrganizationAccessRequest.Status.PENDING
    ).select_related("organization", "user")
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
            "pending_access_requests": pending_access_requests,
            "pending_access_count": pending_access_requests.count(),
        },
    )
