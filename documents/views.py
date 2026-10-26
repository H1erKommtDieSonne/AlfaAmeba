import json
import secrets
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import ApplicationDocument, DocumentProcessingJob
from .queue import claim_next_job, complete_job, fail_job, renew_job


@login_required
@require_GET
def download_document(request, document_id):
    from applications.permissions import can_access_business_application
    from users.permissions import can_review_applications

    document = get_object_or_404(ApplicationDocument.objects.select_related("version__application"), pk=document_id)
    if not (can_review_applications(request.user) or can_access_business_application(request.user, document.version.application)):
        raise PermissionDenied("Документ недоступен")
    document.file.open("rb")
    return FileResponse(document.file, as_attachment=True, filename=document.original_name,
                        content_type="application/octet-stream")


def _bearer_token(authorization):
    """Только точный префикс 'Bearer ' и непустой токен."""
    prefix = "Bearer "
    if not isinstance(authorization, str):
        return None
    authorization = authorization.strip()
    if not authorization.startswith(prefix):
        return None
    token = authorization[len(prefix) :]
    return token or None


def _tokens_equal(supplied, expected):
    if not isinstance(supplied, str) or not isinstance(expected, str):
        return False
    try:
        return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeError:
        return False


def llm_service_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        expected = settings.LLM_SERVICE_TOKEN
        if not expected:
            return JsonResponse({"error": "LLM service token is not configured"}, status=503)
        supplied = _bearer_token(request.headers.get("Authorization", ""))
        if not _tokens_equal(supplied, expected):
            return JsonResponse({"error": "Unauthorized"}, status=401)
        return view(request, *args, **kwargs)

    return wrapped


def _json_body(request, *, allow_empty=False):
    if allow_empty and not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValidationError("Тело запроса должно содержать корректный JSON") from error
    if not isinstance(payload, dict):
        raise ValidationError("Тело запроса должно быть JSON-объектом")
    return payload


def _validation_error(error):
    return JsonResponse({"error": "; ".join(error.messages)}, status=409)


@csrf_exempt
@require_POST
@llm_service_required
def claim_job(request):
    try:
        payload = _json_body(request, allow_empty=True)
    except ValidationError as error:
        return JsonResponse({"error": "; ".join(error.messages)}, status=400)
    worker_id = payload.get("worker_id", "")
    if not isinstance(worker_id, str):
        return JsonResponse({"error": "worker_id должен быть строкой"}, status=400)
    job = claim_next_job(worker_id=worker_id)
    if job is None:
        return HttpResponse(status=204)

    documents = [
        {
            "document_id": str(document.pk),
            "name": document.original_name,
            "requirement_code": document.requirement_code,
            "content_type": document.content_type,
            "size": document.size,
            "sha256": document.sha256,
            "download_url": request.build_absolute_uri(
                reverse("llm-job-document", args=(job.pk, document.pk))
            ),
        }
        for document in job.version.documents.all()
    ]
    return JsonResponse(
        {
            "job_id": str(job.pk),
            "lease_token": str(job.lease_token),
            "lease_expires_at": job.lease_expires_at.isoformat(),
            "attempt": job.attempts,
            "application_id": str(job.version.application_id),
            "version_id": str(job.version_id),
            "version_number": job.version.version_number,
            "application": job.version.snapshot,
            "documents": documents,
        }
    )


@require_GET
@llm_service_required
def download_job_document(request, job_id, document_id):
    document = get_object_or_404(
        ApplicationDocument.objects.select_related("version__processing_job"),
        pk=document_id,
        version__processing_job__pk=job_id,
    )
    job = document.version.processing_job
    supplied_lease = request.headers.get("X-Lease-Token", "").strip()
    if (
        job.status != DocumentProcessingJob.Status.PROCESSING
        or job.lease_expires_at is None
        or job.lease_expires_at <= timezone.now()
        or not supplied_lease
        or not _tokens_equal(supplied_lease, str(job.lease_token))
    ):
        return JsonResponse({"error": "Active lease required"}, status=409)
    document.file.open("rb")
    return FileResponse(
        document.file,
        as_attachment=True,
        filename=document.original_name,
        content_type=document.content_type,
    )


@csrf_exempt
@require_POST
@llm_service_required
def complete_job_view(request, job_id):
    try:
        payload = _json_body(request)
        job = complete_job(
            job_id=job_id,
            lease_token=payload.get("lease_token"),
            documents=payload.get("documents"),
            summary=payload.get("summary"),
        )
    except DocumentProcessingJob.DoesNotExist:
        return JsonResponse({"error": "Job not found"}, status=404)
    except ValidationError as error:
        return _validation_error(error)
    return JsonResponse(
        {
            "job_id": str(job.pk),
            "status": job.status,
            "completed_at": job.completed_at.isoformat(),
        }
    )


@csrf_exempt
@require_POST
@llm_service_required
def renew_job_view(request, job_id):
    try:
        payload = _json_body(request)
        job = renew_job(job_id=job_id, lease_token=payload.get("lease_token"))
    except DocumentProcessingJob.DoesNotExist:
        return JsonResponse({"error": "Job not found"}, status=404)
    except ValidationError as error:
        return _validation_error(error)
    return JsonResponse({"lease_expires_at": job.lease_expires_at.isoformat()})


@csrf_exempt
@require_POST
@llm_service_required
def fail_job_view(request, job_id):
    try:
        payload = _json_body(request)
        retryable = payload.get("retryable", True)
        if not isinstance(retryable, bool):
            return JsonResponse({"error": "retryable должен быть boolean"}, status=400)
        job = fail_job(
            job_id=job_id,
            lease_token=payload.get("lease_token"),
            error_message=payload.get("error", ""),
            retryable=retryable,
        )
    except DocumentProcessingJob.DoesNotExist:
        return JsonResponse({"error": "Job not found"}, status=404)
    except ValidationError as error:
        return _validation_error(error)
    return JsonResponse({"job_id": str(job.pk), "status": job.status})
