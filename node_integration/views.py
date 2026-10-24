import json
import re
import secrets
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import NodeJob
from .services import (
    ALLOWED_VERIFICATION_STATUSES,
    CALLBACK_COMPLETED,
    FEEDBACK_ACCEPTABLE_STATUSES,
    NodeFeedbackConflict,
    NodeJobConflict,
    canonical_json,
    complete_node_job,
    store_node_feedback,
)

MAX_CALLBACK_BYTES = 2 * 1024 * 1024
# Замечания узла — маленькие структурированные тела, отдельный лимит.
FEEDBACK_MAX_BYTES = 256 * 1024
FEEDBACK_MAX_ISSUES = 50
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
FEEDBACK_TOP_KEYS = frozenset({"feedback_id", "job_id", "issues"})
FEEDBACK_ISSUE_KEYS = frozenset(
    {"code", "message", "field", "document_id", "filename", "suggestion"}
)


class BadRequest(Exception):
    """Тело или поля callback не соответствуют контракту."""


class PayloadTooLarge(Exception):
    """Тело callback больше допустимых 2 MiB."""


def _bearer_token(authorization):
    """Только точный префикс 'Bearer ' и непустой токен."""
    if not isinstance(authorization, str):
        return None
    authorization = authorization.strip()
    prefix = "Bearer "
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


def _read_limited_body(request, limit=MAX_CALLBACK_BYTES):
    """Прочитать тело не больше лимита через ограниченный поток Django.

    request.read() читает из WSGIRequest._stream (LimitedStream), поэтому
    никогда не выходит за Content-Length. Без Content-Length поток пуст:
    сырой wsgi.input не трогаем, чтобы не блокироваться на чтении.
    """
    content_length = request.META.get("CONTENT_LENGTH")
    if content_length not in (None, ""):
        try:
            length = int(content_length)
        except (TypeError, ValueError) as error:
            raise BadRequest from error
        if length < 0:
            raise BadRequest
        if length > limit:
            raise PayloadTooLarge

    chunks = []
    remaining = limit + 1
    try:
        while remaining > 0:
            chunk = request.read(min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except (OSError, ValueError) as error:
        raise BadRequest from error

    body = b"".join(chunks)
    if len(body) > limit:
        raise PayloadTooLarge
    return body


def _reject_constant(value):
    # json.loads по умолчанию принимает NaN/Infinity, а это не корректный JSON.
    raise ValueError(f"Недопустимое число {value}")


def _reject_duplicate_keys(pairs):
    """Дубли ключей в одном JSON-объекте неоднозначны — отклоняем."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Дублирующийся ключ {key}")
        result[key] = value
    return result


def _parse_json(body, *, object_pairs_hook=None):
    if not body:
        raise BadRequest
    try:
        payload = json.loads(
            body, parse_constant=_reject_constant, object_pairs_hook=object_pairs_hook
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise BadRequest from error
    if not isinstance(payload, dict):
        raise BadRequest
    return payload


def _validate_callback(request, payload):
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise BadRequest

    for header in ("X-Job-ID", "Idempotency-Key"):
        if header in request.headers and request.headers.get(header) != job_id:
            raise BadRequest

    if payload.get("status") != CALLBACK_COMPLETED:
        raise BadRequest
    if not isinstance(payload.get("result"), dict):
        raise BadRequest
    if not isinstance(payload.get("files"), list):
        raise BadRequest
    verification_status = payload.get("verification_status")
    if (
        not isinstance(verification_status, str)
        or verification_status not in ALLOWED_VERIFICATION_STATUSES
    ):
        raise BadRequest
    if "model" in payload and not isinstance(payload["model"], str):
        raise BadRequest


def _feedback_job_id(payload):
    """Проверить оболочку замечаний и вернуть job_id для поиска задачи."""
    if set(payload) != FEEDBACK_TOP_KEYS:
        raise BadRequest
    feedback_id = payload.get("feedback_id")
    if not isinstance(feedback_id, str):
        raise BadRequest
    try:
        uuid.UUID(feedback_id)
    except (ValueError, AttributeError, TypeError) as error:
        raise BadRequest from error
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise BadRequest
    issues = payload.get("issues")
    if not isinstance(issues, list) or not 1 <= len(issues) <= FEEDBACK_MAX_ISSUES:
        raise BadRequest
    return job_id


def _optional_issue_text(issue, key, maximum):
    value = issue.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadRequest
    value = value.strip()
    if not value or len(value) > maximum:
        raise BadRequest
    return value


def _normalize_issue(issue, valid_document_ids):
    """Проверить замечание и оставить только отображаемые безопасные поля."""
    if not isinstance(issue, dict) or not set(issue) <= FEEDBACK_ISSUE_KEYS:
        raise BadRequest

    code = issue.get("code")
    if not isinstance(code, str) or CODE_PATTERN.fullmatch(code.strip()) is None:
        raise BadRequest
    code = code.strip()

    message = issue.get("message")
    if not isinstance(message, str):
        raise BadRequest
    message = message.strip()
    if not message or len(message) > 1000:
        raise BadRequest

    normalized = {"code": code, "message": message}
    for key, maximum in (("field", 120), ("filename", 255), ("suggestion", 1000)):
        text = _optional_issue_text(issue, key, maximum)
        if text is not None:
            normalized[key] = text

    document_id = issue.get("document_id")
    if document_id is not None:
        if not isinstance(document_id, str):
            raise BadRequest
        try:
            parsed = uuid.UUID(document_id)
        except (ValueError, AttributeError, TypeError) as error:
            raise BadRequest from error
        # Ссылаться можно только на документ этой же версии; filename не адресует.
        if str(parsed) not in valid_document_ids:
            raise BadRequest
        normalized["document_id"] = str(parsed)
    return normalized


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


@csrf_exempt
@require_POST
def callback(request):
    """Callback внешнего узла: только завершённый результат, без сессии и CSRF."""
    expected = settings.DOCUMENT_NODE_CALLBACK_TOKEN
    if not expected:
        return _error("Service unavailable", 503)
    supplied = _bearer_token(request.headers.get("Authorization", ""))
    if not _tokens_equal(supplied, expected):
        return _error("Unauthorized", 401)

    if request.content_type != "application/json":
        return _error("Unsupported media type", 415)

    try:
        payload = _parse_json(_read_limited_body(request))
        _validate_callback(request, payload)
        try:
            # Заодно проверяем, что строки кодируются в UTF-8: json.loads
            # принимает одиночные surrogate-escape, а сохранять их нельзя.
            canonical_json(payload).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as error:
            raise BadRequest from error
        job, _ = complete_node_job(payload)
    except PayloadTooLarge:
        return _error("Payload too large", 413)
    except BadRequest:
        return _error("Bad request", 400)
    except NodeJob.DoesNotExist:
        return _error("Not found", 404)
    except NodeJobConflict:
        return _error("Conflict", 409)

    return JsonResponse({"job_id": job.external_job_id, "status": job.status})


@csrf_exempt
@require_POST
def feedback(request):
    """Структурированные замечания узла по документам версии заявки.

    Узел обязан вызвать этот endpoint сам: Django не извлекает замечания из
    произвольного результата callback и не меняет статусы/решения заявки.
    """
    expected = settings.DOCUMENT_NODE_CALLBACK_TOKEN
    if not expected:
        return _error("Service unavailable", 503)
    supplied = _bearer_token(request.headers.get("Authorization", ""))
    if not _tokens_equal(supplied, expected):
        return _error("Unauthorized", 401)

    if request.content_type != "application/json":
        return _error("Unsupported media type", 415)

    try:
        payload = _parse_json(
            _read_limited_body(request, FEEDBACK_MAX_BYTES),
            object_pairs_hook=_reject_duplicate_keys,
        )
        job_id = _feedback_job_id(payload)
        try:
            # Заодно отсеиваем одиночные surrogate-escape: их нельзя хранить в UTF-8.
            canonical_json(payload).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as error:
            raise BadRequest from error
        job = NodeJob.objects.filter(external_job_id=job_id).first()
        if job is None:
            raise NodeJob.DoesNotExist
        if job.status not in FEEDBACK_ACCEPTABLE_STATUSES:
            raise NodeJobConflict
        valid_document_ids = {
            str(pk) for pk in job.version.documents.values_list("id", flat=True)
        }
        issues = [_normalize_issue(item, valid_document_ids) for item in payload["issues"]]
        stored, _ = store_node_feedback(payload, job=job, issues=issues)
    except PayloadTooLarge:
        return _error("Payload too large", 413)
    except BadRequest:
        return _error("Bad request", 400)
    except NodeJob.DoesNotExist:
        return _error("Not found", 404)
    except (NodeJobConflict, NodeFeedbackConflict):
        return _error("Conflict", 409)

    return JsonResponse(
        {
            "feedback_id": str(stored.feedback_id),
            "job_id": stored.job.external_job_id,
            "issues_count": len(stored.issues),
        }
    )
