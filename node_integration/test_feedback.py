import json
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ApplicationEvent, ApplicationVersion
from core.testing import CreditTestCase
from documents.models import ApplicationDocument

from .models import NodeFeedback, NodeJob
from .views import FEEDBACK_MAX_BYTES

TOKEN = "test-node-callback-token"
HEADERS = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


@override_settings(DOCUMENT_NODE_CALLBACK_TOKEN=TOKEN)
class NodeFeedbackTests(CreditTestCase):
    def setUp(self):
        self.version = self._make_version()
        self.document = self._document(self.version)
        self.job = NodeJob.objects.create(
            version=self.version,
            external_job_id="c" * 32,
            status=NodeJob.Status.COMPLETED,
            result={"summary": "raw model result"},
        )
        self.feedback_id = str(uuid.uuid4())

    def _make_version(self, number=1):
        application = Application.objects.create(
            organization=self.organization,
            organization_name=self.organization.name,
            inn=self.organization.inn,
            created_by=self.business_user,
            status=Application.Status.IN_REVIEW,
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

    def _document(self, version, name="statement.pdf"):
        return ApplicationDocument.objects.create(
            version=version,
            file=SimpleUploadedFile(name, b"data", content_type="application/pdf"),
            original_name=name,
            requirement_code="statement",
            content_type="application/pdf",
            size=4,
            sha256="0" * 64,
        )

    def _payload(self, **overrides):
        payload = {
            "feedback_id": self.feedback_id,
            "job_id": self.job.external_job_id,
            "issues": [
                {
                    "code": "missing_field",
                    "message": "  Не найден номер счёта  ",
                    "field": "account_number",
                    "document_id": str(self.document.pk),
                    "filename": "statement.pdf",
                    "suggestion": "Загрузите полный документ",
                }
            ],
        }
        payload.update(overrides)
        return payload

    def _post(self, payload=None, *, raw=None, headers=None, content_type="application/json"):
        data = raw if raw is not None else json.dumps(payload)
        merged = {**HEADERS, **(headers or {})}
        merged = {key: value for key, value in merged.items() if value is not None}
        return self.client.post(
            reverse("document-node-feedback"),
            data=data,
            content_type=content_type,
            **merged,
        )

    # --- auth / transport -------------------------------------------------

    def test_empty_token_closes_endpoint(self):
        with override_settings(DOCUMENT_NODE_CALLBACK_TOKEN=""):
            response = self._post(self._payload())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(NodeFeedback.objects.count(), 0)

    def test_rejects_missing_or_wrong_bearer(self):
        for headers in ({"HTTP_AUTHORIZATION": None}, {"HTTP_AUTHORIZATION": "Bearer wrong"}):
            with self.subTest(headers=headers):
                response = self._post(self._payload(), headers=headers)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(NodeFeedback.objects.count(), 0)

    def test_requires_json_content_type(self):
        self.assertEqual(self._post(self._payload(), content_type="text/plain").status_code, 415)

    def test_rejects_oversized_body(self):
        response = self._post(raw="x" * (FEEDBACK_MAX_BYTES + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(NodeFeedback.objects.count(), 0)

    def test_rejects_malformed_duplicate_nan_and_bad_unicode(self):
        self.assertEqual(self._post(raw="{not-json").status_code, 400)

        duplicate = '{"feedback_id":"a","feedback_id":"b","job_id":"x","issues":[]}'
        self.assertEqual(self._post(raw=duplicate).status_code, 400)

        nan = json.dumps(
            self._payload(issues=[{"code": "x", "message": "y", "field": float("nan")}])
        )
        self.assertEqual(self._post(raw=nan).status_code, 400)

        unicode_raw = json.dumps(self._payload()).replace(
            '"statement.pdf"', '"\\ud800"'
        )
        self.assertEqual(self._post(raw=unicode_raw).status_code, 400)

    # --- job lookup / status ---------------------------------------------

    def test_unknown_job_returns_404(self):
        response = self._post(self._payload(job_id="d" * 32))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(NodeFeedback.objects.count(), 0)

    def test_pending_and_failed_are_conflict(self):
        for status in (NodeJob.Status.PENDING, NodeJob.Status.FAILED):
            with self.subTest(status=status):
                NodeJob.objects.filter(pk=self.job.pk).update(status=status)
                response = self._post(self._payload(feedback_id=str(uuid.uuid4())))
                self.assertEqual(response.status_code, 409)
        self.assertEqual(NodeFeedback.objects.count(), 0)

    def test_accepts_waiting_sending_uncertain_completed(self):
        for index, status in enumerate(
            (NodeJob.Status.WAITING, NodeJob.Status.SENDING, NodeJob.Status.UNCERTAIN, NodeJob.Status.COMPLETED),
            start=1,
        ):
            with self.subTest(status=status):
                NodeJob.objects.filter(pk=self.job.pk).update(status=status)
                response = self._post(self._payload(feedback_id=str(uuid.uuid4())))
                self.assertEqual(response.status_code, 200, response.content.decode())

    # --- acceptance / storage --------------------------------------------

    def test_valid_feedback_is_stored_normalized_and_isolated(self):
        application = self.version.application
        events_before = application.events.count()
        versions_before = application.versions.count()
        job_result_before = NodeJob.objects.get(pk=self.job.pk).result

        response = self._post(self._payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "feedback_id": self.feedback_id,
                "job_id": self.job.external_job_id,
                "issues_count": 1,
            },
        )
        stored = NodeFeedback.objects.get(pk=self.feedback_id)
        self.assertEqual(stored.job_id, self.job.pk)
        self.assertEqual(stored.issues[0]["message"], "Не найден номер счёта")
        self.assertEqual(stored.issues[0]["code"], "missing_field")
        self.assertEqual(stored.issues[0]["document_id"], str(self.document.pk))
        self.assertTrue(stored.payload_sha256)

        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.IN_REVIEW)
        self.assertEqual(application.events.count(), events_before)
        self.assertEqual(application.versions.count(), versions_before)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, NodeJob.Status.COMPLETED)
        self.assertEqual(self.job.result, job_result_before)

    def test_identical_replay_is_idempotent_once(self):
        self.assertEqual(self._post(self._payload()).status_code, 200)
        created_at = NodeFeedback.objects.get(pk=self.feedback_id).created_at

        reordered = {
            "issues": [
                {
                    "suggestion": "Загрузите полный документ",
                    "filename": "statement.pdf",
                    "document_id": str(self.document.pk),
                    "field": "account_number",
                    "message": "  Не найден номер счёта  ",
                    "code": "missing_field",
                }
            ],
            "job_id": self.job.external_job_id,
            "feedback_id": self.feedback_id,
        }
        response = self._post(reordered)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(NodeFeedback.objects.count(), 1)
        self.assertEqual(NodeFeedback.objects.get(pk=self.feedback_id).created_at, created_at)

    def test_same_id_with_different_body_conflicts(self):
        self.assertEqual(self._post(self._payload()).status_code, 200)
        changed = self._payload(
            issues=[{"code": "other_code", "message": "Другое замечание"}]
        )
        response = self._post(changed)
        self.assertEqual(response.status_code, 409)
        stored = NodeFeedback.objects.get(pk=self.feedback_id)
        self.assertEqual(stored.issues[0]["code"], "missing_field")

    # --- payload validation ----------------------------------------------

    def test_rejects_unknown_keys_and_wrong_types(self):
        cases = [
            {**self._payload(), "extra": 1},
            self._payload(issues=[{"code": "c", "message": "m", "unknown": "x"}]),
            self._payload(issues=[]),
            self._payload(issues=[{"code": "c", "message": "m"}] * 51),
            self._payload(feedback_id="not-a-uuid"),
            self._payload(job_id="C" * 32),
            self._payload(issues="nope"),
            self._payload(issues=[{"message": "m"}]),
            self._payload(issues=[{"code": "c"}]),
            self._payload(issues=[{"code": "BadCode", "message": "m"}]),
            self._payload(issues=[{"code": "c" * 65, "message": "m"}]),
            self._payload(issues=[{"code": "c", "message": "   "}]),
            self._payload(issues=[{"code": "c", "message": "m" * 1001}]),
            self._payload(issues=[{"code": "c", "message": "m", "field": 5}]),
            self._payload(issues=[{"code": "c", "message": "m", "suggestion": "s" * 1001}]),
        ]
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                response = self._post(payload)
                self.assertEqual(response.status_code, 400, response.content.decode())
        self.assertEqual(NodeFeedback.objects.count(), 0)

    def test_document_id_must_belong_to_version(self):
        other_version = self._make_version(number=2)
        foreign = self._document(other_version, name="other.pdf")
        response = self._post(
            self._payload(
                issues=[
                    {
                        "code": "missing_field",
                        "message": "m",
                        "document_id": str(foreign.pk),
                    }
                ]
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(NodeFeedback.objects.count(), 0)

    def test_required_fields_reject_null_and_wrong_types(self):
        cases = [
            self._payload(feedback_id=None),
            self._payload(feedback_id=123),
            self._payload(job_id=None),
            self._payload(job_id=123),
            self._payload(issues=None),
            self._payload(issues=[None]),
            self._payload(issues=[{"code": None, "message": "m"}]),
            self._payload(issues=[{"code": "c", "message": None}]),
            self._payload(issues=[{"code": 1, "message": "m"}]),
        ]
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                response = self._post(payload)
                self.assertEqual(response.status_code, 400, response.content.decode())
        self.assertEqual(NodeFeedback.objects.count(), 0)

    def test_optional_null_fields_are_treated_as_absent(self):
        response = self._post(
            self._payload(
                issues=[
                    {
                        "code": "missing_field",
                        "message": "m",
                        "field": None,
                        "document_id": None,
                        "filename": None,
                        "suggestion": None,
                    }
                ]
            )
        )
        self.assertEqual(response.status_code, 200)
        stored = NodeFeedback.objects.get(pk=self.feedback_id)
        self.assertEqual(stored.issues, [{"code": "missing_field", "message": "m"}])

    def test_boundary_lengths_and_max_issue_count_are_accepted(self):
        forty_nine_more = [
            {"code": "issue_code", "message": "m"} for _ in range(49)
        ]
        response = self._post(
            self._payload(
                issues=[
                    {"code": "a" * 64, "message": "m" * 1000},
                    *forty_nine_more,
                ]
            )
        )
        self.assertEqual(response.status_code, 200, response.content.decode())
        stored = NodeFeedback.objects.get(pk=self.feedback_id)
        self.assertEqual(len(stored.issues), 50)
        self.assertEqual(stored.issues[0]["code"], "a" * 64)
        self.assertEqual(len(stored.issues[0]["message"]), 1000)

    def test_filename_alone_is_display_only_and_accepted(self):
        response = self._post(
            self._payload(
                issues=[{"code": "unreadable", "message": "m", "filename": "../etc/passwd"}]
            )
        )
        self.assertEqual(response.status_code, 200)
        stored = NodeFeedback.objects.get(pk=self.feedback_id)
        self.assertEqual(stored.issues[0]["filename"], "../etc/passwd")
        self.assertNotIn("document_id", stored.issues[0])
