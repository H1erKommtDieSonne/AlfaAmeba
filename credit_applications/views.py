from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ApplicationForm, RegistrationForm
from .models import Application, Product, UserProfile
from .permissions import can_manage_bank, can_review_applications, can_submit_applications
from .services import submit_application


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            UserProfile.objects.create(user=user, role=UserProfile.Role.BUSINESS)
            login(request, user)
            messages.success(request, "Аккаунт создан. Можно заполнить первую заявку")
            return redirect("dashboard")
    else:
        form = RegistrationForm()
    return render(request, "registration/register.html", {"form": form})


@login_required
def dashboard(request):
    if can_manage_bank(request.user):
        return redirect("bank-admin-dashboard")
    if can_review_applications(request.user):
        return redirect("bank-queue")
    return redirect("business-cabinet")


@login_required
def business_cabinet(request):
    if not can_submit_applications(request.user):
        return redirect("dashboard")
    if request.method == "POST":
        form = ApplicationForm(request.POST)
        if form.is_valid():
            application = submit_application(form, request.user)
            messages.success(request, "Заявка отправлена и появилась в очереди банка")
            return redirect("application-success", application_id=application.id)
    else:
        initial_product = Product.objects.filter(code="working-capital", active=True).first()
        form = ApplicationForm(
            initial={
                "organization_name": "ООО Север",
                "inn": "7707083893",
                "product": initial_product,
                "amount": 1_500_000,
                "term_months": 12,
                "purpose": "Закупка сезонной партии товара на три месяца",
            }
        )
    return render(
        request,
        "credit_applications/business.html",
        {"form": form, "active_cabinet": "business"},
    )


@login_required
def application_success(request, application_id):
    application = get_object_or_404(
        Application.objects.prefetch_related("versions__product"),
        pk=application_id,
        created_by=request.user,
    )
    version = application.versions.get(version_number=application.current_version)
    return render(
        request,
        "credit_applications/success.html",
        {"application": application, "version": version, "active_cabinet": "business"},
    )


@login_required
def bank_queue(request):
    if not can_review_applications(request.user):
        raise PermissionDenied("Банковская очередь доступна только сотрудникам банка")
    applications = (
        Application.objects.filter(status=Application.Status.SUBMITTED)
        .prefetch_related("versions__product")
        .order_by("-updated_at")
    )
    rows = [
        {
            "application": application,
            "version": application.versions.get(version_number=application.current_version),
        }
        for application in applications
    ]
    return render(
        request,
        "credit_applications/bank_queue.html",
        {"rows": rows, "active_cabinet": "bank"},
    )


@login_required
def bank_admin_dashboard(request):
    if not can_manage_bank(request.user):
        raise PermissionDenied("Раздел доступен только администратору банка")

    recent_applications = (
        Application.objects.select_related("created_by")
        .prefetch_related("versions__product")
        .order_by("-updated_at")[:6]
    )
    recent_rows = [
        {
            "application": application,
            "version": application.versions.get(version_number=application.current_version),
        }
        for application in recent_applications
    ]
    return render(
        request,
        "credit_applications/bank_admin_dashboard.html",
        {
            "active_cabinet": "bank-admin",
            "total_applications": Application.objects.count(),
            "submitted_applications": Application.objects.filter(
                status=Application.Status.SUBMITTED
            ).count(),
            "business_users": UserProfile.objects.filter(
                role=UserProfile.Role.BUSINESS
            ).count(),
            "bank_users": UserProfile.objects.filter(
                role__in=[UserProfile.Role.BANK_EMPLOYEE, UserProfile.Role.BANK_ADMIN]
            ).count(),
            "total_users": get_user_model().objects.count(),
            "recent_rows": recent_rows,
        },
    )

