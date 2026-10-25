import json
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django import forms
from django.views.decorators.http import require_GET
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from analytics.services import current_analysis_run
from documents.models import DocumentProcessingJob
from node_integration.financial import financial_view
from node_integration.forms import FinancialInputsForm, statement_formset
from node_integration.models import FinancialInputs
from node_integration.presentation import node_financial_summary, node_review
from node_integration.services import NodeJobBusy, request_recalculation
from node_integration.services import latest_completed_job as latest_completed_node_job
from node_integration.services import latest_job as latest_node_job
from organizations.models import OrganizationAccessRequest
from products.models import Product
from products.advisor import NEEDS, recommend, requirements
from .advisor_forms import NeedsForm
from users.permissions import (
    can_approve_applications,
    can_process_applications,
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


def _node_jobs(version):
    """(последняя попытка узла, попытка, чей результат показываем).

    Пока пересчёт после подтверждения данных в работе, показываем прошлый готовый
    результат, а не пустую карточку; если готового нет — последнюю попытку.
    """
    latest = latest_node_job(version)
    shown = latest_completed_node_job(version) or latest
    return latest, shown


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


def _document_feedback_context(application, job):
    """Замечания узла: к показываемой попытке текущей версии и вся остальная история.

    В историю попадают и прошлые версии, и прошлые попытки текущей версии: их
    замечания относятся к другому набору данных и за текущие выдаваться не должны.
    """
    from node_integration.models import NodeFeedback

    feedback = NodeFeedback.objects.filter(job__version__application=application)
    current_qs = feedback.filter(job=job).order_by("created_at", "feedback_id") if job else feedback.none()
    history_qs = (
        feedback.exclude(job=job) if job else feedback
    ).order_by("-created_at", "-job__version__version_number", "feedback_id")
    return _feedback_groups(current_qs), _feedback_groups(history_qs)


def _local_findings(processing_job):
    """Замечания локального разбора документов (схема document-checks-1) одним списком с файлом и местом."""
    if processing_job is None or processing_job.status != "COMPLETED":
        return []
    findings = []
    for result in processing_job.serialized_documents.all():
        if result.schema_version != "document-checks-1" or not isinstance(result.payload, dict):
            continue
        for finding in result.payload.get("findings") or []:
            if not isinstance(finding, dict) or not finding.get("message"):
                continue
            findings.append({
                "message": str(finding["message"]),
                "filename": result.source_document.original_name,
                "locator": str(finding.get("location") or ""),
                "quote": str(finding.get("text") or ""),
            })
    return findings


def _remarks(financial, feedback_current, processing_job):
    """Замечания к документам одним списком: от узла и от локального разбора.

    Полный список узла лежит в его результате; отдельный feedback — его же урезанная
    копия, она нужна, только пока результата нет (порядок доставки не гарантирован).
    """
    if financial and financial.get("state") == "ready":
        remarks = list(financial["issues"])
    else:
        remarks = [issue for group in feedback_current for issue in group["issues"]]
    return remarks + _local_findings(processing_job)


def _client_needs(version):
    """Задача клиента из снимка версии: что он хотел сделать и предлагал ли залог."""
    snapshot = version.snapshot if isinstance(version.snapshot, dict) else {}
    needs = snapshot.get("needs") if isinstance(snapshot.get("needs"), dict) else {}
    return {
        "need": dict(NEEDS).get(needs.get("need"), ""),
        "collateral": needs.get("collateral") is True,
    }


def _node_result_json(node_job):
    """JSON только для легаси-панели банка; шаблон экранирует его сам.

    Для финансовой схемы v2 (вложенный ``result.schema_version`` строго ``int``
    2, включая неполный ``financial_analysis``) сырой JSON не показываем: он
    может содержать необработанные выходы модели и кредитные решения.
    Легаси-ответы без такой схемы отображаются как раньше.
    """
    if node_job is None or node_job.status != "COMPLETED":
        return ""
    result = node_job.result
    if not isinstance(result, dict):
        return ""
    inner = result.get("result")
    if isinstance(inner, dict):
        version = inner.get("schema_version")
        if isinstance(version, int) and not isinstance(version, bool) and version == 2:
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
    _, node_job = _node_jobs(version)
    feedback_current, feedback_history = _document_feedback_context(application, node_job)
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


def _bank_queue(user, status):
    """Очередь банка с фильтром по статусу: общая для списка и для листания в карточке."""
    selected_status = status or ""
    if can_approve_applications(user) and not selected_status:
        selected_status = Application.Status.PENDING_APPROVAL
    applications = (
        Application.objects.exclude(status=Application.Status.DRAFT)
        .select_related("assigned_to")
        .prefetch_related("versions__product")
        .order_by("-updated_at", "id")
    )
    if selected_status in Application.Status.values:
        applications = applications.filter(status=selected_status)
    return selected_status, applications


def _queue_neighbours(user, status, application):
    """Соседние заявки в той же выборке очереди: (предыдущая, следующая, позиция, всего).

    Если заявка из выборки выпала (статус сменился), листать не от чего — только «К очереди».
    """
    _, applications = _bank_queue(user, status)
    ids = list(applications.values_list("id", flat=True))
    if application.pk not in ids:
        return None
    index = ids.index(application.pk)
    return {
        "previous": ids[index - 1] if index > 0 else None,
        "next": ids[index + 1] if index + 1 < len(ids) else None,
        "position": index + 1,
        "total": len(ids),
    }


@login_required
def bank_queue(request):
    if not can_review_applications(request.user):
        raise PermissionDenied("Банковская очередь доступна только сотрудникам банка")
    selected_status, applications = _bank_queue(request.user, request.GET.get("status", ""))
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
    # Фильтр очереди едет за сотрудником в карточку: по нему листаются соседние заявки
    # и по нему же «К очереди» возвращает в ту же выборку.
    queue_status = request.GET.get("status")
    queue_query = f"?{urlencode({'status': queue_status})}" if queue_status is not None else ""
    detail_url = reverse("bank-application-detail", args=[application.pk]) + queue_query
    version = application.versions.get(version_number=application.current_version)
    inputs = FinancialInputs.objects.filter(version=version).select_related("confirmed_by").first()
    can_confirm_inputs = _can_confirm_inputs(request.user, application)
    action_form = ReviewActionForm()
    comment_form = CommentForm()
    inputs_form = FinancialInputsForm(instance=inputs, version=version)
    statements = statement_formset(version=version, inputs=inputs)
    if request.method == "POST":
        if request.POST.get("form_type") == "comment":
            comment_form = CommentForm(request.POST)
            if comment_form.is_valid():
                add_comment(application, author=request.user, **comment_form.cleaned_data)
                messages.success(request, "Комментарий добавлен")
                return redirect(detail_url)
        elif request.POST.get("form_type") == "node_inputs":
            if not can_confirm_inputs:
                raise PermissionDenied("Подтверждать данные для расчёта может специалист, у которого заявка в работе")
            inputs_form = FinancialInputsForm(request.POST, instance=inputs, version=version)
            accounts = inputs_form.cleaned_data["accounts"] if inputs_form.is_valid() else ()
            statements = statement_formset(request.POST, version=version, inputs=inputs, accounts=accounts)
            if inputs_form.is_valid() and statements.is_valid():
                with transaction.atomic():
                    inputs_form.save(statements=statements.periods(), user=request.user)
                    try:
                        _, created = request_recalculation(version, user=request.user)
                    except NodeJobBusy as error:
                        messages.warning(request, f"Данные сохранены. {error}.")
                    else:
                        messages.success(
                            request,
                            "Данные сохранены, заявка поставлена на пересчёт узлом"
                            if created else "Данные сохранены: узел получит их при отправке заявки",
                        )
                return redirect(detail_url)
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
                    return redirect(detail_url)
    processing_job = (
        DocumentProcessingJob.objects.filter(version=version)
        .prefetch_related("serialized_documents__source_document")
        .first()
    )
    analysis_run = current_analysis_run(application)
    node_job, shown_node_job = _node_jobs(version)
    feedback_current, feedback_history = _document_feedback_context(application, shown_node_job)
    financial = financial_view(shown_node_job)
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
            "node_result_json": _node_result_json(shown_node_job),
            "node_review": node_review(shown_node_job),
            # Безопасная сводка финансового анализа; клиенту не отдаётся.
            "node_financial_summary": node_financial_summary(shown_node_job),
            "document_feedback_current": feedback_current,
            "document_feedback_history": feedback_history,
            "queue_query": queue_query,
            "queue_nav": _queue_neighbours(request.user, queue_status, application),
            "financial": financial,
            "recalculation": node_job if node_job and shown_node_job and node_job.pk != shown_node_job.pk else None,
            "remarks": _remarks(financial, feedback_current, processing_job),
            "client_needs": _client_needs(version),
            "financial_inputs": inputs,
            "can_confirm_inputs": can_confirm_inputs,
            "inputs_form": inputs_form,
            "statements": statements,
        },
    )


def _can_confirm_inputs(user, application):
    """Данные для расчёта подтверждает специалист, у которого заявка в работе (как и решения по ней)."""
    return (
        can_process_applications(user)
        and application.status == Application.Status.IN_REVIEW
        and application.assigned_to_id in (None, user.pk)
    )
