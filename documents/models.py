import uuid
from pathlib import Path

from django.db import models
from django.utils import timezone


def application_document_path(instance, filename):
    suffix = Path(filename).suffix.lower()[:12]
    return f"applications/{instance.version.application_id}/versions/{instance.version_id}/{uuid.uuid4()}{suffix}"


class ApplicationDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        "applications.ApplicationVersion", related_name="documents", on_delete=models.PROTECT
    )
    file = models.FileField(upload_to=application_document_path, max_length=255)
    original_name = models.CharField(max_length=255)
    requirement_code = models.CharField(max_length=64, blank=True)
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
        "applications.ApplicationVersion", related_name="processing_job", on_delete=models.PROTECT
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
