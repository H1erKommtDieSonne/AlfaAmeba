from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django import forms
from django.views.decorators.http import require_GET
from django.shortcuts import get_object_or_404, redirect, render

from documents.models import DocumentProcessingJob
from organizations.models import OrganizationAccessRequest
from products.models import Product
from products.advisor import recommend, requirements
from .advisor_forms import NeedsForm
from users.permissions import (
    can_approve_applications,
    can_review_applications,
    can_submit_applications,
)

from .forms import ApplicationForm, CommentForm, ReviewActionForm
from .models import Application, ApplicationComment
from .permissions import can_access_business_application, can_edit_business_application
from .services import (
    add_comment,
    perform_review_action,
    submit_application,
    submit_revision,
)


@login_required
@require_GET
def financing_advice(request):
    if not can_submit_applications(request.user):
        raise PermissionDenied("Подбор доступен клиентам")
    form = NeedsForm(request.GET)
    form.is_valid()
    data = dict(form.cleaned_data)
    for name, field in [("amount", forms.DecimalField(min_value=1, max_digits=14, decimal_places=2, required=False)),
                        ("term_months", forms.IntegerField(min_value=1, max_value=120, required=False))]:
        try:
            data[name] = field.clean(request.GET.get(name))
        except ValidationError:
            return JsonResponse({"error": "Укажите корректную сумму и срок."}, status=400)
    products = list(Product.objects.filter(active=True))
    result = recommend(data, products)
    selected = next((p for p in products if p.code == request.GET.get("product")), None)
    result["checklist"] = requirements(selected.code, data.get("collateral", False)) if selected else []
    result["selection_matches"] = bool(selected and any(item["code"] == selected.code and item["available"] for item in result["items"]))
    return JsonResponse(result)


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
    return render(request, "applications/business.html", {"form": form})


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
        "applications/success.html",
        {"application": application, "version": version},
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
    return render(request, "applications/business_applications.html", {"rows": rows})


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
        **(version.snapshot or {}).get("needs", {}),
    }
    revision_form = ApplicationForm(request.POST if request.method == "POST" else None,
                                    request.FILES if request.method == "POST" else None,
                                    initial=initial, previous_version=version)
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
        "applications/business_application_detail.html",
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
        "applications/bank_queue.html",
        {
            "rows": rows,
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
        "applications/bank_application_detail.html",
        {
            "application": application,
            "version": version,
            "action_form": action_form,
            "comment_form": comment_form,
            "documents": version.documents.all(),
            "processing_job": processing_job,
        },
    )
