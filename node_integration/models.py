import uuid

from django.db import models
from django.utils import timezone


class NodeJob(models.Model):
    """Задача проверки версии заявки внешним узлом.

    Запись создаётся вместе с документами версии в статусе PENDING. Отправку
    выполняет отдельный воркер; обратный вызов узла только фиксирует результат.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Ожидает отправки"
        SENDING = "SENDING", "Отправляется"
        WAITING = "WAITING", "Ждёт результата"
        COMPLETED = "COMPLETED", "Завершена"
        FAILED = "FAILED", "Ошибка"
        UNCERTAIN = "UNCERTAIN", "Неизвестный результат"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.OneToOneField(
        "applications.ApplicationVersion",
        related_name="node_job",
        on_delete=models.PROTECT,
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    # Идентификатор задачи на стороне узла: 32 символа в нижнем регистре hex.
    external_job_id = models.CharField(
        max_length=32, unique=True, null=True, blank=True, db_index=True
    )
    result = models.JSONField(null=True, blank=True)
    result_sha256 = models.CharField(max_length=64, blank=True)
    last_error = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=("status", "available_at"))]

    def __str__(self):
        return f"{self.version} — {self.get_status_display()}"


class NodeFeedback(models.Model):
    """Структурированные замечания узла по документам одной версии заявки.

    Запись неизменяема и привязана к задаче узла (``NodeJob``): версия берётся
    из задачи, а не из тела запроса. Отдельные ``issues`` — только для показа
    клиенту/банку; банковские статусы и решения заявки не меняются.
    """

    feedback_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(NodeJob, related_name="feedback", on_delete=models.PROTECT)
    issues = models.JSONField()
    payload_sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=("job", "created_at"))]

    def __str__(self):
        return f"Замечания {self.feedback_id} — {self.job}"
