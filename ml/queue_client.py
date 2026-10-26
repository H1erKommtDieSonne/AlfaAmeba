"""Pull-клиенты очередей Django для ML/LLM-воркера.

Только стандартная библиотека. Токены и адреса передаются явно через
переменные окружения; клиент не логирует секреты и тела документов.
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_TIMEOUT_SECONDS = 30
_MAX_ERROR_BODY = 300


class QueueClientError(RuntimeError):
    """Базовая ошибка pull-клиента."""


class ConfigurationClientError(QueueClientError):
    """401/503 или некорректная конфигурация: повторять бессмысленно."""


class PermanentClientError(QueueClientError, ValueError):
    """4xx/проверка данных: задача не станет корректной сама."""


class TransientClientError(QueueClientError):
    """Сеть, таймаут, 5xx: разумно повторить позже."""


class DocumentVerificationError(QueueClientError):
    """Скачанный файл не совпал с размером или SHA-256 из claim."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """urllib по умолчанию отправляет Authorization на redirect. Запрещаем это."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def validate_base_url(url, *, env_name):
    if not isinstance(url, str) or not url.strip():
        raise ConfigurationClientError(f"{env_name} не задан")
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ConfigurationClientError(f"{env_name} должен начинаться с http:// или https://")
    if not parsed.hostname:
        raise ConfigurationClientError(f"{env_name} должен содержать хост")
    if parsed.username or parsed.password:
        raise ConfigurationClientError(f"{env_name} не должен содержать логин или пароль")
    if parsed.query or parsed.fragment:
        raise ConfigurationClientError(f"{env_name} не должен содержать query или fragment")
    return url.strip().rstrip("/")


def _origin(url):
    parsed = urlsplit(url)
    default_port = 443 if parsed.scheme == "https" else 80
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or default_port,
    )


def _validate_download_url(base_url, download_url):
    if not isinstance(download_url, str) or not download_url:
        raise PermanentClientError("download_url отсутствует")
    try:
        parsed = urlsplit(download_url)
        origin = _origin(download_url)
    except ValueError as error:
        raise PermanentClientError("Некорректный download_url") from error
    if parsed.scheme not in ("http", "https"):
        raise PermanentClientError("download_url должен быть http(s)")
    if parsed.username or parsed.password:
        raise PermanentClientError("download_url не должен содержать логин или пароль")
    if origin != _origin(base_url):
        raise PermanentClientError("download_url вне origin сервиса Django")
    decoded_segments = unquote(parsed.path).split("/")
    if any(segment in (".", "..") for segment in decoded_segments):
        raise PermanentClientError("download_url содержит недопустимый путь")
    queue_path = urlsplit(base_url).path.rstrip("/") + "/jobs/"
    if not parsed.path.startswith(queue_path):
        raise PermanentClientError(
            "download_url вне пути /jobs/ очереди документов"
        )
    return download_url


def _short_error_body(error):
    try:
        raw = error.read(_MAX_ERROR_BODY)
        text = raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            text = payload["error"]
    except (ValueError, TypeError):
        pass
    return text[:_MAX_ERROR_BODY]


class BaseQueueClient:
    """Общая часть claim/fail и HTTP-ошибок для двух очередей."""

    env_url_name = ""
    env_token_name = ""

    def __init__(self, base_url=None, token=None, timeout=None):
        raw_url = base_url or os.environ.get(self.env_url_name, "")
        raw_token = token or os.environ.get(self.env_token_name, "")
        self.base_url = validate_base_url(raw_url, env_name=self.env_url_name)
        if not isinstance(raw_token, str) or not raw_token.strip():
            raise ConfigurationClientError(f"{self.env_token_name} не задан")
        self.token = raw_token.strip()
        if timeout is None:
            timeout = DEFAULT_TIMEOUT_SECONDS
        try:
            timeout = int(timeout)
        except (TypeError, ValueError) as error:
            raise ConfigurationClientError("Таймаут должен быть целым числом секунд") from error
        if timeout <= 0 or timeout > 300:
            raise ConfigurationClientError("Таймаут должен быть от 1 до 300 секунд")
        self.timeout = timeout
        # Local queue traffic must not be sent through an OS HTTP proxy.
        handlers = [_NoRedirectHandler]
        if urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
            handlers.append(ProxyHandler({}))
        self._opener = build_opener(*handlers)
        self.opener = self._opener  # backwards-compatible public alias

    def _open(self, request):
        try:
            return self._opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            body = _short_error_body(error)
            suffix = f": {body}" if body else ""
            if error.code in (401, 403, 503):
                raise ConfigurationClientError(
                    f"HTTP {error.code}: проверьте {self.env_token_name} и URL{suffix}"
                ) from error
            if error.code >= 500 or error.code in (408, 429):
                raise TransientClientError(f"HTTP {error.code}{suffix}") from error
            raise PermanentClientError(f"HTTP {error.code}{suffix}") from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise TransientClientError(f"Сеть недоступна: {reason}") from error

    def _json_request(self, path, *, method="GET", payload=None, lease_token=None):
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if lease_token:
            headers["X-Lease-Token"] = lease_token
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=data,
            headers=headers,
            method=method,
        )
        response = self._open(request)
        with response:
            if response.status == 204:
                return None
            try:
                return json.load(response)
            except (ValueError, UnicodeDecodeError) as error:
                raise PermanentClientError("Django вернул некорректный JSON") from error

    def claim(self, worker_id):
        return self._json_request(
            "jobs/claim/", method="POST", payload={"worker_id": worker_id}
        )

    def fail(self, job_id, lease_token, error, *, retryable=True):
        if not isinstance(retryable, bool):
            raise PermanentClientError("retryable должен быть boolean")
        return self._json_request(
            f"jobs/{job_id}/fail/",
            method="POST",
            payload={
                "lease_token": lease_token,
                "error": str(error)[:4000],
                "retryable": retryable,
            },
        )


class QueueClient(BaseQueueClient):
    """Очередь документов: /api/v1/llm (совместим с прежним API)."""

    env_url_name = "DJANGO_QUEUE_URL"
    env_token_name = "LLM_SERVICE_TOKEN"

    def download(self, download_url, lease_token):
        safe_url = _validate_download_url(self.base_url, download_url)
        request = Request(
            safe_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Lease-Token": str(lease_token),
            },
            method="GET",
        )
        response = self._open(request)
        with response:
            if response.status != 200:
                raise TransientClientError(f"HTTP {response.status} при скачивании")
            try:
                content = response.read(20 * 1024 * 1024 + 1)
            except (TimeoutError, OSError) as error:
                raise TransientClientError("Не удалось дочитать документ") from error
            if len(content) > 20 * 1024 * 1024:
                raise PermanentClientError("Документ превышает лимит скачивания 20 МБ")
            return content

    def renew(self, job_id, lease_token):
        return self._json_request(
            f"jobs/{job_id}/renew/",
            method="POST",
            payload={"lease_token": lease_token},
        )

    def complete(self, job_id, lease_token, documents, summary=None):
        return self._json_request(
            f"jobs/{job_id}/complete/",
            method="POST",
            payload={
                "lease_token": lease_token,
                "documents": documents,
                "summary": summary or {},
            },
        )


class AnalyticsQueueClient(BaseQueueClient):
    """Очередь аналитики: /api/v1/analytics."""

    env_url_name = "DJANGO_ANALYTICS_URL"
    env_token_name = "ANALYTICS_SERVICE_TOKEN"

    def complete_analysis(self, job_id, lease_token, input_hash, result):
        return self._json_request(
            f"jobs/{job_id}/complete/",
            method="POST",
            payload={
                "lease_token": lease_token,
                "input_hash": input_hash,
                "result": result,
            },
        )
