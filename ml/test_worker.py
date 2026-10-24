"""Focused stdlib-тесты stub-воркера. Без Django и без сети (кроме localhost)."""

import hashlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from ml.queue_client import (
    ConfigurationClientError,
    PermanentClientError,
    QueueClient,
    TransientClientError,
)
from ml.stubs import (
    build_analysis_result,
    build_document_result,
    extraction_summary,
)
from ml.worker import ConfigError, Worker, WorkerConfig, load_config, main as worker_main

CONTENT = b"DEMO SYNTHETIC FILE CONTENT, NOT A REAL DOCUMENT"
SHA256 = hashlib.sha256(CONTENT).hexdigest()


def document_meta(document_id="11111111-1111-1111-1111-111111111111"):
    return {
        "document_id": document_id,
        "name": "synthetic.txt",
        "content_type": "text/plain",
        "size": len(CONTENT),
        "sha256": SHA256,
        "download_url": "http://django:8000/api/v1/llm/jobs/j/documents/d/",
    }


def document_job_claim(scenario="normal", **overrides):
    """Claim очереди документов: сырые метаданные файла, без payload."""
    claim = {
        "job_id": "22222222-2222-2222-2222-222222222222",
        "lease_token": "33333333-3333-3333-3333-333333333333",
        "lease_expires_at": "2026-10-22T12:10:00+00:00",
        "attempt": 1,
        "application_id": "44444444-4444-4444-4444-444444444444",
        "version_id": "55555555-5555-5555-5555-555555555555",
        "version_number": 1,
        "application": {"organization_name": "СИНТЕТИЧЕСКАЯ ОРГАНИЗАЦИЯ"},
        "documents": [document_meta()],
    }
    claim.update(overrides)
    return claim


def document_claim(scenario="normal", **overrides):
    """Claim аналитики: уже сериализованные документы."""
    item = build_document_result(document_meta(), CONTENT, scenario)
    claim = {
        "job_id": "22222222-2222-2222-2222-222222222222",
        "lease_token": "33333333-3333-3333-3333-333333333333",
        "lease_expires_at": "2026-10-22T12:10:00+00:00",
        "attempt": 1,
        "application_id": "44444444-4444-4444-4444-444444444444",
        "version_id": "55555555-5555-5555-5555-555555555555",
        "version_number": 1,
        "application": {"organization_name": "СИНТЕТИЧЕСКАЯ ОРГАНИЗАЦИЯ"},
        "input_hash": "a" * 64,
        "documents": [item],
        "extraction_status": "COMPLETED",
        "model_version": "stub-1",
        "policy_version": "demo-1",
    }
    claim.update(overrides)
    return claim


def base_config(**overrides):
    values = {
        "stage": "documents",
        "scenario": "normal",
        "poll_seconds": 60,
        "worker_id": "test-worker",
        "http_timeout": 5,
        "queue_url": "http://django:8000/api/v1/llm",
        "llm_token": "test-llm-token",
        "analytics_url": "http://django:8000/api/v1/analytics",
        "analytics_token": "test-analytics-token",
    }
    values.update(overrides)
    return WorkerConfig(**values)


class FakeDocumentClient:
    def __init__(self, claim=None, content=CONTENT, claim_error=None):
        self.claim_value = claim
        self.content = content
        self.claim_error = claim_error
        self.claimed_with = None
        self.completed = []
        self.failures = []

    def claim(self, worker_id):
        self.claimed_with = worker_id
        if self.claim_error:
            raise self.claim_error
        return self.claim_value

    def download(self, download_url, lease_token):
        return self.content

    def complete(self, job_id, lease_token, documents, summary=None):
        self.completed.append((job_id, lease_token, documents, summary))
        return {"status": "COMPLETED"}

    def fail(self, job_id, lease_token, error, *, retryable=True):
        self.failures.append((job_id, lease_token, error, retryable))
        return {"status": "FAILED"}


class FakeAnalyticsClient:
    def __init__(self, claim=None, claim_error=None):
        self.claim_value = claim
        self.claim_error = claim_error
        self.claimed_with = None
        self.completed = []
        self.failures = []

    def claim(self, worker_id):
        self.claimed_with = worker_id
        if self.claim_error:
            raise self.claim_error
        return self.claim_value

    def complete_analysis(self, job_id, lease_token, input_hash, result):
        self.completed.append((job_id, lease_token, input_hash, result))
        return {"status": "READY"}

    def fail(self, job_id, lease_token, error, *, retryable=True):
        self.failures.append((job_id, lease_token, error, retryable))
        return {"status": "FAILED"}


class ConfigTests(unittest.TestCase):
    def test_defaults_and_stage_specific_requirements(self):
        env = {
            "DJANGO_QUEUE_URL": "http://127.0.0.1:8000/api/v1/llm",
            "LLM_SERVICE_TOKEN": "llm",
        }
        config = load_config("documents", env)
        self.assertEqual(config.scenario, "normal")
        self.assertEqual(config.poll_seconds, 60)
        self.assertTrue(config.worker_id)
        self.assertEqual(config.analytics_url, "")

    def test_invalid_scenario_fails_clearly(self):
        env = {
            "DEMO_SCENARIO": "random_fixture",
            "DJANGO_QUEUE_URL": "http://127.0.0.1:8000/api/v1/llm",
            "LLM_SERVICE_TOKEN": "llm",
        }
        with self.assertRaises(ConfigError) as error:
            load_config("documents", env)
        self.assertIn("DEMO_SCENARIO", str(error.exception))
        self.assertIn("normal", str(error.exception))

    def test_missing_token_and_bad_poll(self):
        env = {
            "DJANGO_QUEUE_URL": "http://127.0.0.1:8000/api/v1/llm",
            "LLM_SERVICE_TOKEN": "llm",
            "WORKER_POLL_SECONDS": "0",
        }
        with self.assertRaises(ConfigError):
            load_config("documents", env)

        env["WORKER_POLL_SECONDS"] = "60"
        env.pop("LLM_SERVICE_TOKEN")
        with self.assertRaises(ConfigError) as error:
            load_config("documents", env)
        self.assertIn("LLM_SERVICE_TOKEN", str(error.exception))

    def test_analytics_stage_needs_separate_token(self):
        env = {
            "DJANGO_ANALYTICS_URL": "http://127.0.0.1:8000/api/v1/analytics",
            "LLM_SERVICE_TOKEN": "llm-only",
        }
        with self.assertRaises(ConfigError) as error:
            load_config("analytics", env)
        self.assertIn("ANALYTICS_SERVICE_TOKEN", str(error.exception))


class ExtractionStubTests(unittest.TestCase):
    def test_normal_payload_is_explicitly_synthetic_and_checksum_is_verified(self):
        result = build_document_result(document_meta(), CONTENT, "normal")
        self.assertEqual(result["document_type"], "demo_document")
        self.assertEqual(result["schema_version"], "1.0")
        self.assertIsNone(result["confidence"])
        payload = result["payload"]
        self.assertIs(payload["is_demo"], True)
        self.assertIs(payload["synthetic_fixture"], True)
        self.assertEqual(payload["scenario_id"], "normal")
        self.assertEqual(payload["extraction_method"], "stub")
        self.assertIs(payload["ocr_performed"], False)
        self.assertEqual(payload["verified_download"]["sha256"], SHA256)
        self.assertEqual(payload["verified_download"]["byte_count"], len(CONTENT))
        self.assertEqual(payload["fixture"]["monthly_income"], "120000.00")
        self.assertIs(payload["fixture"]["synthetic"], True)

    def test_checksum_mismatch_is_rejected(self):
        with self.assertRaises(Exception) as error:
            build_document_result(document_meta(), b"other bytes", "normal")
        self.assertIn("Размер", str(error.exception))

    def test_missing_data_and_manual_review_have_unknown_or_flag(self):
        missing = build_document_result(document_meta(), CONTENT, "missing_data")
        self.assertIsNone(missing["payload"]["fixture"]["monthly_income"])
        self.assertIn("missing_fields", missing["payload"]["fixture"])

        manual = build_document_result(document_meta(), CONTENT, "manual_review")
        self.assertIs(manual["payload"]["fixture"]["manual_review_flag"], True)

    def test_extraction_summary_does_not_claim_ocr(self):
        result = build_document_result(document_meta(), CONTENT, "normal")
        summary = extraction_summary([result])
        self.assertEqual(summary["document_count"], 1)
        self.assertIs(summary["is_demo"], True)
        self.assertIs(summary["ocr_performed"], False)


class PredictionStubTests(unittest.TestCase):
    def test_normal_all_components_ok_and_demo_only(self):
        result = build_analysis_result(document_claim("normal"))
        self.assertEqual(
            set(result),
            {
                "schema_version",
                "is_demo",
                "model_version",
                "policy_version",
                "scenario_id",
                "recommendation",
                "summary",
                "reason_codes",
                "components",
            },
        )
        self.assertEqual(result["scenario_id"], "normal")
        self.assertEqual(result["recommendation"], "PROCEED_TO_REVIEW")
        self.assertIs(result["is_demo"], True)
        self.assertEqual(result["model_version"], "stub-1")
        self.assertEqual(result["policy_version"], "demo-1")
        self.assertTrue(all(item["status"] == "OK" for item in result["components"].values()))
        self.assertIn("DEMO_ONLY", result["reason_codes"])
        self.assertNotIn("probability_of_default", result)

    def test_missing_data_is_incomplete_without_confident_normal_output(self):
        result = build_analysis_result(document_claim("missing_data"))
        self.assertEqual(result["scenario_id"], "missing_data")
        self.assertEqual(result["recommendation"], "REQUEST_INFORMATION")
        statuses = {item["status"] for item in result["components"].values()}
        self.assertNotIn("OK", statuses)
        self.assertIn("UNKNOWN", statuses)
        self.assertIn("INCOMPLETE", statuses)

    def test_manual_review_sets_radar_and_recommendation(self):
        result = build_analysis_result(document_claim("manual_review"))
        self.assertEqual(result["scenario_id"], "manual_review")
        self.assertEqual(result["recommendation"], "MANUAL_REVIEW")
        self.assertEqual(result["components"]["radar"]["status"], "MANUAL_REVIEW")

    def test_failed_or_unsupported_extraction_never_produces_normal_output(self):
        failed = document_claim(
            "normal",
            extraction_status="FAILED",
            documents=[{"payload": {}}],
        )
        result = build_analysis_result(failed)
        self.assertEqual(result["scenario_id"], "missing_data")
        self.assertEqual(result["recommendation"], "REQUEST_INFORMATION")
        self.assertTrue(
            all(item["status"] != "OK" for item in result["components"].values())
        )

        real_payload = document_claim(
            "normal",
            documents=[{"payload": {"is_demo": False, "monthly_income": "1"}}],
        )
        real_result = build_analysis_result(real_payload)
        self.assertEqual(real_result["scenario_id"], "missing_data")
        self.assertNotEqual(real_result["recommendation"], "PROCEED_TO_REVIEW")

        empty = document_claim("normal", documents=[])
        self.assertEqual(
            build_analysis_result(empty)["recommendation"], "REQUEST_INFORMATION"
        )


class WorkerCycleTests(unittest.TestCase):
    def test_one_all_stage_cycle_completes_both_queues(self):
        doc_client = FakeDocumentClient(claim=document_job_claim("normal"))
        analytics_claim = document_claim("normal")
        analytics_client = FakeAnalyticsClient(claim=analytics_claim)
        worker = Worker(
            base_config(stage="all"), doc_client, analytics_client
        )

        ok, worked = worker._cycle()

        self.assertTrue(ok)
        self.assertTrue(worked)
        self.assertEqual(doc_client.claimed_with, "test-worker")
        self.assertEqual(len(doc_client.completed), 1)
        self.assertEqual(len(analytics_client.completed), 1)
        doc_result = doc_client.completed[0][2][0]
        self.assertEqual(doc_result["payload"]["scenario_id"], "normal")
        analysis_result = analytics_client.completed[0][3]
        self.assertEqual(analysis_result["scenario_id"], "normal")

    def test_checksum_mismatch_reports_permanent_fail_not_complete(self):
        doc_client = FakeDocumentClient(
            claim=document_job_claim("normal"), content=b"tampered"
        )
        worker = Worker(base_config(stage="documents"), doc_client, FakeAnalyticsClient())

        ok, worked = worker._cycle()

        self.assertFalse(ok)
        self.assertTrue(worked)
        self.assertEqual(doc_client.completed, [])
        self.assertEqual(len(doc_client.failures), 1)
        self.assertIs(doc_client.failures[0][3], False)
        self.assertIn("Размер", doc_client.failures[0][2])

    def test_missing_data_scenario_is_carried_into_prediction(self):
        doc_client = FakeDocumentClient(claim=document_job_claim("missing_data"))
        analytics_client = FakeAnalyticsClient(claim=document_claim("missing_data"))
        worker = Worker(
            base_config(stage="all", scenario="missing_data"),
            doc_client,
            analytics_client,
        )

        ok, _worked = worker._cycle()

        self.assertTrue(ok)
        self.assertEqual(
            doc_client.completed[0][2][0]["payload"]["scenario_id"], "missing_data"
        )
        self.assertEqual(
            analytics_client.completed[0][3]["recommendation"],
            "REQUEST_INFORMATION",
        )

    def test_transient_and_config_errors_do_not_crash(self):
        transient = Worker(
            base_config(stage="documents"),
            FakeDocumentClient(claim_error=TransientClientError("timeout")),
            FakeAnalyticsClient(),
        )
        self.assertEqual(transient._cycle(), (False, False))

        config_error = Worker(
            base_config(stage="analytics"),
            FakeDocumentClient(),
            FakeAnalyticsClient(claim_error=ConfigurationClientError("HTTP 401")),
        )
        self.assertEqual(config_error._cycle(), (False, False))

    def test_once_idle_exit_zero_and_error_exit_nonzero(self):
        idle = Worker(
            base_config(stage="all"),
            FakeDocumentClient(claim=None),
            FakeAnalyticsClient(claim=None),
        )
        self.assertEqual(idle.run(once=True), 0)

        broken = Worker(
            base_config(stage="documents"),
            FakeDocumentClient(claim_error=TransientClientError("timeout")),
            FakeAnalyticsClient(),
        )
        self.assertEqual(broken.run(once=True), 1)

    def test_analytics_permanent_result_error_reports_fail(self):
        bad = document_claim("normal")
        bad.pop("model_version")
        analytics_client = FakeAnalyticsClient(claim=bad)
        worker = Worker(
            base_config(stage="analytics"), FakeDocumentClient(), analytics_client
        )

        ok, worked = worker._cycle()

        self.assertFalse(ok)
        self.assertTrue(worked)
        self.assertEqual(analytics_client.completed, [])
        self.assertEqual(len(analytics_client.failures), 1)
        self.assertIs(analytics_client.failures[0][3], False)


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://example.invalid/leak")
        self.end_headers()

    def log_message(self, format, *args):
        return


class DownloadSafetyTests(unittest.TestCase):
    def test_cross_origin_download_is_rejected_before_auth(self):
        client = QueueClient(
            base_url="http://django:8000/api/v1/llm", token="secret", timeout=2
        )
        with self.assertRaises(PermanentClientError):
            client.download("http://evil.invalid/doc", "lease")
        with self.assertRaises(PermanentClientError):
            client.download("https://django:8000/doc", "lease")

    def test_same_origin_non_queue_path_is_rejected_before_auth(self):
        client = QueueClient(
            base_url="http://django:8000/api/v1/llm", token="secret", timeout=2
        )
        client._opener = Mock()
        for url in (
            "http://django:8000/api/v1/analytics/jobs/j/",
            "http://django:8000/admin/",
            "http://django:8000/api/v1/llm/jobs",
            "http://django:8000/api/v1/llm/jobsx/",
            "http://django:8000/api/v1/llm/jobs/../admin/",
            "http://django:8000/api/v1/llm/jobs/%2e%2e/admin/",
        ):
            with self.assertRaises(PermanentClientError):
                client.download(url, "lease")
        client._opener.open.assert_not_called()

    def test_redirect_is_not_followed(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = QueueClient(
                base_url=f"http://127.0.0.1:{server.server_port}/api/v1/llm",
                token="secret",
                timeout=2,
            )
            with self.assertRaises(PermanentClientError):
                client.download(
                    f"http://127.0.0.1:{server.server_port}/api/v1/llm/jobs/doc",
                    "lease",
                )
        finally:
            server.shutdown()
            server.server_close()


class CompatibilityTests(unittest.TestCase):
    def test_permanent_error_remains_value_error_for_legacy_callers(self):
        self.assertTrue(issubclass(PermanentClientError, ValueError))
        self.assertTrue(issubclass(PermanentClientError, RuntimeError))

    def test_stage_cli_dispatches_to_stub_worker(self):
        argv = ["--stage", "documents", "--once"]
        with patch("ml.stub_worker.main", return_value=0) as stub_main:
            self.assertEqual(worker_main(argv), 0)
        stub_main.assert_called_once_with(argv)


if __name__ == "__main__":
    unittest.main()
