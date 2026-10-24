import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from analytics.models import AnalysisRun
from applications.models import Application, ApplicationVersion
from core.testing import CreditTestCase
from documents.models import ApplicationDocument, DocumentProcessingJob
from documents.services import save_documents_and_enqueue

from .models import NodeJob
from .services import canonical_json, enqueue_version, sha256_json
from .views import MAX_CALLBACK_BYTES, callback

TOKEN = "test-node-callback-token"
HEADERS = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


@override_settings(DOCUMENT_NODE_CALLBACK_TOKEN=TOKEN)
class NodeCallbackTests(CreditTestCase):
    def setUp(self):
        self.version = self._make_version()
        self.job = NodeJob.objects.create(
            version=self.version,
            external_job_id="a" * 32,
            status=NodeJob.Status.WAITING,
        )

    def _make_version(self, number=1):
        application = Application.objects.create(
            organization=self.organization,
            organization_name=self.organization.name,
            inn=self.organization.inn,
            created_by=self.business_user,
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        return ApplicationVersion.objects.create(
            application=application,
            version_number=number,
            product=self.product,
            amount="1500000.00",
            term_months=12,
            purpose="Закупка сезонной партии товара на три месяца",
            submitted_at=timezone.now(),
            snapshot={},
        )

    def _payload(self, **overrides):
        payload = {
            "job_id": self.job.external_job_id,
            "status": "completed",
            "result": {
                "summary": "Проверка завершена",
                "nested": {"b": 2, "a": 1},
            },
            "files": [{"name": "statement.pdf", "sha256": "0" * 64}],
            "verification_status": "needs_review",
            "model": "document-node-1",
        }
        payload.update(overrides)
        return payload

    def _post(self, payload=None, *, raw=None, headers=None, content_type="application/json"):
        data = raw if raw is not None else json.dumps(payload)
        merged_headers = {**HEADERS, **(headers or {})}
        # None явно убирает заголовок, чтобы проверить его отсутствие.
        merged_headers = {key: value for key, value in merged_headers.items() if value is not None}
        return self.client.post(
            reverse("document-node-callback"),
            data=data,
            content_type=content_type,
            **merged_headers,
        )

    def test_empty_configured_token_closes_callback(self):
        with override_settings(DOCUMENT_NODE_CALLBACK_TOKEN=""):
            response = self._post(self._payload())
        self.assertEqual(response.status_code, 503)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.WAITING)

    def test_rejects_missing_or_wrong_bearer_token(self):
        for headers in (
            {"HTTP_AUTHORIZATION": None},
            {"HTTP_AUTHORIZATION": "Bearer wrong"},
        ):
            response = self._post(self._payload(), headers=headers)
            self.assertEqual(response.status_code, 401)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.WAITING)
        self.assertIsNone(self.job.result)

    def test_unknown_job_returns_404_without_mutation(self):
        response = self._post(self._payload(job_id="b" * 32))
        self.assertEqual(response.status_code, 404)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.WAITING)
        self.assertIsNone(self.job.result)
        self.assertEqual(NodeJob.objects.count(), 1)

    def test_rejects_malformed_json_and_nonfinite_numbers(self):
        malformed = self._post(raw="{not-json")
        self.assertEqual(malformed.status_code, 400)

        nonfinite = self._post(raw=json.dumps(self._payload(result={"value": float("nan")})))
        self.assertEqual(nonfinite.status_code, 400)
        self.job.refresh_from_db()
        self.assertIsNone(self.job.result)

    def test_rejects_invalid_payload_fields(self):
        cases = [
            {"status": "failed"},
            {"result": []},
            {"files": {}},
            {"verification_status": "verified"},
            {"verification_status": ["needs_review"]},
            {"verification_status": {"value": "needs_review"}},
            {"job_id": "A" * 32},
            {"job_id": "a" * 31},
            {"model": 42},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                response = self._post(self._payload(**overrides))
                self.assertEqual(response.status_code, 400)
        self.job.refresh_from_db()
        self.assertIsNone(self.job.result)

    def test_rejects_exponent_overflow_and_invalid_unicode(self):
        overflow_raw = json.dumps(self._payload(result={"value": 0})).replace(
            '"value": 0', '"value": 1e400'
        )
        self.assertEqual(self._post(raw=overflow_raw).status_code, 400)

        unicode_raw = json.dumps(self._payload(result={"value": "x"})).replace(
            '"x"', '"\\ud800"'
        )
        self.assertEqual(self._post(raw=unicode_raw).status_code, 400)

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.WAITING)
        self.assertIsNone(self.job.result)

    def test_optional_headers_must_match_body_job_id(self):
        for header in ("HTTP_X_JOB_ID", "HTTP_IDEMPOTENCY_KEY"):
            with self.subTest(header=header):
                response = self._post(
                    self._payload(),
                    headers={header: "b" * 32},
                )
                self.assertEqual(response.status_code, 400)
        self.job.refresh_from_db()
        self.assertIsNone(self.job.result)

    def test_requires_json_content_type(self):
        response = self._post(self._payload(), content_type="text/plain")
        self.assertEqual(response.status_code, 415)

    def test_rejects_oversized_body_with_content_length(self):
        response = self._post(raw=b"x" * (MAX_CALLBACK_BYTES + 1))
        self.assertEqual(response.status_code, 413)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.WAITING)

    def test_rejects_oversized_body_without_content_length(self):
        class UnreadableRawStream:
            """Сырой wsgi.input не должен читаться: только bounded stream Django."""

            def read(self, *args, **kwargs):
                raise AssertionError("raw wsgi.input must not be read")

        factory = RequestFactory()
        body = b"x" * (MAX_CALLBACK_BYTES + 1)
        request = factory.post(
            "/api/v1/document-node/callback/",
            data=body,
            content_type="application/json",
            **HEADERS,
        )
        request.META.pop("CONTENT_LENGTH", None)
        request.META["wsgi.input"] = UnreadableRawStream()

        response = callback(request)

        self.assertEqual(response.status_code, 413)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.WAITING)

    def test_completed_callback_preserves_full_json_and_does_not_touch_application(self):
        payload = self._payload()
        application_status = self.version.application.status
        event_count = self.version.application.events.count()

        response = self._post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"job_id": self.job.external_job_id, "status": "COMPLETED"})
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.COMPLETED)
        self.assertEqual(self.job.result, payload)
        self.assertEqual(self.job.result["files"], payload["files"])
        self.assertEqual(self.job.result["verification_status"], "needs_review")
        self.assertEqual(self.job.result_sha256, sha256_json(payload))
        self.assertIsNotNone(self.job.completed_at)

        self.version.application.refresh_from_db()
        self.assertEqual(self.version.application.status, application_status)
        self.assertEqual(self.version.application.events.count(), event_count)

    def test_accepts_completion_from_sending_waiting_and_uncertain(self):
        for index, status in enumerate(
            (NodeJob.Status.SENDING, NodeJob.Status.WAITING, NodeJob.Status.UNCERTAIN),
            start=1,
        ):
            with self.subTest(status=status):
                version = self._make_version(number=index + 1)
                job = NodeJob.objects.create(
                    version=version,
                    external_job_id=f"{index:032x}",
                    status=status,
                )
                response = self._post(self._payload(job_id=job.external_job_id))
                self.assertEqual(response.status_code, 200)
                job.refresh_from_db()
                self.assertEqual(job.status, NodeJob.Status.COMPLETED)

    def test_rejects_completion_from_pending_or_failed(self):
        for status in (NodeJob.Status.PENDING, NodeJob.Status.FAILED):
            with self.subTest(status=status):
                NodeJob.objects.filter(pk=self.job.pk).update(status=status)
                response = self._post(self._payload())
                self.assertEqual(response.status_code, 409)
                self.job.refresh_from_db()
                self.assertEqual(self.job.status, status)
                self.assertIsNone(self.job.result)

    def test_duplicate_reordered_payload_is_idempotent(self):
        payload = self._payload()
        self.assertEqual(self._post(payload).status_code, 200)
        self.job.refresh_from_db()
        first_completed_at = self.job.completed_at
        first_hash = self.job.result_sha256

        reordered = {
            "model": payload["model"],
            "verification_status": payload["verification_status"],
            "files": payload["files"],
            "result": {"nested": {"a": 1, "b": 2}, "summary": payload["result"]["summary"]},
            "status": payload["status"],
            "job_id": payload["job_id"],
        }
        self.assertNotEqual(canonical_json(payload), json.dumps(reordered))
        response = self._post(reordered)

        self.assertEqual(response.status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.COMPLETED)
        self.assertEqual(self.job.result_sha256, first_hash)
        self.assertEqual(self.job.completed_at, first_completed_at)
        self.assertEqual(self.job.result, payload)

    def test_second_different_payload_does_not_overwrite(self):
        payload = self._payload()
        self.assertEqual(self._post(payload).status_code, 200)
        self.job.refresh_from_db()
        first_result = self.job.result
        first_hash = self.job.result_sha256

        changed = self._payload(result={"summary": "Другой результат"})
        response = self._post(changed)

        self.assertEqual(response.status_code, 409)
        self.job.refresh_from_db()
        self.assertEqual(self.job.result, first_result)
        self.assertEqual(self.job.result_sha256, first_hash)
        self.assertEqual(self.job.status, NodeJob.Status.COMPLETED)


class NodeEnqueueTests(CreditTestCase):
    def setUp(self):
        self.version = self._make_version()

    def _make_version(self, number=1):
        application = Application.objects.create(
            organization=self.organization,
            organization_name=self.organization.name,
            inn=self.organization.inn,
            created_by=self.business_user,
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        return ApplicationVersion.objects.create(
            application=application,
            version_number=number,
            product=self.product,
            amount="1500000.00",
            term_months=12,
            purpose="Закупка сезонной партии товара на три месяца",
            submitted_at=timezone.now(),
            snapshot={},
        )

    def _upload(self):
        return SimpleUploadedFile(
            "statement.pdf",
            b"%PDF-1.4 synthetic statement",
            content_type="application/pdf",
        )

    def test_enqueue_creates_one_pending_job_bound_to_version(self):
        processing_job = save_documents_and_enqueue(
            self.version,
            [("statement", self._upload())],
        )

        node_job = NodeJob.objects.get(version=self.version)
        self.assertEqual(node_job.status, NodeJob.Status.PENDING)
        self.assertIsNone(node_job.external_job_id)
        self.assertIsNotNone(node_job.available_at)
        self.assertEqual(node_job.version_id, self.version.pk)
        self.assertEqual(node_job.version, self.version)
        self.assertEqual(processing_job.version_id, self.version.pk)
        self.assertEqual(ApplicationDocument.objects.filter(version=self.version).count(), 1)
        self.assertTrue(
            AnalysisRun.objects.filter(
                version=self.version, status=AnalysisRun.Status.WAITING
            ).exists()
        )

        again = enqueue_version(self.version)
        self.assertEqual(again.pk, node_job.pk)
        self.assertEqual(NodeJob.objects.filter(version=self.version).count(), 1)

    def test_enqueue_rolls_back_with_document_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                save_documents_and_enqueue(
                    self.version,
                    [("statement", self._upload())],
                )
                raise RuntimeError("rollback")

        self.assertFalse(NodeJob.objects.filter(version=self.version).exists())
        self.assertFalse(ApplicationDocument.objects.filter(version=self.version).exists())
        self.assertFalse(DocumentProcessingJob.objects.filter(version=self.version).exists())
        self.assertFalse(AnalysisRun.objects.filter(version=self.version).exists())
