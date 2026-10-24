"""Локальный stub-воркер: извлечение документов и demo-аналитика.

Запуск из корня репозитория:
    python -m ml.worker --stage all
    python -m ml.worker --stage documents --once

В контейнере тот же код лежит как /app/worker.py.
"""

import argparse
import logging
import os
import platform
import signal
import socket
import sys
import time
from dataclasses import dataclass

from .queue_client import (
    AnalyticsQueueClient,
    ConfigurationClientError,
    PermanentClientError,
    QueueClient,
    TransientClientError,
    DocumentVerificationError,
)
from .stubs import (
    VALID_SCENARIOS,
    build_analysis_result,
    build_document_result,
    extraction_summary,
)

LOG = logging.getLogger("ml.worker")
STAGES = ("documents", "analytics", "all")
DEFAULT_POLL_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 30


class ConfigError(ValueError):
    """Понятная ошибка конфигурации: воркер не должен стартовать."""


@dataclass(frozen=True)
class WorkerConfig:
    stage: str
    scenario: str
    poll_seconds: int
    worker_id: str
    http_timeout: int
    queue_url: str = ""
    llm_token: str = ""
    analytics_url: str = ""
    analytics_token: str = ""


def _int_env(env, name, default, *, maximum):
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{name} должен быть целым числом") from error
    if value <= 0 or value > maximum:
        raise ConfigError(f"{name} должен быть от 1 до {maximum}")
    return value


def _required(env, name):
    value = env.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} не задан")
    return value.strip()


def _default_worker_id(stage):
    host = (platform.node() or socket.gethostname() or "host")[:60]
    return f"ml-worker-{stage}-{host}-{os.getpid()}"[:120]


def load_config(stage, env=None):
    env = os.environ if env is None else env
    if stage not in STAGES:
        raise ConfigError(f"stage должен быть одним из: {', '.join(STAGES)}")

    scenario = env.get("DEMO_SCENARIO", "normal")
    if scenario is None or not str(scenario).strip():
        raise ConfigError("DEMO_SCENARIO не задан")
    scenario = str(scenario).strip()
    if scenario not in VALID_SCENARIOS:
        raise ConfigError(
            "DEMO_SCENARIO должен быть одним из: " + ", ".join(VALID_SCENARIOS)
        )

    poll_seconds = _int_env(
        env, "WORKER_POLL_SECONDS", DEFAULT_POLL_SECONDS, maximum=86400
    )
    http_timeout = _int_env(
        env, "WORKER_HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, maximum=300
    )

    worker_id = env.get("WORKER_ID", "")
    if worker_id is None or not str(worker_id).strip():
        worker_id = _default_worker_id(stage)
    else:
        worker_id = str(worker_id).strip()
    if len(worker_id) > 120:
        raise ConfigError("WORKER_ID длиннее 120 символов")

    need_documents = stage in ("documents", "all")
    need_analytics = stage in ("analytics", "all")

    queue_url = _required(env, "DJANGO_QUEUE_URL") if need_documents else ""
    llm_token = _required(env, "LLM_SERVICE_TOKEN") if need_documents else ""
    analytics_url = (
        _required(env, "DJANGO_ANALYTICS_URL") if need_analytics else ""
    )
    analytics_token = (
        _required(env, "ANALYTICS_SERVICE_TOKEN") if need_analytics else ""
    )

    return WorkerConfig(
        stage=stage,
        scenario=scenario,
        poll_seconds=poll_seconds,
        worker_id=worker_id,
        http_timeout=http_timeout,
        queue_url=queue_url,
        llm_token=llm_token,
        analytics_url=analytics_url,
        analytics_token=analytics_token,
    )


class Worker:
    def __init__(self, config, document_client=None, analytics_client=None):
        self.config = config
        self.stop_requested = False
        self.document_client = document_client
        self.analytics_client = analytics_client
        if config.stage in ("documents", "all") and document_client is None:
            self.document_client = QueueClient(
                base_url=config.queue_url,
                token=config.llm_token,
                timeout=config.http_timeout,
            )
        if config.stage in ("analytics", "all") and analytics_client is None:
            self.analytics_client = AnalyticsQueueClient(
                base_url=config.analytics_url,
                token=config.analytics_token,
                timeout=config.http_timeout,
            )

    # ---- общий цикл -------------------------------------------------
    def run(self, *, once=False):
        if once:
            ok, _worked = self._cycle()
            return 0 if ok else 1

        LOG.info(
            "Воркер запущен: stage=%s worker_id=%s poll=%ss scenario=%s",
            self.config.stage,
            self.config.worker_id,
            self.config.poll_seconds,
            self.config.scenario,
        )
        try:
            while not self.stop_requested:
                _ok, worked = self._cycle()
                if not worked:
                    time.sleep(self.config.poll_seconds)
        except KeyboardInterrupt:
            LOG.info("Воркер остановлен по Ctrl+C")
            return 0
        return 0

    def _cycle(self):
        ok = True
        worked = False
        if self.config.stage in ("documents", "all"):
            stage_ok, stage_worked = self._documents_cycle()
            ok = ok and stage_ok
            worked = worked or stage_worked
        if self.config.stage in ("analytics", "all"):
            stage_ok, stage_worked = self._analytics_cycle()
            ok = ok and stage_ok
            worked = worked or stage_worked
        return ok, worked

    def _log_stage_error(self, stage, error):
        if isinstance(error, ConfigurationClientError):
            LOG.error("%s: ошибка конфигурации/доступа: %s", stage, error)
        elif isinstance(error, TransientClientError):
            LOG.warning("%s: временная ошибка, повтор на следующем цикле: %s", stage, error)
        else:
            LOG.error("%s: %s", stage, error)

    def _report_fail(self, client, stage, job_id, lease_token, message, retryable):
        try:
            client.fail(job_id, lease_token, str(message)[:500], retryable=retryable)
        except ConfigurationClientError as error:
            LOG.error("%s: не удалось сообщить об ошибке (конфигурация): %s", stage, error)
        except Exception as error:  # сеть/гонка аренды: daemon продолжает жить
            LOG.warning("%s: не удалось сообщить об ошибке: %s", stage, error)

    # ---- извлечение -------------------------------------------------
    def _documents_cycle(self):
        stage = "documents"
        try:
            claim = self.document_client.claim(self.config.worker_id)
        except Exception as error:
            self._log_stage_error(stage, error)
            return False, False
        if claim is None:
            LOG.debug("documents: очередь пуста")
            return True, False

        job_id = claim.get("job_id") if isinstance(claim, dict) else None
        lease_token = claim.get("lease_token") if isinstance(claim, dict) else None
        if not isinstance(job_id, str) or not job_id or not isinstance(lease_token, str):
            LOG.error("documents: claim без job_id/lease_token")
            return False, True

        documents = claim.get("documents")
        if not isinstance(documents, list):
            self._report_fail(
                self.document_client, stage, job_id, lease_token,
                "В claim нет массива documents", False,
            )
            return False, True

        try:
            results = []
            for document in documents:
                if not isinstance(document, dict):
                    raise DocumentVerificationError("Элемент documents некорректен")
                content = self.document_client.download(
                    document.get("download_url"), lease_token
                )
                results.append(
                    build_document_result(document, content, self.config.scenario)
                )
        except (DocumentVerificationError, PermanentClientError) as error:
            LOG.error("%s: постоянная ошибка задачи %s: %s", stage, job_id, error)
            self._report_fail(
                self.document_client, stage, job_id, lease_token, error, False
            )
            return False, True
        except Exception as error:
            self._log_stage_error(stage, error)
            retryable = isinstance(error, TransientClientError)
            self._report_fail(
                self.document_client, stage, job_id, lease_token, error, retryable
            )
            return False, True

        try:
            self.document_client.complete(
                job_id, lease_token, results, extraction_summary(results)
            )
        except Exception as error:
            # Не вызываем fail: complete мог примениться на сервере, а ответ потеряться.
            self._log_stage_error(stage, error)
            return False, True

        LOG.info(
            "documents: задача %s завершена, документов=%s, scenario=%s",
            job_id,
            len(results),
            self.config.scenario,
        )
        return True, True

    # ---- аналитика ---------------------------------------------------
    def _analytics_cycle(self):
        stage = "analytics"
        try:
            claim = self.analytics_client.claim(self.config.worker_id)
        except Exception as error:
            self._log_stage_error(stage, error)
            return False, False
        if claim is None:
            LOG.debug("analytics: очередь пуста")
            return True, False

        job_id = claim.get("job_id") if isinstance(claim, dict) else None
        lease_token = claim.get("lease_token") if isinstance(claim, dict) else None
        input_hash = claim.get("input_hash") if isinstance(claim, dict) else None
        if (
            not isinstance(job_id, str)
            or not job_id
            or not isinstance(lease_token, str)
            or not isinstance(input_hash, str)
            or not input_hash
        ):
            LOG.error("analytics: claim без job_id/lease_token/input_hash")
            if isinstance(job_id, str) and job_id and isinstance(lease_token, str):
                self._report_fail(
                    self.analytics_client, stage, job_id, lease_token,
                    "Некорректный claim аналитики", False,
                )
            return False, True

        try:
            result = build_analysis_result(claim)
        except Exception as error:
            self._log_stage_error(stage, error)
            self._report_fail(
                self.analytics_client, stage, job_id, lease_token, error, False
            )
            return False, True

        try:
            self.analytics_client.complete_analysis(
                job_id, lease_token, input_hash, result
            )
        except Exception as error:
            self._log_stage_error(stage, error)
            return False, True

        LOG.info(
            "analytics: задача %s завершена, scenario=%s",
            job_id,
            result["scenario_id"],
        )
        return True, True


def _configure_logging():
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _install_stop_handlers():
    def stop(_signum, _frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGINT"):
        value = getattr(signal, name, None)
        if value is None:
            continue
        try:
            signal.signal(value, stop)
        except (ValueError, OSError):
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Demo-воркер очередей извлечения и аналитики (без реальной ML-модели)"
    )
    parser.add_argument(
        "--stage",
        choices=STAGES,
        default="all",
        help="documents, analytics или all (по умолчанию all)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="выполнить один цикл и выйти; код 1 при ошибке",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    _install_stop_handlers()
    try:
        config = load_config(args.stage)
        worker = Worker(config)
    except ConfigError as error:
        LOG.error("Конфигурация воркера: %s", error)
        return 2
    except ConfigurationClientError as error:
        LOG.error("Конфигурация воркера: %s", error)
        return 2
    return worker.run(once=args.once)


if __name__ == "__main__":
    sys.exit(main())
