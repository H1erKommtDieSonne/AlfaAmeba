import json
import secrets
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import AnalysisRun
from .services import claim_next_run, complete_run, fail_run, serialize_claim


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


def analytics_service_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        expected = settings.ANALYTICS_SERVICE_TOKEN
        if not expected:
            return JsonResponse({"error": "Analytics service token is not configured"}, status=503)
        supplied = _bearer_token(request.headers.get("Authorization", ""))
        if not _tokens_equal(supplied, expected):
            return JsonResponse({"error": "Unauthorized"}, status=401)
        return view(request, *args, **kwargs)

    return wrapped


def _json_body(request):
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValidationError("Тело запроса должно содержать корректный JSON") from error
    if not isinstance(payload, dict):
        raise ValidationError("Тело запроса должно быть JSON-объектом")
    return payload


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _validation_message(error):
    return "; ".join(error.messages)


@csrf_exempt
@require_POST
@analytics_service_required
def claim_job(request):
    try:
        payload = _json_body(request)
    except ValidationError as error:
        return _error(_validation_message(error), 400)

    worker_id = payload.get("worker_id")
    if not isinstance(worker_id, str) or not worker_id.strip():
        return _error("worker_id должен быть непустой строкой", 400)
    worker_id = worker_id.strip()
    if len(worker_id) > 120:
        return _error("worker_id длиннее 120 символов", 400)

    run = claim_next_run(worker_id=worker_id)
    if run is None:
        return HttpResponse(status=204)
    return JsonResponse(serialize_claim(run))


@csrf_exempt
@require_POST
@analytics_service_required
def complete_job_view(request, run_id):
    try:
        payload = _json_body(request)
    except ValidationError as error:
        return _error(_validation_message(error), 400)

    try:
        run = complete_run(
            run_id=run_id,
            lease_token=payload.get("lease_token"),
            input_hash=payload.get("input_hash"),
            result=payload.get("result"),
        )
    except AnalysisRun.DoesNotExist:
        return _error("Analysis run not found", 404)
    except ValidationError as error:
        return _error(_validation_message(error), 409)

    return JsonResponse(
        {
            "job_id": str(run.pk),
            "status": run.status,
            "input_hash": run.input_hash,
            "completed_at": run.completed_at.isoformat(),
        }
    )


@csrf_exempt
@require_POST
@analytics_service_required
def fail_job_view(request, run_id):
    try:
        payload = _json_body(request)
    except ValidationError as error:
        return _error(_validation_message(error), 400)

    retryable = payload.get("retryable", True)
    if not isinstance(retryable, bool):
        return _error("retryable должен быть boolean", 400)
    error_message = payload.get("error", "")
    if not isinstance(error_message, str):
        return _error("error должен быть строкой", 400)

    try:
        run = fail_run(
            run_id=run_id,
            lease_token=payload.get("lease_token"),
            error_message=error_message,
            retryable=retryable,
        )
    except AnalysisRun.DoesNotExist:
        return _error("Analysis run not found", 404)
    except ValidationError as error:
        return _error(_validation_message(error), 409)

    return JsonResponse({"job_id": str(run.pk), "status": run.status})
