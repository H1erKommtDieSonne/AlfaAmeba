import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator, RegexValidator
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    class Role(models.TextChoices):
        BUSINESS = "BUSINESS", "Представитель бизнеса"
        BANK_REVIEWER = "BANK_REVIEWER", "Кредитный специалист"
        SENIOR_APPROVER = "SENIOR_APPROVER", "Старший согласующий"
        BANK_ADMIN = "BANK_ADMIN", "Администратор банка"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name="profile", on_delete=models.CASCADE)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.BUSINESS)

    def __str__(self):
        return f"{self.user} — {self.get_role_display()}"


class Organization(models.Model):
    class VerificationStatus(models.TextChoices):
        PENDING = "PENDING", "Ожидает проверки"
        VERIFIED = "VERIFIED", "Проверена"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Требует уточнения"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, validators=[MinLengthValidator(2)])
    inn = models.CharField(
        max_length=12,
        unique=True,
        validators=[RegexValidator(r"^(\d{10}|\d{12})$", "ИНН должен содержать 10 или 12 цифр")],
    )
    verification_status = models.CharField(
        max_length=24,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.inn})"


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Владелец"
        EDITOR = "EDITOR", "Редактор"
        SUBMITTER = "SUBMITTER", "Отправитель"
        VIEWER = "VIEWER", "Наблюдатель"

    organization = models.ForeignKey(
        Organization, related_name="memberships", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="organization_memberships", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=24, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "user"), name="unique_organization_membership"
            )
        ]

    def __str__(self):
        return f"{self.user} — {self.organization} ({self.get_role_display()})"


class OrganizationAccessRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Ожидает подтверждения"
        APPROVED = "APPROVED", "Подтверждён"
        REJECTED = "REJECTED", "Отклонён"

    organization = models.ForeignKey(
        Organization, related_name="access_requests", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="organization_access_requests", on_delete=models.CASCADE
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="reviewed_organization_access_requests",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    review_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "user"), name="unique_organization_access_request"
            )
        ]
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user} → {self.organization} ({self.get_status_display()})"


class Product(models.Model):
    code = models.SlugField(primary_key=True, max_length=64)
    name = models.CharField(max_length=160)
    description = models.TextField()
    min_amount = models.DecimalField(max_digits=14, decimal_places=2)
    max_amount = models.DecimalField(max_digits=14, decimal_places=2)
    min_term_months = models.PositiveSmallIntegerField()
    max_term_months = models.PositiveSmallIntegerField()
    badge = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("code",)

    def __str__(self):
        return self.name


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
        Organization,
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
    inn = models.CharField(
        max_length=12,
        validators=[RegexValidator(r"^(\d{10}|\d{12})$", "ИНН должен содержать 10 или 12 цифр")],
        db_index=True,
    )
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
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
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


def application_document_path(instance, filename):
    suffix = Path(filename).suffix.lower()[:12]
    return f"applications/{instance.version.application_id}/versions/{instance.version_id}/{uuid.uuid4()}{suffix}"


class ApplicationDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        ApplicationVersion, related_name="documents", on_delete=models.PROTECT
    )
    file = models.FileField(upload_to=application_document_path, max_length=255)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return self.original_name


class DocumentProcessingJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Ожидает обработки"
        PROCESSING = "PROCESSING", "Обрабатывается"
        COMPLETED = "COMPLETED", "Обработана"
        FAILED = "FAILED", "Ошибка обработки"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.OneToOneField(
        ApplicationVersion, related_name="processing_job", on_delete=models.PROTECT
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.CharField(max_length=120, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    result_summary = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=("status", "available_at"))]

    def __str__(self):
        return f"{self.version} — {self.get_status_display()}"


class SerializedDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        DocumentProcessingJob, related_name="serialized_documents", on_delete=models.PROTECT
    )
    source_document = models.OneToOneField(
        ApplicationDocument, related_name="serialized_result", on_delete=models.PROTECT
    )
    document_type = models.CharField(max_length=100, blank=True)
    schema_version = models.CharField(max_length=32, default="1.0")
    confidence = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"Результат {self.source_document.original_name}"


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

