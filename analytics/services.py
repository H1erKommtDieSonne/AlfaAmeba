"""Канонические хэши, жизненный цикл очереди и строгая проверка demo-результата."""

import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from documents.models import DocumentProcessingJob, SerializedDocument

from .models import AnalysisRun

DEFAULT_MODEL_VERSION = "stub-1"
DEFAULT_POLICY_VERSION = "demo-1"

RESULT_SCHEMA_VERSION = "1.0"
RESULT_TOP_LEVEL_KEYS = {
    "schema_version",
    "is_demo",
    "model_version",
    "policy_version",
    "scenario_id",
    "recommendation",
    "summary",
    "reason_codes",
    "components",
}
COMPONENT_STATUSES = {
    "evidence": {"OK", "INCOMPLETE", "UNKNOWN"},
    "radar": {"OK", "MANUAL_REVIEW", "UNKNOWN"},
    "cashflow": {"OK", "UNKNOWN"},
    "scoring": {"OK", "UNKNOWN"},
}
SCENARIO_IDS = {"normal", "missing_data", "manual_review"}
RECOMMENDATIONS = {"PROCEED_TO_REVIEW", "REQUEST_INFORMATION", "MANUAL_REVIEW"}

SUMMARY_MAX_LENGTH = 1000
REASON_CODE_MAX_LENGTH = 200
REASON_CODES_MAX_COUNT = 20
COMPONENT_SUMMARY_MAX_LENGTH = 500


def _json_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"Значение типа {type(value).__name__} не сериализуется в JSON")


def canonical_json(value):
    """Единственное представление JSON для сравнения результатов и хэшей."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    )


def sha256_json(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_confidence(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValidationError("confidence должен быть числом от 0 до 1")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as error:  # Decimal кидает разные типы для мусорных строк.
        raise ValidationError("confidence должен быть числом от 0 до 1") from error
    if not number.is_finite() or number < 0 or number > 1:
        raise ValidationError("confidence должен быть числом от 0 до 1")
    return format(number, "f")


def build_policy_snapshot(version):
    """Снимок продуктовых условий на момент создания запуска."""
    product = version.product
    return {
        "code": product.code,
        "name": product.name,
        "description": product.description,
        "min_amount": format(product.min_amount, "f"),
        "max_amount": format(product.max_amount, "f"),
        "min_term_months": product.min_term_months,
        "max_term_months": product.max_term_months,
        "badge": product.badge,
        "active": bool(product.active),
    }


def _document_manifest(version):
    return [
        {
            "document_id": str(document.pk),
            "sha256": document.sha256,
            "size": document.size,
            "content_type": document.content_type,
            "original_name": document.original_name,
        }
        for document in version.documents.order_by("created_at", "pk")
    ]


def build_source_hash(
    version,
    policy_snapshot=None,
    *,
    model_version=DEFAULT_MODEL_VERSION,
    policy_version=DEFAULT_POLICY_VERSION,
):
    """Хэш входа до извлечения: версия, состав файлов, политика и манифест."""
    return sha256_json(
        {
            "schema_version": "analytics-source-1",
            "application_id": str(version.application_id),
            "version_id": str(version.pk),
            "version_number": version.version_number,
            "version_snapshot": version.snapshot or {},
            "policy_snapshot": policy_snapshot or build_policy_snapshot(version),
            "manifest": _document_manifest(version),
            "model_version": model_version,
            "policy_version": policy_version,
        }
    )


def extraction_status_for(version):
    job = (
        DocumentProcessingJob.objects.filter(version_id=version.pk)
        .values_list("status", flat=True)
        .first()
    )
    return job or ""


def documents_snapshot(version, extraction_status=""):
    """Документы версии без байтов; значения берутся только из извлечения."""
    serialized = {
        item.source_document_id: item
        for item in SerializedDocument.objects.filter(job__version_id=version.pk)
    }
    documents = []
    for document in version.documents.order_by("created_at", "pk"):
        item = serialized.get(document.pk)
        documents.append(
            {
                "document_id": str(document.pk),
                "document_type": item.document_type if item else "",
                "schema_version": item.schema_version if item else "1.0",
                "confidence": canonical_confidence(item.confidence) if item else None,
                "payload": item.payload if item else {},
            }
        )
    return documents


def build_input_hash(
    version,
    *,
    extraction_status,
    policy_snapshot=None,
    source_hash="",
    model_version=DEFAULT_MODEL_VERSION,
    policy_version=DEFAULT_POLICY_VERSION,
):
    """Итоговый канонический хэш: источник + фактическое извлечение."""
    policy = policy_snapshot or build_policy_snapshot(version)
    return sha256_json(
        {
            "schema_version": "analytics-input-1",
            "source_hash": source_hash
            or build_source_hash(
                version,
                policy,
                model_version=model_version,
                policy_version=policy_version,
            ),
            "application_id": str(version.application_id),
            "version_id": str(version.pk),
            "version_number": version.version_number,
            "version_snapshot": version.snapshot or {},
            "policy_snapshot": policy,
            "manifest": _document_manifest(version),
            "model_version": model_version,
            "policy_version": policy_version,
            "extraction_status": extraction_status,
            "documents": documents_snapshot(version, extraction_status),
        }
    )


def create_waiting_analysis_run(version):
    """Создать WAITING-запуск в транзакции сохранения документов версии."""
    policy_snapshot = build_policy_snapshot(version)
    run, _ = AnalysisRun.objects.get_or_create(
        version=version,
        model_version=DEFAULT_MODEL_VERSION,
        policy_version=DEFAULT_POLICY_VERSION,
        defaults={
            "status": AnalysisRun.Status.WAITING,
            "policy_snapshot": policy_snapshot,
            "source_hash": build_source_hash(
                version,
                policy_snapshot,
                model_version=DEFAULT_MODEL_VERSION,
                policy_version=DEFAULT_POLICY_VERSION,
            ),
        },
    )
    return run


def current_analysis_run(application):
    """Текущий запуск ровно для current_version; порядок задан явно."""
    return (
        AnalysisRun.objects.filter(
            version__application_id=application.pk,
            version__version_number=application.current_version,
        )
        .order_by("-created_at", "-pk")
        .first()
    )


def _locked(queryset):
    """Блокировать только строки очереди, не присоединённые job/application."""
    features = connection.features
    if features.has_select_for_update_of:
        options = {"of": ("self",)}
        if features.has_select_for_update_skip_locked:
            options["skip_locked"] = True
        return queryset.select_for_update(**options)
    if features.has_select_for_update_skip_locked:
        return queryset.select_for_update(skip_locked=True)
    return queryset.select_for_update()


def _release_expired_lease(run, now):
    run.claimed_at = None
    run.claimed_by = ""
    run.lease_expires_at = None
    run.lease_token = None
    run.last_error = "Истёк срок аренды задачи аналитики"
    if run.attempts >= run.max_attempts:
        run.status = AnalysisRun.Status.FAILED
        run.completed_at = now
    else:
        run.status = AnalysisRun.Status.PENDING
        run.available_at = now
    run.save(
        update_fields=(
            "status",
            "available_at",
            "claimed_at",
            "claimed_by",
            "lease_expires_at",
            "lease_token",
            "last_error",
            "completed_at",
            "updated_at",
        )
    )


def _resolve_waiting_runs(now):
    waiting = _locked(
        AnalysisRun.objects.filter(
            status=AnalysisRun.Status.WAITING,
            version__processing_job__status__in=(
                DocumentProcessingJob.Status.COMPLETED,
                DocumentProcessingJob.Status.FAILED,
            ),
        ).select_related("version__processing_job")
    )
    for run in waiting:
        run.status = AnalysisRun.Status.PENDING
        run.extraction_status = run.version.processing_job.status
        run.available_at = now
        run.last_error = ""
        run.save(
            update_fields=(
                "status",
                "extraction_status",
                "available_at",
                "last_error",
                "updated_at",
            )
        )


@transaction.atomic
def claim_next_run(*, worker_id):
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValidationError("worker_id должен быть непустой строкой")
    worker_id = worker_id.strip()
    if len(worker_id) > 120:
        raise ValidationError("worker_id длиннее 120 символов")
    now = timezone.now()
    stale = _locked(
        AnalysisRun.objects.filter(
            status=AnalysisRun.Status.PROCESSING,
            lease_expires_at__lt=now,
        )
    )
    for run in stale:
        _release_expired_lease(run, now)

    _resolve_waiting_runs(now)

    queryset = (
        AnalysisRun.objects.filter(
            status=AnalysisRun.Status.PENDING,
            available_at__lte=now,
            attempts__lt=F("max_attempts"),
        )
        .select_related("version__application", "version__product")
        .order_by("available_at", "created_at", "pk")
    )
    run = _locked(queryset).first()
    if run is None:
        return None

    if not run.extraction_status:
        run.extraction_status = extraction_status_for(run.version)
    run.input_hash = build_input_hash(
        run.version,
        extraction_status=run.extraction_status,
        policy_snapshot=run.policy_snapshot,
        source_hash=run.source_hash,
        model_version=run.model_version,
        policy_version=run.policy_version,
    )
    run.status = AnalysisRun.Status.PROCESSING
    run.attempts += 1
    run.claimed_at = now
    run.claimed_by = worker_id
    run.lease_token = uuid.uuid4()
    run.lease_expires_at = now + timedelta(seconds=settings.ANALYTICS_JOB_LEASE_SECONDS)
    run.last_error = ""
    run.save(
        update_fields=(
            "status",
            "attempts",
            "claimed_at",
            "claimed_by",
            "lease_token",
            "lease_expires_at",
            "extraction_status",
            "input_hash",
            "last_error",
            "updated_at",
        )
    )
    return run


def serialize_claim(run):
    version = run.version
    return {
        "job_id": str(run.pk),
        "lease_token": str(run.lease_token),
        "lease_expires_at": run.lease_expires_at.isoformat(),
        "attempt": run.attempts,
        "application_id": str(version.application_id),
        "version_id": str(version.pk),
        "version_number": version.version_number,
        "application": version.snapshot or {},
        "input_hash": run.input_hash,
        "documents": documents_snapshot(version, run.extraction_status),
        "extraction_status": run.extraction_status,
        "model_version": run.model_version,
        "policy_version": run.policy_version,
    }


def _validate_lease_token(run, lease_token, *, require_active):
    try:
        supplied = uuid.UUID(str(lease_token))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValidationError("Некорректный lease_token") from error
    if run.lease_token != supplied:
        raise ValidationError("Запуск арендован другим воркером")
    if require_active and (
        run.lease_expires_at is None or run.lease_expires_at <= timezone.now()
    ):
        raise ValidationError("Срок аренды запуска истёк")


def _validate_text(value, *, field, max_length, allow_empty=False):
    if not isinstance(value, str):
        raise ValidationError(f"{field} должен быть строкой")
    if not allow_empty and not value.strip():
        raise ValidationError(f"{field} не может быть пустым")
    if len(value) > max_length:
        raise ValidationError(f"{field} длиннее {max_length} символов")
    return value


def validate_result(result, run):
    """Строгая схема demo-ответа: без PD, финансовых гарантий и лишних полей."""
    if not isinstance(result, dict):
        raise ValidationError("result должен быть JSON-объектом")
    if set(result) != RESULT_TOP_LEVEL_KEYS:
        raise ValidationError("result не соответствует схеме 1.0")
    if result["schema_version"] != RESULT_SCHEMA_VERSION:
        raise ValidationError("Поддерживается только result.schema_version='1.0'")
    if result["is_demo"] is not True:
        raise ValidationError("result.is_demo должен быть true")
    if result["model_version"] != run.model_version:
        raise ValidationError("result.model_version не совпадает с запуском")
    if result["policy_version"] != run.policy_version:
        raise ValidationError("result.policy_version не совпадает с запуском")
    scenario_id = result["scenario_id"]
    if not isinstance(scenario_id, str) or scenario_id not in SCENARIO_IDS:
        raise ValidationError("Недопустимый scenario_id")
    recommendation = result["recommendation"]
    if not isinstance(recommendation, str) or recommendation not in RECOMMENDATIONS:
        raise ValidationError("Недопустимый recommendation")
    _validate_text(
        result["summary"], field="summary", max_length=SUMMARY_MAX_LENGTH
    )

    reason_codes = result["reason_codes"]
    if not isinstance(reason_codes, list) or len(reason_codes) > REASON_CODES_MAX_COUNT:
        raise ValidationError(
            f"reason_codes должен быть массивом не длиннее {REASON_CODES_MAX_COUNT}"
        )
    for code in reason_codes:
        _validate_text(code, field="reason_codes[]", max_length=REASON_CODE_MAX_LENGTH)

    components = result["components"]
    if not isinstance(components, dict) or set(components) != set(COMPONENT_STATUSES):
        raise ValidationError("components должен содержать evidence/radar/cashflow/scoring")
    for name, allowed_statuses in COMPONENT_STATUSES.items():
        component = components[name]
        if not isinstance(component, dict) or set(component) != {"status", "summary"}:
            raise ValidationError(f"components.{name} должен содержать status и summary")
        status = component["status"]
        if not isinstance(status, str) or status not in allowed_statuses:
            raise ValidationError(f"Недопустимый status компонента {name}")
        _validate_text(
            component["summary"],
            field=f"components.{name}.summary",
            max_length=COMPONENT_SUMMARY_MAX_LENGTH,
            allow_empty=True,
        )

    try:
        # Заодно отсекает NaN/Infinity и не-JSON типы, сравнивать будем канонически.
        return json.loads(canonical_json(result))
    except (TypeError, ValueError) as error:
        raise ValidationError("result должен содержать корректный JSON") from error


def _result_status(result):
    return (
        AnalysisRun.Status.READY
        if all(item["status"] == "OK" for item in result["components"].values())
        else AnalysisRun.Status.PARTIAL
    )


@transaction.atomic
def complete_run(*, run_id, lease_token, input_hash, result):
    run = (
        AnalysisRun.objects.select_for_update()
        .select_related("version__application", "version__product")
        .get(pk=run_id)
    )

    if run.status in (AnalysisRun.Status.READY, AnalysisRun.Status.PARTIAL):
        _validate_lease_token(run, lease_token, require_active=False)
        try:
            incoming = json.loads(canonical_json(result))
        except (TypeError, ValueError) as error:
            raise ValidationError("result должен содержать корректный JSON") from error
        if input_hash != run.input_hash or incoming != run.result:
            raise ValidationError("Запуск уже завершён с другим результатом")
        return run

    if run.status != AnalysisRun.Status.PROCESSING:
        raise ValidationError("Запуск сейчас не обрабатывается")
    _validate_lease_token(run, lease_token, require_active=True)
    if not isinstance(input_hash, str) or not input_hash:
        raise ValidationError("input_hash обязателен")
    if input_hash != run.input_hash:
        raise ValidationError("input_hash не совпадает с выданным заданием")

    expected_hash = build_input_hash(
        run.version,
        extraction_status=run.extraction_status,
        policy_snapshot=run.policy_snapshot,
        source_hash=run.source_hash,
        model_version=run.model_version,
        policy_version=run.policy_version,
    )
    if expected_hash != run.input_hash:
        raise ValidationError("Входные данные изменились после выдачи задания")

    validated = validate_result(result, run)
    run.result = validated
    run.status = _result_status(validated)
    run.completed_at = timezone.now()
    run.lease_expires_at = None
    run.last_error = ""
    run.save(
        update_fields=(
            "result",
            "status",
            "completed_at",
            "lease_expires_at",
            "last_error",
            "updated_at",
        )
    )
    return run


@transaction.atomic
def fail_run(*, run_id, lease_token, error_message, retryable=True):
    if not isinstance(retryable, bool):
        raise ValidationError("retryable должен быть boolean")
    run = AnalysisRun.objects.select_for_update().get(pk=run_id)
    if run.status != AnalysisRun.Status.PROCESSING:
        raise ValidationError("Запуск сейчас не обрабатывается")
    _validate_lease_token(run, lease_token, require_active=True)

    run.last_error = str(error_message).strip()[:4000] or "Ошибка аналитики без описания"
    run.claimed_at = None
    run.claimed_by = ""
    run.lease_expires_at = None
    run.lease_token = None
    if retryable and run.attempts < run.max_attempts:
        run.status = AnalysisRun.Status.PENDING
        run.available_at = timezone.now() + timedelta(
            seconds=settings.ANALYTICS_RETRY_DELAY_SECONDS
        )
    else:
        run.status = AnalysisRun.Status.FAILED
        run.completed_at = timezone.now()
    run.save(
        update_fields=(
            "status",
            "available_at",
            "claimed_at",
            "claimed_by",
            "lease_expires_at",
            "lease_token",
            "last_error",
            "completed_at",
            "updated_at",
        )
    )
    return run
