import json
import secrets
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import ApplicationDocument, DocumentProcessingJob
from .queue import claim_next_job, complete_job, fail_job


def llm_service_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        expected = settings.LLM_SERVICE_TOKEN
        authorization = request.headers.get("Authorization", "")
        supplied = authorization.removeprefix("Bearer ").strip()
        if not expected:
            return JsonResponse({"error": "LLM service token is not configured"}, status=503)
        if not supplied or not secrets.compare_digest(supplied, expected):
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
    job = claim_next_job(worker_id=payload.get("worker_id", ""))
    if job is None:
        return HttpResponse(status=204)

    documents = [
        {
            "document_id": str(document.pk),
            "name": document.original_name,
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
        or not secrets.compare_digest(supplied_lease, str(job.lease_token))
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
def fail_job_view(request, job_id):
    try:
        payload = _json_body(request)
        job = fail_job(
            job_id=job_id,
            lease_token=payload.get("lease_token"),
            error_message=payload.get("error", ""),
            retryable=payload.get("retryable", True) is not False,
        )
    except DocumentProcessingJob.DoesNotExist:
        return JsonResponse({"error": "Job not found"}, status=404)
    except ValidationError as error:
        return _validation_error(error)
    return JsonResponse({"job_id": str(job.pk), "status": job.status})
