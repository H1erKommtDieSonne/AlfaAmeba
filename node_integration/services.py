"""Идемпотентная постановка задач узла и приём его callback-результата."""

import hashlib
import json

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import NodeFeedback, NodeJob

CALLBACK_COMPLETED = "completed"
ALLOWED_VERIFICATION_STATUSES = frozenset({"needs_review", "not_verified"})
# Завершить задачу можно только из состояний, в которых ответ ещё ожидается.
COMPLETABLE_STATUSES = frozenset(
    {
        NodeJob.Status.WAITING,
        NodeJob.Status.SENDING,
        NodeJob.Status.UNCERTAIN,
    }
)
# Замечания принимаем и после результата: узлу нужен канал для проблем разбора.
FEEDBACK_ACCEPTABLE_STATUSES = frozenset(
    {
        NodeJob.Status.WAITING,
        NodeJob.Status.SENDING,
        NodeJob.Status.UNCERTAIN,
        NodeJob.Status.COMPLETED,
    }
)


class NodeJobConflict(Exception):
    """Задача уже завершена другим результатом или сейчас не принимает ответ."""


class NodeFeedbackConflict(Exception):
    """feedback_id переиспользован с другим содержимым."""


class NodeJobBusy(Exception):
    """Узел ещё обрабатывает прошлую попытку этой версии: второй запрос создал бы дубль."""


def enqueue_version(version):
    """Создать первую PENDING-задачу узла для версии заявки, если её ещё нет.

    Вызывается в транзакции сохранения документов; внешних запросов не делает.
    """
    job = NodeJob.objects.filter(version=version).order_by("created_at").first()
    if job is None:
        job = NodeJob.objects.create(
            version=version,
            status=NodeJob.Status.PENDING,
            available_at=timezone.now(),
        )
    return job


def latest_job(version):
    """Последняя попытка узла по версии (любого статуса) или ``None``."""
    return NodeJob.objects.filter(version=version).order_by("-created_at").first()


def latest_completed_job(version):
    """Последняя попытка с доставленным результатом: её показываем, пока новая в работе."""
    return (
        NodeJob.objects.filter(version=version, status=NodeJob.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )


@transaction.atomic
def request_recalculation(version, *, user):
    """Поставить версию на повторную отправку узлу с актуальными данными для расчёта.

    POST /jobs у узла не идемпотентен, поэтому новая попытка создаётся, только когда
    прошлая уже не в работе. Ещё не отправленная PENDING-попытка заберёт свежие
    данные сама: контекст собирается в момент отправки, — тогда новой не нужно.
    Возвращает (задача, создана ли новая).
    """
    jobs = NodeJob.objects.select_for_update().filter(version=version).order_by("-created_at")
    current = jobs.first()
    if current is not None and current.status == NodeJob.Status.PENDING:
        return current, False
    if current is not None and current.status in NodeJob.ACTIVE_STATUSES:
        raise NodeJobBusy("Узел ещё обрабатывает прошлую попытку; пересчёт можно запустить после его ответа")
    job = NodeJob.objects.create(
        version=version,
        status=NodeJob.Status.PENDING,
        available_at=timezone.now(),
        requested_by=user,
    )
    return job, True


def canonical_json(value):
    """Единственное представление JSON для сравнения и хэширования."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_json(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@transaction.atomic
def complete_node_job(payload):
    """Зафиксировать завершённый callback узла.

    Возвращает (job, created). Повтор с тем же каноническим JSON идемпотентен,
    другой результат для уже завершённой задачи не перезаписывается.
    """
    canonical = canonical_json(payload)
    digest = sha256_json(payload)
    job = (
        NodeJob.objects.select_for_update()
        .filter(external_job_id=payload["job_id"])
        .first()
    )
    if job is None:
        raise NodeJob.DoesNotExist

    if job.status == NodeJob.Status.COMPLETED:
        if job.result_sha256 == digest:
            return job, False
        raise NodeJobConflict

    if job.status not in COMPLETABLE_STATUSES:
        raise NodeJobConflict

    job.result = json.loads(canonical)
    job.result_sha256 = digest
    job.status = NodeJob.Status.COMPLETED
    job.completed_at = timezone.now()
    job.last_error = ""
    job.save(
        update_fields=(
            "result",
            "result_sha256",
            "status",
            "completed_at",
            "last_error",
            "updated_at",
        )
    )
    return job, True


def store_node_feedback(payload, *, job, issues):
    """Сохранить нормализованные замечания узла, идемпотентно по feedback_id.

    Хэш считается по каноническому исходному телу: повтор с тем же содержимым
    возвращает existing без записи, другое тело для того же feedback_id —
    ``NodeFeedbackConflict``. Роль ``feedback_id`` — ключ идемпотентности, а не
    способ перезаписи. Гонку закрывает уникальный первичный ключ.
    """
    feedback_id = payload["feedback_id"]
    digest = sha256_json(payload)

    existing = NodeFeedback.objects.filter(pk=feedback_id).first()
    if existing is not None:
        if existing.payload_sha256 == digest:
            return existing, False
        raise NodeFeedbackConflict

    try:
        with transaction.atomic():
            feedback = NodeFeedback.objects.create(
                feedback_id=feedback_id,
                job=job,
                issues=issues,
                payload_sha256=digest,
            )
    except IntegrityError:
        existing = NodeFeedback.objects.filter(pk=feedback_id).first()
        if existing is not None and existing.payload_sha256 == digest:
            return existing, False
        raise NodeFeedbackConflict
    return feedback, True
