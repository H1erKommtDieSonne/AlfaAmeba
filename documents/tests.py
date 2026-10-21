import json

from django.test import override_settings
from django.urls import reverse

from applications.models import ApplicationEvent
from core.testing import CreditTestCase

from .models import ApplicationDocument, DocumentProcessingJob, SerializedDocument

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
