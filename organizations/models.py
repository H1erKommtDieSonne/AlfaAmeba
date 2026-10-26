import uuid

from django.conf import settings
from django.core.validators import MinLengthValidator, RegexValidator
from django.db import models


INN_VALIDATOR = RegexValidator(r"^(\d{10}|\d{12})$", "ИНН должен содержать 10 или 12 цифр")


class Organization(models.Model):
    class VerificationStatus(models.TextChoices):
        PENDING = "PENDING", "Ожидает проверки"
        VERIFIED = "VERIFIED", "Проверена"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Требует уточнения"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, validators=[MinLengthValidator(2)])
    inn = models.CharField(max_length=12, unique=True, validators=[INN_VALIDATOR])
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
