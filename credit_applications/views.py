from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ApplicationForm, CommentForm, RegistrationForm, ReviewActionForm
from .models import (
    Application,
    ApplicationComment,
    DocumentProcessingJob,
    OrganizationAccessRequest,
    Product,
    UserProfile,
)
from .permissions import (
    can_access_business_application,
    can_approve_applications,
    can_edit_business_application,
    can_manage_bank,
    can_review_applications,
    can_submit_applications,
)
from .services import (
    add_comment,
    perform_review_action,
    review_organization_access,
    submit_application,
    submit_revision,
)


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
        form = ApplicationForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                result = submit_application(form, request.user)
            except ValidationError as error:
                form.add_error(None, error.message)
            else:
                if isinstance(result, OrganizationAccessRequest):
                    messages.success(
                        request,
                        "Запрос на подтверждение полномочий отправлен администратору банка",
                    )
                    return redirect("organization-access-status")
                application = result
                messages.success(request, "Заявка отправлена и появилась в очереди банка")
                return redirect("application-success", application_id=application.id)
    else:
        initial_product = Product.objects.filter(code="working-capital", active=True).first()
        membership = (
            request.user.organization_memberships.filter(is_active=True)
            .select_related("organization")
            .first()
        )
        form = ApplicationForm(
            initial={
                "organization_name": membership.organization.name if membership else "",
                "inn": membership.organization.inn if membership else "",
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
def organization_access_status(request):
    if not can_submit_applications(request.user):
        return redirect("dashboard")
    access_requests = request.user.organization_access_requests.select_related(
        "organization", "reviewed_by"
    )
    return render(
        request,
        "credit_applications/organization_access_status.html",
        {"access_requests": access_requests, "active_cabinet": "business"},
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
def business_applications(request):
    if not can_submit_applications(request.user):
        return redirect("dashboard")
    applications = (
        Application.objects.filter(
            Q(created_by=request.user)
            | Q(organization__memberships__user=request.user, organization__memberships__is_active=True)
        )
        .exclude(status=Application.Status.DRAFT)
        .prefetch_related("versions__product")
        .distinct()
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
        "credit_applications/business_applications.html",
        {"rows": rows, "active_cabinet": "business-applications"},
    )


@login_required
def business_application_detail(request, application_id):
    application = get_object_or_404(
        Application.objects.select_related("organization", "assigned_to").prefetch_related(
            "versions__product", "comments__author", "events__actor"
        ),
        pk=application_id,
    )
    if not can_access_business_application(request.user, application):
        raise PermissionDenied("Заявка недоступна")
    version = application.versions.get(version_number=application.current_version)
    can_edit = can_edit_business_application(request.user, application)
    initial = {
        "organization_name": application.organization_name,
        "inn": application.inn,
        "product": version.product,
        "amount": version.amount,
        "term_months": version.term_months,
        "purpose": version.purpose,
    }
    revision_form = ApplicationForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Заявка сейчас недоступна для доработки")
        if revision_form.is_valid():
            try:
                submit_revision(application, revision_form, request.user)
            except ValidationError as error:
                revision_form.add_error(None, error.message)
            else:
                messages.success(request, "Новая версия заявки отправлена в банк")
                return redirect("business-application-detail", application_id=application.pk)
    return render(
        request,
        "credit_applications/business_application_detail.html",
        {
            "application": application,
            "version": version,
            "revision_form": revision_form,
            "can_edit": can_edit,
            "client_comments": application.comments.filter(
                audience=ApplicationComment.Audience.CLIENT
            ),
            "documents": version.documents.all(),
            "processing_job": DocumentProcessingJob.objects.filter(version=version).first(),
            "active_cabinet": "business-applications",
        },
    )
@login_required
def bank_queue(request):
    if not can_review_applications(request.user):
        raise PermissionDenied("Банковская очередь доступна только сотрудникам банка")
    selected_status = request.GET.get("status", "")
    if can_approve_applications(request.user) and not selected_status:
        selected_status = Application.Status.PENDING_APPROVAL
    applications = (
        Application.objects.exclude(status=Application.Status.DRAFT)
        .select_related("assigned_to")
        .prefetch_related("versions__product")
        .order_by("-updated_at")
    )
    if selected_status in Application.Status.values:
        applications = applications.filter(status=selected_status)
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
        {
            "rows": rows,
            "active_cabinet": "bank",
            "selected_status": selected_status,
            "status_choices": Application.Status.choices,
            "new_count": Application.objects.filter(status=Application.Status.SUBMITTED).count(),
            "review_count": Application.objects.filter(status=Application.Status.IN_REVIEW).count(),
            "revision_count": Application.objects.filter(
                status=Application.Status.NEEDS_REVISION
            ).count(),
            "approval_count": Application.objects.filter(
                status=Application.Status.PENDING_APPROVAL
            ).count(),
        },
    )


@login_required
def bank_application_detail(request, application_id):
    if not can_review_applications(request.user):
        raise PermissionDenied("Карточка заявки доступна только сотрудникам банка")
    application = get_object_or_404(
        Application.objects.select_related("organization", "created_by", "assigned_to").prefetch_related(
            "versions__product", "comments__author", "events__actor"
        ),
        pk=application_id,
    )
    action_form = ReviewActionForm()
    comment_form = CommentForm()
    if request.method == "POST":
        if request.POST.get("form_type") == "comment":
            comment_form = CommentForm(request.POST)
            if comment_form.is_valid():
                add_comment(application, author=request.user, **comment_form.cleaned_data)
                messages.success(request, "Комментарий добавлен")
                return redirect("bank-application-detail", application_id=application.pk)
        else:
            action_form = ReviewActionForm(request.POST)
            if action_form.is_valid():
                try:
                    perform_review_action(
                        application,
                        actor=request.user,
                        action=action_form.cleaned_data["action"],
                        note=action_form.cleaned_data["note"],
                    )
                except ValidationError as error:
                    action_form.add_error(None, error.message)
                else:
                    messages.success(request, "Статус заявки обновлён")
                    return redirect("bank-application-detail", application_id=application.pk)
    version = application.versions.get(version_number=application.current_version)
    processing_job = (
        DocumentProcessingJob.objects.filter(version=version)
        .prefetch_related("serialized_documents__source_document")
        .first()
    )
    return render(
        request,
        "credit_applications/bank_application_detail.html",
        {
            "application": application,
            "version": version,
            "action_form": action_form,
            "comment_form": comment_form,
            "documents": version.documents.all(),
            "processing_job": processing_job,
            "active_cabinet": "bank",
        },
    )


@login_required
def bank_admin_dashboard(request):
    if not can_manage_bank(request.user):
        raise PermissionDenied("Раздел доступен только администратору банка")

    pending_access_requests = OrganizationAccessRequest.objects.filter(
        status=OrganizationAccessRequest.Status.PENDING
    ).select_related("organization", "user")
    return render(
        request,
        "credit_applications/bank_admin_dashboard.html",
        {
            "active_cabinet": "bank-admin",
            "business_users": UserProfile.objects.filter(
                role=UserProfile.Role.BUSINESS
            ).count(),
            "bank_users": UserProfile.objects.filter(
                role__in=[
                    UserProfile.Role.BANK_REVIEWER,
                    UserProfile.Role.SENIOR_APPROVER,
                    UserProfile.Role.BANK_ADMIN,
                ]
            ).count(),
            "total_users": get_user_model().objects.count(),
            "pending_access_requests": pending_access_requests,
            "pending_access_count": pending_access_requests.count(),
        },
    )

