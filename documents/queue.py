import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from applications.models import ApplicationEvent

from .models import DocumentProcessingJob, SerializedDocument


def _locked_jobs(queryset):
    if connection.features.has_select_for_update_skip_locked:
        return queryset.select_for_update(skip_locked=True)
    return queryset.select_for_update()


@transaction.atomic
def claim_next_job(*, worker_id=""):
    now = timezone.now()
    DocumentProcessingJob.objects.filter(
        status=DocumentProcessingJob.Status.PROCESSING, lease_expires_at__lt=now,
        attempts__gte=F("max_attempts"),
    ).update(status=DocumentProcessingJob.Status.FAILED, last_error="Истёк срок последней попытки обработки",
             lease_token=None, lease_expires_at=None, claimed_by="", claimed_at=None)
    DocumentProcessingJob.objects.filter(
        status=DocumentProcessingJob.Status.PROCESSING,
        lease_expires_at__lt=now,
    ).update(
        status=DocumentProcessingJob.Status.PENDING,
        available_at=now,
        claimed_at=None,
        claimed_by="",
        lease_expires_at=None,
        lease_token=None,
        last_error="Истёк срок аренды задачи",
    )

    queryset = (
        DocumentProcessingJob.objects.filter(
            status=DocumentProcessingJob.Status.PENDING,
            available_at__lte=now,
            attempts__lt=F("max_attempts"),
        )
        .select_related("version__application", "version__product")
        .order_by("available_at", "created_at")
    )
    job = _locked_jobs(queryset).first()
    if job is None:
        return None

    job.status = DocumentProcessingJob.Status.PROCESSING
    job.attempts += 1
    job.claimed_at = now
    job.claimed_by = worker_id.strip()[:120]
    job.lease_token = uuid.uuid4()
    job.lease_expires_at = now + timedelta(seconds=settings.LLM_JOB_LEASE_SECONDS)
    job.last_error = ""
    job.save(
        update_fields=(
            "status",
            "attempts",
            "claimed_at",
            "claimed_by",
            "lease_token",
            "lease_expires_at",
            "last_error",
            "updated_at",
        )
    )
    return job


def _validate_active_lease(job, lease_token):
    try:
        supplied_token = uuid.UUID(str(lease_token))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValidationError("Некорректный lease_token") from error
    if job.lease_token != supplied_token:
        raise ValidationError("Задача арендована другим воркером")
    if job.lease_expires_at is None or job.lease_expires_at <= timezone.now():
        raise ValidationError("Срок аренды задачи истёк")


@transaction.atomic
def renew_job(*, job_id, lease_token):
    job = DocumentProcessingJob.objects.select_for_update().get(pk=job_id)
    if job.status != DocumentProcessingJob.Status.PROCESSING:
        raise ValidationError("Задача сейчас не обрабатывается")
    _validate_active_lease(job, lease_token)
    job.lease_expires_at = timezone.now() + timedelta(seconds=settings.LLM_JOB_LEASE_SECONDS)
    job.save(update_fields=("lease_expires_at", "updated_at"))
    return job


@transaction.atomic
def complete_job(*, job_id, lease_token, documents, summary=None):
    job = (
        DocumentProcessingJob.objects.select_for_update()
        .select_related("version__application")
        .get(pk=job_id)
    )
    if job.status == DocumentProcessingJob.Status.COMPLETED:
        if str(job.lease_token) == str(lease_token):
            return job
        raise ValidationError("Задача уже завершена")
    if job.status != DocumentProcessingJob.Status.PROCESSING:
        raise ValidationError("Задача сейчас не обрабатывается")
    _validate_active_lease(job, lease_token)

    source_documents = {str(item.pk): item for item in job.version.documents.all()}
    if not isinstance(documents, list):
        raise ValidationError("Поле documents должно быть массивом")
    received_ids = [str(item.get("document_id", "")) for item in documents if isinstance(item, dict)]
    if len(received_ids) != len(documents) or len(set(received_ids)) != len(received_ids):
        raise ValidationError("Каждый результат должен содержать уникальный document_id")
    if set(received_ids) != set(source_documents):
        raise ValidationError("Нужен результат для каждого документа задачи")

    for item in documents:
        payload = item.get("payload")
        if not isinstance(payload, dict):
            raise ValidationError("payload каждого документа должен быть JSON-объектом")
        confidence = item.get("confidence")
        if confidence is not None:
            try:
                confidence = Decimal(str(confidence))
            except InvalidOperation as error:
                raise ValidationError("confidence должен быть числом от 0 до 1") from error
            if not confidence.is_finite() or confidence < 0 or confidence > 1:
                raise ValidationError("confidence должен быть числом от 0 до 1")
        SerializedDocument.objects.create(
            job=job,
            source_document=source_documents[item["document_id"]],
            document_type=str(item.get("document_type", ""))[:100],
            schema_version=str(item.get("schema_version", "1.0"))[:32],
            confidence=confidence,
            payload=payload,
        )

    if summary is None:
        summary = {}
    if not isinstance(summary, dict):
        raise ValidationError("summary должен быть JSON-объектом")
    job.status = DocumentProcessingJob.Status.COMPLETED
    job.completed_at = timezone.now()
    job.result_summary = summary
    job.last_error = ""
    job.save(
        update_fields=(
            "status",
            "completed_at",
            "result_summary",
            "last_error",
            "updated_at",
        )
    )
    ApplicationEvent.objects.create(
        application=job.version.application,
        event_type=ApplicationEvent.Type.DOCUMENTS_PROCESSED,
        message=f"Обработано документов: {len(documents)}",
        metadata={"job_id": str(job.pk), "version": job.version.version_number},
    )
    return job


@transaction.atomic
def fail_job(*, job_id, lease_token, error_message, retryable=True):
    job = DocumentProcessingJob.objects.select_for_update().get(pk=job_id)
    if job.status != DocumentProcessingJob.Status.PROCESSING:
        raise ValidationError("Задача сейчас не обрабатывается")
    _validate_active_lease(job, lease_token)

    job.last_error = str(error_message).strip()[:4000] or "Ошибка обработки без описания"
    job.claimed_at = None
    job.claimed_by = ""
    job.lease_expires_at = None
    job.lease_token = None
    if retryable and job.attempts < job.max_attempts:
        job.status = DocumentProcessingJob.Status.PENDING
        job.available_at = timezone.now() + timedelta(seconds=settings.LLM_RETRY_DELAY_SECONDS)
    else:
        job.status = DocumentProcessingJob.Status.FAILED
    job.save(
        update_fields=(
            "status",
            "available_at",
            "claimed_at",
            "claimed_by",
            "lease_expires_at",
            "lease_token",
            "last_error",
            "updated_at",
        )
    )
    return job
