import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator, RegexValidator
from django.db import models


class UserProfile(models.Model):
    class Role(models.TextChoices):
        BUSINESS = "BUSINESS", "Представитель бизнеса"
        BANK_EMPLOYEE = "BANK_EMPLOYEE", "Сотрудник банка"
        BANK_ADMIN = "BANK_ADMIN", "Администратор банка"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name="profile", on_delete=models.CASCADE)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.BUSINESS)

    def __str__(self):
        return f"{self.user} — {self.get_role_display()}"


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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="credit_applications",
        on_delete=models.PROTECT,
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

