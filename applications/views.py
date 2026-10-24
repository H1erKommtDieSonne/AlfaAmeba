import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django import forms
from django.views.decorators.http import require_GET
from django.shortcuts import get_object_or_404, redirect, render

from analytics.services import current_analysis_run
from documents.models import DocumentProcessingJob
from node_integration.presentation import node_review
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

ANALYSIS_STATUS_DISPLAY = {
    "WAITING": ("Ожидает обработки документов", "status--progress"),
    "PENDING": ("Готова к запуску", "status--progress"),
    "PROCESSING": ("Выполняется", "status--progress"),
    "READY": ("Готова", "status--positive"),
    "PARTIAL": ("Частичный результат", "status--warning"),
    "FAILED": ("Ошибка", "status--negative"),
}
ANALYSIS_RUNNING_MESSAGES = {
    "WAITING": "Запуск аналитики ожидает извлечения документов.",
    "PENDING": "Запуск аналитики готов к выполнению.",
    "PROCESSING": "Идёт обработка данных. Результат появится после завершения; страницу можно обновить позже.",
}
RECOMMENDATION_DISPLAY = {
    "PROCEED_TO_REVIEW": "Передать на ручное рассмотрение",
    "REQUEST_INFORMATION": "Запросить дополнительную информацию",
    "MANUAL_REVIEW": "Требуется ручная проверка",
}
SCENARIO_DISPLAY = {
    "normal": "Стандартный сценарий",
    "missing_data": "Недостаточно данных",
    "manual_review": "Ручная проверка",
}
COMPONENT_DISPLAY = (
    ("evidence", "Документы"),
    ("radar", "Радар показателей"),
    ("cashflow", "Денежный поток"),
    ("scoring", "Скоринг"),
)
COMPONENT_STATUS_DISPLAY = {
    "evidence": {
        "OK": ("Документы разобраны", "status--positive"),
        "INCOMPLETE": ("Данные неполные", "status--warning"),
        "UNKNOWN": ("Нет данных", "status--neutral"),
    },
    "radar": {
        "OK": ("Показатели в норме", "status--positive"),
        "MANUAL_REVIEW": ("Требуется ручная проверка", "status--warning"),
        "UNKNOWN": ("Нет данных", "status--neutral"),
    },
    "cashflow": {
        "OK": ("Данные доступны", "status--positive"),
        "UNKNOWN": ("Нет данных", "status--neutral"),
    },
    "scoring": {
        "OK": ("Демо-оценка доступна", "status--positive"),
        "UNKNOWN": ("Нет данных", "status--neutral"),
    },
}


def _analysis_components(result):
    components = result.get("components")
    if not isinstance(components, dict):
        components = {}
    items = []
    for key, title in COMPONENT_DISPLAY:
        component = components.get(key)
        if not isinstance(component, dict):
            component = {}
        status = component.get("status", "UNKNOWN")
        status_label, status_class = COMPONENT_STATUS_DISPLAY[key].get(
            status, ("Нет данных", "status--neutral")
        )
        items.append(
            {
                "key": key,
                "title": title,
                "status": status,
                "status_label": status_label,
                "status_class": status_class,
                "summary": component.get("summary", ""),
            }
        )
    return items


def _analysis_view(run):
    """Отображаемые подписи для текущего запуска; ошибки воркера не раскрываются."""
    if run is None:
        return {"state": "no_analysis", "status_label": "", "status_class": ""}

    status_label, status_class = ANALYSIS_STATUS_DISPLAY.get(
        run.status, ("Неизвестный статус", "status--neutral")
    )
    view = {
        "state": "running",
        "status": run.status,
        "status_label": status_label,
        "status_class": status_class,
        "message": ANALYSIS_RUNNING_MESSAGES.get(run.status, ""),
    }
    if run.status == "FAILED":
        view["state"] = "failed"
        return view
    if run.status not in {"READY", "PARTIAL"}:
        return view

    result = run.result if isinstance(run.result, dict) else None
    if result is None:
        view["state"] = "unavailable"
        return view

    recommendation = result.get("recommendation")
    scenario = result.get("scenario_id")
    reason_codes = result.get("reason_codes")
    if not isinstance(reason_codes, list):
        reason_codes = []
    view.update(
        {
            "state": "result",
            "is_demo": result.get("is_demo") is True,
            "recommendation_label": RECOMMENDATION_DISPLAY.get(
                recommendation, "Рекомендация недоступна"
            ),
            "scenario_label": SCENARIO_DISPLAY.get(scenario, "Сценарий не указан"),
            "summary": result.get("summary", ""),
            "reason_codes": reason_codes,
            "components": _analysis_components(result),
            "model_version": result.get("model_version") or run.model_version,
            "policy_version": result.get("policy_version") or run.policy_version,
            "version_number": run.version.version_number,
            "completed_at": run.completed_at,
        }
    )
    return view


def _node_job_for(version):
    """Задание внешнего узла для версии заявки (интеграция обязательна)."""
    from node_integration.models import NodeJob

    return NodeJob.objects.filter(version=version).first()


def _feedback_groups(queryset):
    """Безопасные группы замечаний узла: только отображаемые поля, без сырых результатов."""
    groups = []
    for feedback in queryset.select_related("job__version"):
        issues = feedback.issues if isinstance(feedback.issues, list) else []
        safe_issues = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            safe_issues.append(
                {
                    "code": issue.get("code", ""),
                    "message": issue.get("message", ""),
                    "field": issue.get("field", ""),
                    "document_id": issue.get("document_id", ""),
                    "filename": issue.get("filename", ""),
                    "suggestion": issue.get("suggestion", ""),
                }
            )
        groups.append(
            {
                "feedback_id": str(feedback.feedback_id),
                "created_at": feedback.created_at,
                "version_number": feedback.job.version.version_number,
                "issues": safe_issues,
            }
        )
    return groups


def _document_feedback_context(application):
    """Замечания узла: текущая выбранная версия и история более старых версий."""
    from node_integration.models import NodeFeedback

    current_number = application.current_version
    current_qs = NodeFeedback.objects.filter(
        job__version__application=application,
        job__version__version_number=current_number,
    ).order_by("created_at", "feedback_id")
    history_qs = (
        NodeFeedback.objects.filter(job__version__application=application)
        .exclude(job__version__version_number=current_number)
        .order_by("-created_at", "-job__version__version_number", "feedback_id")
    )
    return _feedback_groups(current_qs), _feedback_groups(history_qs)


def _node_result_json(node_job):
    """JSON только для панели банка; шаблон выводит его с автоэкранированием."""
    if node_job is None or node_job.status != "COMPLETED":
        return ""
    result = node_job.result
    if not isinstance(result, dict):
        return ""
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


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
    feedback_current, feedback_history = _document_feedback_context(application)
    node_job = _node_job_for(version)
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
            "document_feedback_current": feedback_current,
            "document_feedback_history": feedback_history,
            # Клиенту — только безопасное представление, без сырого результата узла.
            "node_review": node_review(node_job),
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
    analysis_run = current_analysis_run(application)
    node_job = _node_job_for(version)
    feedback_current, feedback_history = _document_feedback_context(application)
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
            "analysis_run": analysis_run,
            "analysis_view": _analysis_view(analysis_run),
            "node_job": node_job,
            "node_result_json": _node_result_json(node_job),
            "node_review": node_review(node_job),
            "document_feedback_current": feedback_current,
            "document_feedback_history": feedback_history,
        },
    )
