import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AnalysisRun(models.Model):
    """Очередь аналитики по версии заявки.

    Одна запись — один запуск модели/политики. Результат после завершения
    неизменяем: повторная запись с другим результатом запрещена на уровне модели.
    """

    class Status(models.TextChoices):
        WAITING = "WAITING", "Ждёт извлечения документов"
        PENDING = "PENDING", "Готова к запуску"
        PROCESSING = "PROCESSING", "Выполняется"
        READY = "READY", "Готова"
        PARTIAL = "PARTIAL", "Частичный результат"
        FAILED = "FAILED", "Ошибка"

    TERMINAL_STATUSES = (Status.READY, Status.PARTIAL, Status.FAILED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        "applications.ApplicationVersion",
        related_name="analysis_runs",
        on_delete=models.PROTECT,
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.WAITING, db_index=True
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.CharField(max_length=120, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Оставляем последний lease_token и после завершения: он нужен для
    # идемпотентного повтора complete с тем же результатом.
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    model_version = models.CharField(max_length=64, default="stub-1")
    policy_version = models.CharField(max_length=64, default="demo-1")
    policy_snapshot = models.JSONField(default=dict, blank=True)
    source_hash = models.CharField(max_length=64, blank=True)
    input_hash = models.CharField(max_length=64, blank=True)
    extraction_status = models.CharField(max_length=16, blank=True)
    result = models.JSONField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("version", "model_version", "policy_version"),
                name="unique_analysis_run_version_versions",
            )
        ]
        indexes = [
            models.Index(fields=("status", "available_at")),
            models.Index(fields=("version", "status")),
        ]

    def __str__(self):
        return f"{self.version} — {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            stored = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(
                    "result",
                    "status",
                    "version_id",
                    "model_version",
                    "policy_version",
                    "input_hash",
                    "source_hash",
                )
                .first()
            )
            if stored is not None:
                if stored["result"] is not None and self.result != stored["result"]:
                    raise ValidationError("Результат завершённого анализа нельзя изменить")
                if stored["status"] in (self.Status.READY, self.Status.PARTIAL):
                    if self.status != stored["status"]:
                        raise ValidationError("Статус завершённого анализа нельзя изменить")
                    if self.result != stored["result"]:
                        raise ValidationError("Результат завершённого анализа нельзя изменить")
                    for field in (
                        "version_id",
                        "model_version",
                        "policy_version",
                        "input_hash",
                        "source_hash",
                    ):
                        if getattr(self, field) != stored[field]:
                            raise ValidationError(
                                "Идентичность завершённого анализа нельзя изменить"
                            )
        return super().save(*args, **kwargs)
