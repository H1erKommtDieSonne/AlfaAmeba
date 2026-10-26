import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.db import models

from organizations.models import INN_VALIDATOR


class Application(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Черновик"
        SUBMITTED = "SUBMITTED", "Отправлена"
        IN_REVIEW = "IN_REVIEW", "На рассмотрении"
        NEEDS_REVISION = "NEEDS_REVISION", "Требует доработки"
        PENDING_APPROVAL = "PENDING_APPROVAL", "На согласовании"
        APPROVED = "APPROVED", "Одобрена"
        DECLINED = "DECLINED", "Отклонена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="credit_applications",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        related_name="applications",
        on_delete=models.PROTECT,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="assigned_credit_applications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    organization_name = models.CharField(max_length=200, validators=[MinLengthValidator(2)])
    inn = models.CharField(max_length=12, validators=[INN_VALIDATOR], db_index=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT, db_index=True)
    current_version = models.PositiveIntegerField(default=1)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self):
        return f"{self.organization_name} — {self.get_status_display()}"


class ApplicationVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(Application, related_name="versions", on_delete=models.PROTECT)
    version_number = models.PositiveIntegerField()
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    term_months = models.PositiveSmallIntegerField()
    purpose = models.TextField(validators=[MinLengthValidator(10)])
    snapshot = models.JSONField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("application", "version_number"),
                name="unique_application_version",
            )
        ]
        ordering = ("application", "version_number")

    def __str__(self):
        return f"{self.application_id} v{self.version_number}"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, submitted_at__isnull=False).exists():
            raise ValidationError("Отправленную версию заявки нельзя изменять")
        return super().save(*args, **kwargs)


class ApplicationComment(models.Model):
    class Audience(models.TextChoices):
        CLIENT = "CLIENT", "Клиенту"
        INTERNAL = "INTERNAL", "Только банку"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(Application, related_name="comments", on_delete=models.CASCADE)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="application_comments", on_delete=models.PROTECT
    )
    audience = models.CharField(max_length=16, choices=Audience.choices)
    text = models.TextField(validators=[MinLengthValidator(3)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"Комментарий к {self.application_id} от {self.author}"


class ApplicationEvent(models.Model):
    class Type(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Заявка отправлена"
        ASSIGNED = "ASSIGNED", "Назначен сотрудник"
        STATUS_CHANGED = "STATUS_CHANGED", "Статус изменён"
        COMMENT_ADDED = "COMMENT_ADDED", "Добавлен комментарий"
        REVISION_SUBMITTED = "REVISION_SUBMITTED", "Доработка отправлена"
        DOCUMENTS_PROCESSED = "DOCUMENTS_PROCESSED", "Документы обработаны"

    application = models.ForeignKey(Application, related_name="events", on_delete=models.CASCADE)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="application_events",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=32, choices=Type.choices)
    from_status = models.CharField(max_length=32, blank=True)
    to_status = models.CharField(max_length=32, blank=True)
    message = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"{self.get_event_type_display()} — {self.application_id}"
