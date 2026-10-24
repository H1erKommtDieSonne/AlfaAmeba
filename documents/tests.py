import json
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import ApplicationEvent
from core.testing import CreditTestCase

from .models import ApplicationDocument, DocumentProcessingJob, SerializedDocument
from .queue import claim_next_job, fail_job

HEADERS = {"HTTP_AUTHORIZATION": "Bearer test-llm-token"}


@override_settings(LLM_SERVICE_TOKEN="test-llm-token", LLM_RETRY_DELAY_SECONDS=0)
class LlmQueueTests(CreditTestCase):
    def test_llm_worker_claims_downloads_and_completes_job(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        document = ApplicationDocument.objects.get()

        unauthorized = self.client.post(
            reverse("llm-job-claim"),
            data=json.dumps({"worker_id": "worker-1"}),
            content_type="application/json",
        )
        self.assertEqual(unauthorized.status_code, 401)

        claimed = self.client.post(
            reverse("llm-job-claim"),
            data=json.dumps({"worker_id": "worker-1"}),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(claimed.status_code, 200)
        job_payload = claimed.json()
        self.assertEqual(job_payload["documents"][0]["document_id"], str(document.pk))
        self.assertEqual(job_payload["documents"][0]["requirement_code"], "statement")
        self.assertEqual(job_payload["application"]["needs"]["need"], "working")
        self.assertEqual(job_payload["application"]["checklist"][0]["status"], "uploaded")

        empty_queue = self.client.post(
            reverse("llm-job-claim"),
            data="{}",
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(empty_queue.status_code, 204)

        blocked_download = self.client.get(
            reverse("llm-job-document", args=(job_payload["job_id"], document.pk)),
            **HEADERS,
        )
        self.assertEqual(blocked_download.status_code, 409)

        downloaded = self.client.get(
            reverse("llm-job-document", args=(job_payload["job_id"], document.pk)),
            HTTP_X_LEASE_TOKEN=job_payload["lease_token"],
            **HEADERS,
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(b"".join(downloaded.streaming_content), b"%PDF-1.4 synthetic statement")

        completed = self.client.post(
            reverse("llm-job-complete", args=(job_payload["job_id"],)),
            data=json.dumps(
                {
                    "lease_token": job_payload["lease_token"],
                    "documents": [
                        {
                            "document_id": str(document.pk),
                            "document_type": "bank_statement",
                            "schema_version": "1.0",
                            "confidence": 0.93,
                            "payload": {
                                "account": "synthetic",
                                "transactions": [],
                            },
                        }
                    ],
                    "summary": {"document_count": 1},
                }
            ),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(completed.status_code, 200)
        job = DocumentProcessingJob.objects.get(pk=job_payload["job_id"])
        self.assertEqual(job.status, DocumentProcessingJob.Status.COMPLETED)
        self.assertEqual(SerializedDocument.objects.get().payload["account"], "synthetic")
        self.assertTrue(
            ApplicationEvent.objects.filter(
                application=job.version.application,
                event_type=ApplicationEvent.Type.DOCUMENTS_PROCESSED,
            ).exists()
        )

    def test_llm_worker_can_return_retryable_failure(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        claimed = self.client.post(
            reverse("llm-job-claim"),
            data="{}",
            content_type="application/json",
            **HEADERS,
        ).json()

        failed = self.client.post(
            reverse("llm-job-fail", args=(claimed["job_id"],)),
            data=json.dumps(
                {
                    "lease_token": claimed["lease_token"],
                    "error": "Временная ошибка OCR",
                    "retryable": True,
                }
            ),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(failed.status_code, 200)
        job = DocumentProcessingJob.objects.get(pk=claimed["job_id"])
        self.assertEqual(job.status, DocumentProcessingJob.Status.PENDING)
        self.assertEqual(job.last_error, "Временная ошибка OCR")
        self.assertIsNone(job.completed_at)

    def test_non_retryable_failure_records_completed_at(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        job = DocumentProcessingJob.objects.get()
        claimed = claim_next_job(worker_id="worker-1")

        fail_job(
            job_id=job.pk,
            lease_token=claimed.lease_token,
            error_message="Неисправимая ошибка OCR",
            retryable=False,
        )

        job.refresh_from_db()
        self.assertEqual(job.status, DocumentProcessingJob.Status.FAILED)
        self.assertIsNotNone(job.completed_at)

    def test_exhausted_retryable_failure_records_completed_at(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        job = DocumentProcessingJob.objects.get()
        job.max_attempts = 1
        job.save(update_fields=("max_attempts",))
        claimed = claim_next_job(worker_id="worker-1")
        self.assertEqual(claimed.attempts, 1)

        fail_job(
            job_id=job.pk,
            lease_token=claimed.lease_token,
            error_message="Последняя попытка не удалась",
            retryable=True,
        )

        job.refresh_from_db()
        self.assertEqual(job.status, DocumentProcessingJob.Status.FAILED)
        self.assertIsNotNone(job.completed_at)

    def test_expired_last_lease_fails_job_instead_of_endless_pending(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        job = DocumentProcessingJob.objects.get()
        job.max_attempts = 1
        job.save(update_fields=("max_attempts",))

        claimed = claim_next_job(worker_id="worker-1")
        self.assertEqual(claimed.attempts, 1)
        DocumentProcessingJob.objects.filter(pk=job.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        self.assertIsNone(claim_next_job(worker_id="worker-2"))
        job.refresh_from_db()
        self.assertEqual(job.status, DocumentProcessingJob.Status.FAILED)
        self.assertIsNotNone(job.completed_at)
        self.assertIsNone(job.lease_token)

    def test_claim_rejects_non_string_worker_id(self):
        response = self.client.post(
            reverse("llm-job-claim"),
            data=json.dumps({"worker_id": 42}),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(response.status_code, 400)

    def test_fail_rejects_non_boolean_retryable(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        claimed = self.client.post(
            reverse("llm-job-claim"),
            data=json.dumps({"worker_id": "worker-1"}),
            content_type="application/json",
            **HEADERS,
        ).json()

        failed = self.client.post(
            reverse("llm-job-fail", args=(claimed["job_id"],)),
            data=json.dumps(
                {
                    "lease_token": claimed["lease_token"],
                    "error": "Ошибка",
                    "retryable": "yes",
                }
            ),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(failed.status_code, 400)
        job = DocumentProcessingJob.objects.get(pk=claimed["job_id"])
        self.assertEqual(job.status, DocumentProcessingJob.Status.PROCESSING)

    def test_complete_rejects_non_finite_confidence(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        document = ApplicationDocument.objects.get()
        claimed = self.client.post(
            reverse("llm-job-claim"),
            data=json.dumps({"worker_id": "worker-1"}),
            content_type="application/json",
            **HEADERS,
        ).json()

        completed = self.client.post(
            reverse("llm-job-complete", args=(claimed["job_id"],)),
            data=json.dumps(
                {
                    "lease_token": claimed["lease_token"],
                    "documents": [
                        {
                            "document_id": str(document.pk),
                            "document_type": "bank_statement",
                            "schema_version": "1.0",
                            "confidence": "NaN",
                            "payload": {},
                        }
                    ],
                }
            ),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(completed.status_code, 409)
        self.assertFalse(SerializedDocument.objects.exists())
