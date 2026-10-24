import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class NodeJob(models.Model):
    """Задача проверки версии заявки внешним узлом.

    Запись создаётся вместе с документами версии в статусе PENDING. Отправку
    выполняет отдельный воркер; обратный вызов узла только фиксирует результат.

    Попыток на версию может быть несколько: пересчёт после того, как сотрудник
    подтвердил данные для расчёта, — новая задача с новым job_id узла, а прежняя
    остаётся в истории со своим результатом (docs/financial-api-v2.md, «Статусы,
    callback, версии»). Карточка показывает последнюю попытку.
    """

    ACTIVE_STATUSES = ("PENDING", "SENDING", "WAITING")

    class Status(models.TextChoices):
        PENDING = "PENDING", "Ожидает отправки"
        SENDING = "SENDING", "Отправляется"
        WAITING = "WAITING", "Ждёт результата"
        COMPLETED = "COMPLETED", "Завершена"
        FAILED = "FAILED", "Ошибка"
        UNCERTAIN = "UNCERTAIN", "Неизвестный результат"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        "applications.ApplicationVersion",
        related_name="node_jobs",
        on_delete=models.PROTECT,
    )
    # Кто запросил пересчёт; у первой попытки, созданной вместе с версией, пусто.
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="requested_node_jobs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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


class FinancialInputs(models.Model):
    """Данные для расчёта узла, которые подтверждает сотрудник банка, а не LLM.

    Без них узел не утверждает, что видит весь денежный поток, не признаёт месяцы
    выписки полными и не учитывает платёж по новому кредиту (docs/financial-api-v2.md,
    «Контекст заявки»). Привязаны к версии: другой комплект документов — другие периоды.
    В контекст уходят при следующей отправке версии узлу.
    """

    class DebtStatus(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Не подтверждено"
        NONE = "NONE", "Действующих долгов нет"
        COMPLETE = "COMPLETE", "Графики всех долгов приложены"

    version = models.OneToOneField(
        "applications.ApplicationVersion",
        related_name="financial_inputs",
        on_delete=models.CASCADE,
    )
    annual_rate_percent = models.DecimalField("ставка, % годовых", max_digits=5, decimal_places=2, null=True, blank=True)
    proposed_monthly_payment_rub = models.DecimalField(
        "платёж по новому кредиту, ₽", max_digits=14, decimal_places=2, null=True, blank=True
    )
    # Все расчётные счета клиента; без списка узел не считает поток полным.
    expected_account_ids = models.JSONField("подтверждённые счета", default=list, blank=True)
    # [{filename, account_id, period_start, period_end, opening_balance_rub, closing_balance_rub, complete}]
    statement_periods = models.JSONField("периоды выписок", default=list, blank=True)
    debt_status = models.CharField("долги", max_length=16, choices=DebtStatus.choices, default=DebtStatus.UNKNOWN)
    debt_schedule_start = models.DateField("графики долгов с", null=True, blank=True)
    debt_schedule_end = models.DateField("графики долгов по", null=True, blank=True)
    reporting_year = models.PositiveSmallIntegerField("отчётный год", null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="confirmed_financial_inputs", on_delete=models.PROTECT
    )
    confirmed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "данные для расчёта узла"
        verbose_name_plural = "данные для расчёта узла"

    def __str__(self):
        return f"Данные для расчёта — {self.version}"
