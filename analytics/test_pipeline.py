"""Сквозной HTTP-тест: Django LiveServer + настоящие клиенты очередей + Worker.

Тест не подменяет методы стадий и не подделывает HTTP-ответы: Worker получает
реальные QueueClient/AnalyticsQueueClient, ходит по сети на LiveServerTestCase,
а состояние проверяется в тестовой БД и в HTML банковской/бизнес-страниц.
"""

import hashlib
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import LiveServerTestCase, override_settings
from django.urls import reverse

from analytics.models import AnalysisRun
from applications.models import Application, ApplicationEvent
from core.testing import make_user
from documents.models import (
    ApplicationDocument,
    DocumentProcessingJob,
    SerializedDocument,
)
from documents.queue import claim_next_job, fail_job
from organizations.models import Organization, OrganizationMembership
from products.models import Product
from users.models import User

from ml.queue_client import AnalyticsQueueClient, QueueClient
from ml.worker import Worker, WorkerConfig

LLM_TOKEN = "pipeline-test-llm-token"
ANALYTICS_TOKEN = "pipeline-test-analytics-token"

SCENARIOS = {
    "normal": {
        "run_status": AnalysisRun.Status.READY,
        "recommendation": "PROCEED_TO_REVIEW",
        "recommendation_label": "Передать на ручное рассмотрение",
        "scenario_label": "Стандартный сценарий",
        "components": {
            "evidence": "OK",
            "radar": "OK",
            "cashflow": "OK",
            "scoring": "OK",
        },
    },
    "missing_data": {
        "run_status": AnalysisRun.Status.PARTIAL,
        "recommendation": "REQUEST_INFORMATION",
        "recommendation_label": "Запросить дополнительную информацию",
        "scenario_label": "Недостаточно данных",
        "components": {
            "evidence": "INCOMPLETE",
            "radar": "UNKNOWN",
            "cashflow": "UNKNOWN",
            "scoring": "UNKNOWN",
        },
    },
    "manual_review": {
        "run_status": AnalysisRun.Status.PARTIAL,
        "recommendation": "MANUAL_REVIEW",
        "recommendation_label": "Требуется ручная проверка",
        "scenario_label": "Ручная проверка",
        "components": {
            "evidence": "OK",
            "radar": "MANUAL_REVIEW",
            "cashflow": "OK",
            "scoring": "OK",
        },
    },
}

FAILED_EXTRACTION = {
    "run_status": AnalysisRun.Status.PARTIAL,
    "recommendation": "REQUEST_INFORMATION",
    "recommendation_label": "Запросить дополнительную информацию",
    "scenario_label": "Недостаточно данных",
    "components": {
        "evidence": "UNKNOWN",
        "radar": "UNKNOWN",
        "cashflow": "UNKNOWN",
        "scoring": "UNKNOWN",
    },
}

RESULT_KEYS = {
    "schema_version",
    "is_demo",
    "model_version",
    "policy_version",
    "scenario_id",
    "recommendation",
    "summary",
    "reason_codes",
    "components",
}


@override_settings(
    LLM_SERVICE_TOKEN=LLM_TOKEN,
    ANALYTICS_SERVICE_TOKEN=ANALYTICS_TOKEN,
    LLM_JOB_LEASE_SECONDS=600,
    ANALYTICS_JOB_LEASE_SECONDS=600,
    LLM_RETRY_DELAY_SECONDS=0,
    ANALYTICS_RETRY_DELAY_SECONDS=0,
)
class WorkerHttpPipelineTests(LiveServerTestCase):
    """Production-подобный путь: форма -> БД -> live HTTP -> Worker -> БД/HTML."""

    def setUp(self):
        # LiveServerTestCase наследует TransactionTestCase, поэтому setUpTestData
        # здесь не используем: фикстуры должны существовать в каждом тесте.
        self.product = Product.objects.create(
            code="working-capital",
            name="Оборотный кредит",
            description="На текущие расходы",
            min_amount=Decimal("100000.00"),
            max_amount=Decimal("30000000.00"),
            min_term_months=3,
            max_term_months=36,
            badge="Основной сценарий",
        )
        self.business_user = make_user("pipeline-owner@example.test")
        self.bank_user = make_user(
            "pipeline-bank@example.test", User.Role.BANK_REVIEWER
        )
        self.organization = Organization.objects.create(
            name="ООО Пайплайн",
            inn="7712345678",
            verification_status=Organization.VerificationStatus.VERIFIED,
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.business_user,
            role=OrganizationMembership.Role.OWNER,
        )

    def _submit_application(self, scenario):
        content = (
            f"%PDF-1.4 synthetic pipeline fixture for scenario {scenario}"
        ).encode("utf-8")
        file_name = f"{scenario}-pipeline-statement.pdf"
        payload = {
            "organization_name": "ООО Пайплайн",
            "inn": "7712345678",
            "need": "working",
            "product": self.product.pk,
            "amount": "1500000.00",
            "term_months": "12",
            "purpose": "Закупка сезонной партии товара на три месяца",
            "document_statement": [
                SimpleUploadedFile(
                    file_name,
                    content,
                    content_type="application/pdf",
                )
            ],
        }

        self.client.force_login(self.business_user)
        response = self.client.post(reverse("business-cabinet"), payload)
        self.assertEqual(response.status_code, 302, response.content.decode())

        application = Application.objects.get()
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertEqual(application.current_version, 1)
        self.assertEqual(application.versions.count(), 1)

        document = ApplicationDocument.objects.get(version__application=application)
        self.assertEqual(document.original_name, file_name)
        self.assertEqual(document.size, len(content))
        self.assertEqual(document.sha256, hashlib.sha256(content).hexdigest())
        with document.file.storage.open(document.file.name, "rb") as stored:
            self.assertEqual(stored.read(), content)

        job = DocumentProcessingJob.objects.get(version__application=application)
        self.assertEqual(job.status, DocumentProcessingJob.Status.PENDING)
        self.assertEqual(job.attempts, 0)

        run = AnalysisRun.objects.get(version__application=application)
        self.assertEqual(run.status, AnalysisRun.Status.WAITING)
        self.assertEqual(run.attempts, 0)
        return application, document, content

    def _config(self, scenario, *, stage, worker_id):
        queue_url = (
            f"{self.live_server_url}/api/v1/llm"
            if stage in ("documents", "all")
            else ""
        )
        analytics_url = (
            f"{self.live_server_url}/api/v1/analytics"
            if stage in ("analytics", "all")
            else ""
        )
        return WorkerConfig(
            stage=stage,
            scenario=scenario,
            poll_seconds=60,
            worker_id=worker_id,
            http_timeout=10,
            queue_url=queue_url,
            llm_token=LLM_TOKEN if stage in ("documents", "all") else "",
            analytics_url=analytics_url,
            analytics_token=ANALYTICS_TOKEN if stage in ("analytics", "all") else "",
        )

    def _run_all(self, scenario, *, worker_id="pipeline-all"):
        worker = Worker(self._config(scenario, stage="all", worker_id=worker_id))
        # Настоящие сетевые клиенты, а не mock-объекты.
        self.assertIsInstance(worker.document_client, QueueClient)
        self.assertIsInstance(worker.analytics_client, AnalyticsQueueClient)
        self.assertEqual(worker.document_client.token, LLM_TOKEN)
        self.assertEqual(worker.analytics_client.token, ANALYTICS_TOKEN)
        self.assertNotEqual(worker.document_client.token, worker.analytics_client.token)
        self.assertTrue(worker.document_client.base_url.startswith(self.live_server_url))
        self.assertTrue(
            worker.analytics_client.base_url.startswith(self.live_server_url)
        )
        self.assertEqual(worker.run(once=True), 0)
        return worker

    def _assert_documents_persisted(self, application, document, content, scenario):
        job = DocumentProcessingJob.objects.get(version__application=application)
        self.assertEqual(job.status, DocumentProcessingJob.Status.COMPLETED)
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.completed_at)
        self.assertEqual(job.result_summary.get("document_count"), 1)
        self.assertIs(job.result_summary.get("is_demo"), True)
        self.assertIs(job.result_summary.get("ocr_performed"), False)
        self.assertEqual(job.result_summary.get("extraction_method"), "stub")

        serialized = SerializedDocument.objects.get(job=job)
        self.assertEqual(serialized.source_document_id, document.pk)
        payload = serialized.payload
        self.assertIs(payload["is_demo"], True)
        self.assertIs(payload["synthetic_fixture"], True)
        self.assertEqual(payload["extraction_method"], "stub")
        self.assertIs(payload["ocr_performed"], False)
        self.assertEqual(payload["scenario_id"], scenario)
        self.assertEqual(
            payload["verified_download"]["sha256"],
            hashlib.sha256(content).hexdigest(),
        )
        self.assertEqual(payload["verified_download"]["byte_count"], len(content))
        return job, serialized

    def _assert_run_result(self, application, expected, *, extraction_status, scenario_id):
        run = AnalysisRun.objects.get(version__application=application)
        self.assertEqual(run.status, expected["run_status"])
        self.assertEqual(run.extraction_status, extraction_status)
        self.assertEqual(run.attempts, 1)
        self.assertIsNotNone(run.completed_at)
        self.assertEqual(run.result["scenario_id"], scenario_id)
        self.assertEqual(run.result["recommendation"], expected["recommendation"])
        self.assertIs(run.result["is_demo"], True)
        self.assertEqual(run.result["model_version"], "stub-1")
        self.assertEqual(run.result["policy_version"], "demo-1")
        self.assertIn("DEMO_ONLY", run.result["reason_codes"])
        self.assertNotIn("probability_of_default", run.result)
        self.assertEqual(set(run.result), RESULT_KEYS)
        for name, status in expected["components"].items():
            self.assertEqual(run.result["components"][name]["status"], status)
        return run

    def _assert_pages(self, application, expected):
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)

        self.client.force_login(self.bank_user)
        bank_response = self.client.get(
            reverse("bank-application-detail", args=[application.pk])
        )
        self.assertEqual(bank_response.status_code, 200)
        self.assertContains(bank_response, "ДЕМО")
        self.assertContains(bank_response, expected["recommendation_label"])
        self.assertContains(bank_response, expected["scenario_label"])
        self.assertContains(bank_response, "Коды оснований")
        self.assertContains(bank_response, "stub-1")
        self.assertContains(bank_response, "demo-1")
        if expected["run_status"] == AnalysisRun.Status.PARTIAL:
            self.assertContains(bank_response, "Частичный результат")
        else:
            self.assertNotContains(bank_response, "Частичный результат")

        self.client.force_login(self.business_user)
        business_response = self.client.get(
            reverse("business-application-detail", args=[application.pk])
        )
        self.assertEqual(business_response.status_code, 200)
        for marker in (
            "ДЕМО",
            "Коды оснований",
            "stub-1",
            "demo-1",
            expected["recommendation_label"],
            "synthetic_",
            "verified_download",
            "is_demo",
            "extraction_method",
            "input_hash",
            "source_hash",
            "lease_token",
        ):
            self.assertNotContains(business_response, marker)

    def _assert_second_all_run_is_noop(self, application, scenario):
        job = DocumentProcessingJob.objects.get(version__application=application)
        run = AnalysisRun.objects.get(version__application=application)
        job_completed_at = job.completed_at
        run_completed_at = run.completed_at
        run_result = run.result
        job_attempts = job.attempts
        run_attempts = run.attempts
        serialized_count = SerializedDocument.objects.filter(job=job).count()
        event_count = ApplicationEvent.objects.filter(application=application).count()

        worker = self._run_all(scenario, worker_id="pipeline-second-all")
        # Клиент трактует 204 от обеих очередей как None: работы больше нет.
        self.assertIsNone(worker.document_client.claim("pipeline-probe"))
        self.assertIsNone(worker.analytics_client.claim("pipeline-probe"))

        job.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(job.status, DocumentProcessingJob.Status.COMPLETED)
        self.assertEqual(job.attempts, job_attempts)
        self.assertEqual(job.completed_at, job_completed_at)
        self.assertEqual(run.status, SCENARIOS[scenario]["run_status"])
        self.assertEqual(run.attempts, run_attempts)
        self.assertEqual(run.completed_at, run_completed_at)
        self.assertEqual(run.result, run_result)
        self.assertEqual(
            SerializedDocument.objects.filter(job=job).count(), serialized_count
        )
        self.assertEqual(
            ApplicationEvent.objects.filter(application=application).count(),
            event_count,
        )
        self.assertEqual(
            Application.objects.get(pk=application.pk).status,
            Application.Status.SUBMITTED,
        )

    def _exercise_scenario(self, scenario):
        application, document, content = self._submit_application(scenario)
        expected = SCENARIOS[scenario]
        self._run_all(scenario)
        self._assert_documents_persisted(application, document, content, scenario)
        self._assert_run_result(
            application,
            expected,
            extraction_status="COMPLETED",
            scenario_id=scenario,
        )
        self._assert_pages(application, expected)
        self._assert_second_all_run_is_noop(application, scenario)

    def test_normal_scenario_over_real_http(self):
        self._exercise_scenario("normal")

    def test_missing_data_scenario_over_real_http(self):
        self._exercise_scenario("missing_data")

    def test_manual_review_scenario_over_real_http(self):
        self._exercise_scenario("manual_review")

    def test_failed_extraction_analytics_only_worker_gets_partial_unknown(self):
        application, document, _content = self._submit_application("normal")
        job = DocumentProcessingJob.objects.get(version__application=application)

        claimed = claim_next_job(worker_id="pipeline-pre-fail")
        self.assertEqual(claimed.pk, job.pk)
        self.assertEqual(claimed.attempts, 1)
        fail_job(
            job_id=job.pk,
            lease_token=claimed.lease_token,
            error_message="Synthetic permanent extraction failure",
            retryable=False,
        )
        job.refresh_from_db()
        self.assertEqual(job.status, DocumentProcessingJob.Status.FAILED)
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.last_error, "Synthetic permanent extraction failure")
        # Никакого fake complete: результатов извлечения нет.
        self.assertFalse(SerializedDocument.objects.filter(job=job).exists())
        self.assertEqual(
            AnalysisRun.objects.get(version__application=application).status,
            AnalysisRun.Status.WAITING,
        )

        worker = Worker(
            self._config("normal", stage="analytics", worker_id="pipeline-analytics-only")
        )
        self.assertIsNone(worker.document_client)
        self.assertIsInstance(worker.analytics_client, AnalyticsQueueClient)
        self.assertEqual(worker.analytics_client.token, ANALYTICS_TOKEN)
        self.assertTrue(
            worker.analytics_client.base_url.startswith(self.live_server_url)
        )
        self.assertEqual(worker.run(once=True), 0)

        run = self._assert_run_result(
            application,
            FAILED_EXTRACTION,
            extraction_status="FAILED",
            scenario_id="missing_data",
        )
        self.assertEqual(run.result["components"]["evidence"]["status"], "UNKNOWN")
        self.assertNotIn(
            "OK",
            {item["status"] for item in run.result["components"].values()},
        )
        self._assert_pages(application, FAILED_EXTRACTION)

        before_result = run.result
        run_attempts = run.attempts
        second = Worker(
            self._config(
                "normal", stage="analytics", worker_id="pipeline-analytics-second"
            )
        )
        self.assertEqual(second.run(once=True), 0)
        self.assertIsNone(second.analytics_client.claim("pipeline-probe"))
        run.refresh_from_db()
        self.assertEqual(run.attempts, run_attempts)
        self.assertEqual(run.result, before_result)
        self.assertEqual(
            Application.objects.get(pk=application.pk).status,
            Application.Status.SUBMITTED,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()