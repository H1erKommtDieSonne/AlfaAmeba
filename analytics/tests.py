import json
from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ApplicationVersion
from core.testing import CreditTestCase
from documents.models import DocumentProcessingJob
from documents.queue import claim_next_job, complete_job, fail_job

from .models import AnalysisRun
from .services import (
    _locked,
    build_input_hash,
    build_source_hash,
    current_analysis_run,
)

ANALYTICS_TOKEN = "test-analytics-token"
HEADERS = {"HTTP_AUTHORIZATION": f"Bearer {ANALYTICS_TOKEN}"}


def demo_result(**overrides):
    result = {
        "schema_version": "1.0",
        "is_demo": True,
        "model_version": "stub-1",
        "policy_version": "demo-1",
        "scenario_id": "normal",
        "recommendation": "PROCEED_TO_REVIEW",
        "summary": "Демонстрационный результат, не кредитное решение",
        "reason_codes": ["DEMO_ONLY", "EVIDENCE_OK"],
        "components": {
            "evidence": {"status": "OK", "summary": "Документы разобраны"},
            "radar": {"status": "OK", "summary": "Демо-показатели в норме"},
            "cashflow": {"status": "OK", "summary": "Денежный поток условно стабилен"},
            "scoring": {"status": "OK", "summary": "Демо-скоринг без реального PD"},
        },
    }
    result.update(overrides)
    return result


@override_settings(
    ANALYTICS_SERVICE_TOKEN=ANALYTICS_TOKEN,
    LLM_SERVICE_TOKEN="test-llm-token",
    ANALYTICS_RETRY_DELAY_SECONDS=0,
)
class AnalyticsQueueTests(CreditTestCase):
    def _submit(self):
        self.client.force_login(self.business_user)
        response = self.client.post(reverse("business-cabinet"), self.valid_payload())
        self.assertEqual(response.status_code, 302, response.content.decode())
        application = Application.objects.get()
        run = AnalysisRun.objects.get(version__application=application)
        return application, run

    def _complete_extraction(self, application):
        job = DocumentProcessingJob.objects.get(version__application=application)
        claimed = claim_next_job(worker_id="llm-test")
        self.assertEqual(claimed.pk, job.pk)
        complete_job(
            job_id=job.pk,
            lease_token=claimed.lease_token,
            documents=[
                {
                    "document_id": str(document.pk),
                    "document_type": "bank_statement",
                    "schema_version": "1.0",
                    "confidence": 0.93,
                    "payload": {"account": "synthetic", "transactions": []},
                }
                for document in job.version.documents.all()
            ],
            summary={"document_count": job.version.documents.count()},
        )
        job.refresh_from_db()
        self.assertEqual(job.status, DocumentProcessingJob.Status.COMPLETED)

    def _claim(self):
        return self.client.post(
            reverse("analytics-job-claim"),
            data=json.dumps({"worker_id": "analytics-worker-1"}),
            content_type="application/json",
            **HEADERS,
        )

    def _complete(self, claimed, result=None, *, input_hash=None, lease_token=None):
        return self.client.post(
            reverse("analytics-job-complete", args=(claimed["job_id"],)),
            data=json.dumps(
                {
                    "lease_token": lease_token or claimed["lease_token"],
                    "input_hash": input_hash or claimed["input_hash"],
                    "result": result or demo_result(),
                }
            ),
            content_type="application/json",
            **HEADERS,
        )

    def test_auth_worker_id_and_token_separation(self):
        self._submit()

        missing = self.client.post(
            reverse("analytics-job-claim"),
            data=json.dumps({"worker_id": "worker"}),
            content_type="application/json",
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(set(missing.json()), {"error"})

        llm_token = self.client.post(
            reverse("analytics-job-claim"),
            data=json.dumps({"worker_id": "worker"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-llm-token",
        )
        self.assertEqual(llm_token.status_code, 401)

        wrong_type = self.client.post(
            reverse("analytics-job-claim"),
            data=json.dumps({"worker_id": 42}),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(wrong_type.status_code, 400)

        missing_worker = self.client.post(
            reverse("analytics-job-claim"),
            data="{}",
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(missing_worker.status_code, 400)

        malformed = self.client.post(
            reverse("analytics-job-claim"),
            data="{not json",
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(malformed.status_code, 400)

        allowed = self._claim()
        self.assertEqual(allowed.status_code, 204)

    @override_settings(ANALYTICS_SERVICE_TOKEN="")
    def test_unconfigured_token_returns_503(self):
        response = self.client.post(
            reverse("analytics-job-claim"),
            data=json.dumps({"worker_id": "worker"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer anything",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(set(response.json()), {"error"})

    def test_waiting_run_only_claims_after_extraction_completed(self):
        application, run = self._submit()
        self.assertEqual(run.status, AnalysisRun.Status.WAITING)
        self.assertTrue(run.source_hash)
        self.assertEqual(run.input_hash, "")

        before = self._claim()
        self.assertEqual(before.status_code, 204)
        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.WAITING)

        self._complete_extraction(application)

        claimed = self._claim()
        self.assertEqual(claimed.status_code, 200)
        payload = claimed.json()
        self.assertEqual(payload["job_id"], str(run.pk))
        self.assertEqual(payload["application_id"], str(application.pk))
        self.assertEqual(payload["version_id"], str(run.version_id))
        self.assertEqual(payload["version_number"], 1)
        self.assertEqual(payload["attempt"], 1)
        self.assertEqual(payload["extraction_status"], "COMPLETED")
        self.assertEqual(payload["model_version"], "stub-1")
        self.assertEqual(payload["policy_version"], "demo-1")
        self.assertEqual(payload["application"]["organization_name"], "ООО Север")
        self.assertEqual(len(payload["documents"]), 1)
        self.assertEqual(payload["documents"][0]["document_type"], "bank_statement")
        self.assertEqual(payload["documents"][0]["payload"]["account"], "synthetic")
        self.assertEqual(payload["documents"][0]["confidence"], "0.9300")
        self.assertEqual(len(payload["input_hash"]), 64)

        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.PROCESSING)
        self.assertEqual(payload["input_hash"], run.input_hash)

    def test_lock_targets_only_analysis_run_rows(self):
        self._submit()
        queryset = AnalysisRun.objects.filter(
            status=AnalysisRun.Status.WAITING,
            version__processing_job__status__in=(
                DocumentProcessingJob.Status.COMPLETED,
                DocumentProcessingJob.Status.FAILED,
            ),
        ).select_related("version__processing_job")
        features = connection.features
        with (
            transaction.atomic(),
            mock.patch.object(features, "has_select_for_update", True),
            mock.patch.object(features, "has_select_for_update_of", True),
            mock.patch.object(features, "has_select_for_update_skip_locked", True),
        ):
            sql = str(_locked(queryset).query)

        # Фильтр по job делает join внутренним, но блокируем только очередь.
        self.assertIn('INNER JOIN "documents_documentprocessingjob"', sql)
        self.assertIn('FOR UPDATE OF "analytics_analysisrun" SKIP LOCKED', sql)
        lock_clause = sql.split("FOR UPDATE", 1)[1]
        self.assertNotIn('"documents_documentprocessingjob"', lock_clause)
        self.assertNotIn('"applications_applicationversion"', lock_clause)

    def test_complete_validation_idempotence_and_conflict(self):
        application, _ = self._submit()
        self._complete_extraction(application)
        claimed = self._claim().json()
        application_status = Application.objects.get(pk=application.pk).status

        malformed = self.client.post(
            reverse("analytics-job-complete", args=(claimed["job_id"],)),
            data="{not json",
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(malformed.status_code, 400)

        wrong_lease = self._complete(
            claimed, lease_token="00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(wrong_lease.status_code, 409)

        wrong_hash = self._complete(claimed, input_hash="0" * 64)
        self.assertEqual(wrong_hash.status_code, 409)

        bad_schema = demo_result()
        bad_schema["components"]["evidence"]["status"] = "APPROVED"
        bad_schema_response = self._complete(claimed, result=bad_schema)
        self.assertEqual(bad_schema_response.status_code, 409)

        extra_field = demo_result()
        extra_field["probability_of_default"] = 0.01
        self.assertEqual(self._complete(claimed, result=extra_field).status_code, 409)
        self.assertFalse(AnalysisRun.objects.filter(result__isnull=False).exists())

        completed = self._complete(claimed)
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], AnalysisRun.Status.READY)

        run = AnalysisRun.objects.get(pk=claimed["job_id"])
        self.assertEqual(run.status, AnalysisRun.Status.READY)
        self.assertEqual(run.result["recommendation"], "PROCEED_TO_REVIEW")
        self.assertIsNotNone(run.completed_at)

        repeated = self._complete(claimed)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json(), completed.json())

        conflicting = self._complete(
            claimed, result=demo_result(summary="Другой результат")
        )
        self.assertEqual(conflicting.status_code, 409)
        run.refresh_from_db()
        self.assertEqual(run.result["summary"], "Демонстрационный результат, не кредитное решение")

        self.assertEqual(Application.objects.get(pk=application.pk).status, application_status)

    def test_partial_status_when_any_component_is_not_ok(self):
        application, _ = self._submit()
        self._complete_extraction(application)
        claimed = self._claim().json()
        result = demo_result(scenario_id="missing_data")
        result["components"]["evidence"] = {
            "status": "INCOMPLETE",
            "summary": "Часть документов не распознана",
        }
        response = self._complete(claimed, result=result)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], AnalysisRun.Status.PARTIAL)

    def test_failed_extraction_allows_unknown_analytics(self):
        application, _ = self._submit()
        job = DocumentProcessingJob.objects.get(version__application=application)
        claimed_documents = claim_next_job(worker_id="llm-test")
        fail_job(
            job_id=job.pk,
            lease_token=claimed_documents.lease_token,
            error_message="Извлечение недоступно",
            retryable=False,
        )

        claimed = self._claim()
        self.assertEqual(claimed.status_code, 200)
        payload = claimed.json()
        self.assertEqual(payload["extraction_status"], "FAILED")
        self.assertEqual(payload["documents"][0]["payload"], {})
        self.assertIsNone(payload["documents"][0]["confidence"])

        result = demo_result(scenario_id="missing_data")
        result["components"]["evidence"] = {
            "status": "UNKNOWN",
            "summary": "Документы не извлечены",
        }
        response = self._complete(payload, result=result)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], AnalysisRun.Status.PARTIAL)

    def test_expired_lease_reclaim_and_last_attempt_failure(self):
        application, run = self._submit()
        self._complete_extraction(application)
        run.max_attempts = 2
        run.save(update_fields=("max_attempts",))

        first = self._claim().json()
        failed = self.client.post(
            reverse("analytics-job-fail", args=(first["job_id"],)),
            data=json.dumps(
                {
                    "lease_token": first["lease_token"],
                    "error": "Временная ошибка",
                    "retryable": True,
                }
            ),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(failed.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.PENDING)

        second = self._claim().json()
        self.assertEqual(second["attempt"], 2)
        self.assertNotEqual(second["lease_token"], first["lease_token"])

        run.lease_expires_at = timezone.now() - timedelta(seconds=1)
        run.save(update_fields=("lease_expires_at",))

        empty = self._claim()
        self.assertEqual(empty.status_code, 204)
        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.FAILED)
        self.assertIsNotNone(run.completed_at)

    def test_expired_non_last_lease_is_reclaimed(self):
        application, run = self._submit()
        self._complete_extraction(application)
        first = self._claim().json()

        run.lease_expires_at = timezone.now() - timedelta(seconds=1)
        run.save(update_fields=("lease_expires_at",))

        reclaimed = self._claim()
        self.assertEqual(reclaimed.status_code, 200)
        self.assertEqual(reclaimed.json()["attempt"], 2)
        self.assertNotEqual(reclaimed.json()["lease_token"], first["lease_token"])
        run.refresh_from_db()
        self.assertEqual(run.status, AnalysisRun.Status.PROCESSING)

    def test_fail_requires_strict_boolean_and_missing_run_is_404(self):
        application, _ = self._submit()
        self._complete_extraction(application)
        claimed = self._claim().json()

        invalid = self.client.post(
            reverse("analytics-job-fail", args=(claimed["job_id"],)),
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
        self.assertEqual(invalid.status_code, 400)

        missing = self.client.post(
            reverse("analytics-job-complete", args=("00000000-0000-0000-0000-000000000000",)),
            data=json.dumps({"lease_token": claimed["lease_token"], "input_hash": "x", "result": {}}),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(missing.status_code, 404)

    def test_unhashable_result_enums_return_409_not_500(self):
        application, _ = self._submit()
        self._complete_extraction(application)
        claimed = self._claim().json()

        invalid_results = []
        for field, value in (
            ("scenario_id", ["normal"]),
            ("recommendation", {"value": "PROCEED_TO_REVIEW"}),
        ):
            result = demo_result()
            result[field] = value
            invalid_results.append(result)
        for status in ([], {"value": "OK"}):
            result = demo_result()
            result["components"]["evidence"]["status"] = status
            invalid_results.append(result)

        for result in invalid_results:
            response = self._complete(claimed, result=result)
            self.assertEqual(response.status_code, 409, response.content.decode())

        run = AnalysisRun.objects.get(pk=claimed["job_id"])
        self.assertEqual(run.status, AnalysisRun.Status.PROCESSING)
        self.assertIsNone(run.result)

    def test_auth_requires_bearer_prefix_and_rejects_non_ascii(self):
        self._submit()
        for authorization in (
            ANALYTICS_TOKEN,
            f"Bearer{ANALYTICS_TOKEN}",
            "Bearer",
            "Bearer ",
            "Bearer токен",
            f"bearer {ANALYTICS_TOKEN}",
        ):
            response = self.client.post(
                reverse("analytics-job-claim"),
                data=json.dumps({"worker_id": "worker"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=authorization,
            )
            self.assertEqual(response.status_code, 401, authorization)
            self.assertEqual(set(response.json()), {"error"})

        self.assertEqual(self._claim().status_code, 204)

    def test_documents_auth_requires_bearer_prefix_and_rejects_non_ascii(self):
        for authorization in ("test-llm-token", "Bearer", "Bearer токен"):
            response = self.client.post(
                reverse("llm-job-claim"),
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION=authorization,
            )
            self.assertEqual(response.status_code, 401, authorization)
            self.assertEqual(set(response.json()), {"error"})

        allowed = self.client.post(
            reverse("llm-job-claim"),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-llm-token",
        )
        self.assertEqual(allowed.status_code, 204)

    def test_hashes_bind_model_and_policy_versions(self):
        application, run = self._submit()
        version = run.version
        default_source = build_source_hash(version, run.policy_snapshot)
        self.assertEqual(run.source_hash, default_source)
        self.assertNotEqual(
            default_source,
            build_source_hash(
                version, run.policy_snapshot, model_version="other-model"
            ),
        )
        self.assertNotEqual(
            default_source,
            build_source_hash(
                version, run.policy_snapshot, policy_version="other-policy"
            ),
        )

        base_input = build_input_hash(version, extraction_status="COMPLETED")
        self.assertNotEqual(
            base_input,
            build_input_hash(
                version,
                extraction_status="COMPLETED",
                model_version="other-model",
            ),
        )
        self.assertNotEqual(
            base_input,
            build_input_hash(
                version,
                extraction_status="COMPLETED",
                policy_version="other-policy",
            ),
        )

    def test_claim_and_complete_use_run_model_and_policy_versions(self):
        application, run = self._submit()
        self._complete_extraction(application)
        version = run.version
        run.model_version = "custom-model"
        run.policy_version = "custom-policy"
        run.source_hash = build_source_hash(
            version,
            run.policy_snapshot,
            model_version=run.model_version,
            policy_version=run.policy_version,
        )
        run.save(update_fields=("model_version", "policy_version", "source_hash"))

        claimed_response = self._claim()
        self.assertEqual(
            claimed_response.status_code, 200, claimed_response.content.decode()
        )
        claimed = claimed_response.json()
        expected_input = build_input_hash(
            version,
            extraction_status=claimed["extraction_status"],
            policy_snapshot=run.policy_snapshot,
            source_hash=run.source_hash,
            model_version="custom-model",
            policy_version="custom-policy",
        )
        self.assertEqual(claimed["model_version"], "custom-model")
        self.assertEqual(claimed["policy_version"], "custom-policy")
        self.assertEqual(claimed["input_hash"], expected_input)

        completed = self._complete(
            claimed,
            result=demo_result(
                model_version="custom-model", policy_version="custom-policy"
            ),
        )
        self.assertEqual(completed.status_code, 200, completed.content.decode())
        self.assertEqual(completed.json()["status"], AnalysisRun.Status.READY)

    def test_model_guard_protects_finished_result_status_and_identity(self):
        application, run = self._submit()
        stale = AnalysisRun.objects.get(pk=run.pk)
        result = demo_result()
        run.result = result
        run.status = AnalysisRun.Status.READY
        run.completed_at = timezone.now()
        run.save(update_fields=("result", "status", "completed_at"))

        finished = AnalysisRun.objects.get(pk=run.pk)
        finished.result = None
        with self.assertRaises(ValidationError):
            finished.save(update_fields=("result",))

        finished = AnalysisRun.objects.get(pk=run.pk)
        finished.result = demo_result(summary="Другой результат")
        with self.assertRaises(ValidationError):
            finished.save(update_fields=("result",))

        finished = AnalysisRun.objects.get(pk=run.pk)
        finished.status = AnalysisRun.Status.PROCESSING
        with self.assertRaises(ValidationError):
            finished.save(update_fields=("status",))

        finished = AnalysisRun.objects.get(pk=run.pk)
        finished.model_version = "other-model"
        with self.assertRaises(ValidationError):
            finished.save(update_fields=("model_version",))

        finished = AnalysisRun.objects.get(pk=run.pk)
        finished.input_hash = "f" * 64
        with self.assertRaises(ValidationError):
            finished.save(update_fields=("input_hash",))

        other_version = ApplicationVersion.objects.create(
            application=application,
            version_number=2,
            product=self.product,
            amount="1600000.00",
            term_months=12,
            purpose="Другая версия для проверки неизменяемости",
            submitted_at=timezone.now(),
            snapshot={},
        )
        finished = AnalysisRun.objects.get(pk=run.pk)
        finished.version = other_version
        with self.assertRaises(ValidationError):
            finished.save(update_fields=("version",))

        stale.status = AnalysisRun.Status.PROCESSING
        with self.assertRaises(ValidationError):
            stale.save(update_fields=("status",))

        stored = AnalysisRun.objects.get(pk=run.pk)
        self.assertEqual(stored.result, result)
        self.assertEqual(stored.status, AnalysisRun.Status.READY)
        self.assertEqual(stored.model_version, "stub-1")
        self.assertEqual(stored.version_id, run.version_id)

    def test_model_guard_allows_failed_run_retry(self):
        application, run = self._submit()
        AnalysisRun.objects.filter(pk=run.pk).update(status=AnalysisRun.Status.FAILED)
        failed = AnalysisRun.objects.get(pk=run.pk)
        failed.status = AnalysisRun.Status.PENDING
        failed.save(update_fields=("status",))
        self.assertEqual(
            AnalysisRun.objects.get(pk=run.pk).status, AnalysisRun.Status.PENDING
        )

    def test_current_run_selector_uses_exact_current_version_and_keeps_history(self):
        application, first_run = self._submit()
        second_version = ApplicationVersion.objects.create(
            application=application,
            version_number=2,
            product=self.product,
            amount="1600000.00",
            term_months=12,
            purpose="Повторная заявка с обновлёнными условиями",
            submitted_at=timezone.now(),
            snapshot={"organization_name": "ООО Север", "version": 2},
        )
        from documents.services import save_documents_and_enqueue

        save_documents_and_enqueue(
            second_version,
            [
                SimpleUploadedFile(
                    "second-statement.pdf",
                    b"%PDF-1.4 second synthetic statement",
                    content_type="application/pdf",
                )
            ],
        )
        Application.objects.filter(pk=application.pk).update(current_version=2)
        application.refresh_from_db()

        selected = current_analysis_run(application)
        self.assertEqual(selected.version_id, second_version.pk)
        self.assertNotEqual(selected.pk, first_run.pk)
        self.assertEqual(first_run.status, AnalysisRun.Status.WAITING)
        self.assertEqual(application.versions.count(), 2)
