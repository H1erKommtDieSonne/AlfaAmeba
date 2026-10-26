"""Контракт представления вложенного ``result.status`` внешнего узла (v2).

Статус ``NodeJob`` остаётся доставкой callback: ``processing_error`` не
превращается в ``FAILED`` и не вызывает повторную отправку.
"""

import json
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import CreditTestCase
from node_integration.models import NodeJob
from node_integration.presentation import (
    PROCESSING_ERROR_MESSAGE,
    node_review,
)
from node_integration.services import complete_node_job

from .models import Application, ApplicationVersion
from .test_node_ui import bank_detail, current_version, submit_application

INNER_STATUSES = (
    ("complete", "status--positive"),
    ("needs_documents", "status--warning"),
    ("needs_review", "status--warning"),
    ("processing_error", "status--negative"),
)


def _stub(status=NodeJob.Status.COMPLETED, result=None):
    return SimpleNamespace(status=status, result=result)


class NodeReviewUnitTests(SimpleTestCase):
    def test_absent_and_unfinished_are_none(self):
        self.assertIsNone(node_review(None))
        for status in ("PENDING", "SENDING", "WAITING", "FAILED", "UNCERTAIN"):
            with self.subTest(status=status):
                self.assertIsNone(
                    node_review(
                        _stub(status=status, result={"result": {"status": "complete"}})
                    )
                )

    def test_all_four_statuses_have_safe_display(self):
        for status, tone in INNER_STATUSES:
            with self.subTest(status=status):
                review = node_review(
                    _stub(result={"result": {"status": status, "summary": "RAW-MODEL"}})
                )
                self.assertEqual(review["status"], status)
                self.assertEqual(review["tone"], tone)
                self.assertTrue(review["label"])
                self.assertTrue(review["message"])
                # Сырая сводка модели не берётся в подпись.
                self.assertNotIn("RAW-MODEL", json.dumps(review, ensure_ascii=False))

    def test_legacy_and_malformed_shapes_are_none(self):
        cases = [
            None,
            {},
            {"result": None},
            {"result": "x"},
            {"result": []},
            {"result": {}},
            {"result": {"status": None}},
            {"result": {"status": 1}},
            {"result": {"status": ""}},
            {"result": {"status": "brand_new_state"}},
            {"other": 1},
        ]
        for result in cases:
            with self.subTest(result=result):
                self.assertIsNone(node_review(_stub(result=result)))

    def test_processing_error_message_is_generic_and_leak_free(self):
        raw = "<script>alert('model')</script>"
        review = node_review(
            _stub(
                result={
                    "result": {
                        "status": "processing_error",
                        "summary": raw,
                        "error": {"code": "document_processing_failed", "retryable": True},
                        "issues": [{"code": "x", "message": raw}],
                    }
                }
            )
        )
        self.assertEqual(review["message"], PROCESSING_ERROR_MESSAGE)
        serialized = json.dumps(review, ensure_ascii=False)
        for leaked in ("script", "document_processing_failed", "retryable", "issues"):
            self.assertNotIn(leaked, serialized)

    def test_known_status_message_ignores_summary(self):
        review = node_review(
            _stub(result={"result": {"status": "needs_review", "summary": "SECRET-RAW"}})
        )
        self.assertNotIn("SECRET-RAW", json.dumps(review, ensure_ascii=False))

    def test_helper_does_not_mutate_status_or_extract_issues(self):
        job = _stub(
            status=NodeJob.Status.COMPLETED,
            result={"result": {"status": "processing_error", "issues": [{"code": "x"}]}},
        )
        review = node_review(job)
        self.assertEqual(job.status, NodeJob.Status.COMPLETED)
        self.assertNotIn("issues", review)


class NodeReviewViewTests(CreditTestCase):
    def setUp(self):
        self.application = submit_application(self)
        self.version = current_version(self.application)

    def _set_node(self, *, status=NodeJob.Status.COMPLETED, result=None, external_job_id=None):
        defaults = {"status": status}
        if result is not None:
            defaults["result"] = result
        if external_job_id is not None:
            defaults["external_job_id"] = external_job_id
        if status == NodeJob.Status.COMPLETED:
            defaults["completed_at"] = timezone.now()
        return NodeJob.objects.update_or_create(version=self.version, defaults=defaults)[0]

    def _bank(self):
        return bank_detail(self, self.application, self.bank_user)

    def test_bank_context_exposes_node_review_for_all_four_statuses(self):
        for status, tone in INNER_STATUSES:
            with self.subTest(status=status):
                self._set_node(
                    result={"result": {"status": status, "summary": f"RAW-{status}"}}
                )
                response = self._bank()
                self.assertEqual(response.status_code, 200)
                review = response.context["node_review"]
                self.assertEqual(review["status"], status)
                self.assertEqual(review["tone"], tone)
                self.assertNotIn(f"RAW-{status}", review["message"])

    def test_client_context_exposes_generic_review_without_raw_result(self):
        self._set_node(
            result={
                "result": {
                    "status": "processing_error",
                    "summary": "RAW-EXCEPTION",
                    "error": {"code": "boom"},
                }
            }
        )
        self.client.force_login(self.business_user)
        response = self.client.get(
            reverse("business-application-detail", args=[self.application.pk])
        )
        self.assertEqual(response.status_code, 200)
        review = response.context["node_review"]
        self.assertEqual(review["status"], "processing_error")
        self.assertEqual(review["message"], PROCESSING_ERROR_MESSAGE)
        self.assertNotIn("node_result_json", response.context)
        self.assertNotIn("node_job", response.context)
        self.assertNotContains(response, "RAW-EXCEPTION")
        self.assertNotContains(response, "Ответ внешнего узла")
        self.assertNotContains(response, "boom")

    def test_legacy_callback_result_exposes_none_but_bank_json_remains(self):
        self._set_node(result={"score": 42, "note": "<b>x</b>"})
        response = self._bank()
        self.assertIsNone(response.context["node_review"])
        self.assertContains(response, "&quot;score&quot;: 42")

    def test_unknown_inner_status_is_none(self):
        self._set_node(result={"result": {"status": "new_status_v9"}})
        response = self._bank()
        self.assertIsNone(response.context["node_review"])
        self.assertEqual(response.status_code, 200)

    def test_processing_error_does_not_mutate_node_job_or_retry(self):
        job = self._set_node(
            result={"result": {"status": "processing_error"}},
            external_job_id="a" * 32,
        )
        self._bank()
        self._bank()
        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.COMPLETED)
        self.assertEqual(job.attempts, 0)
        self.assertEqual(job.external_job_id, "a" * 32)
        self.assertIsNotNone(job.completed_at)

    def test_only_current_version_is_presented(self):
        self._set_node(result={"result": {"status": "complete"}})
        second_version = ApplicationVersion.objects.create(
            application=self.application,
            version_number=2,
            product=self.product,
            amount="1600000.00",
            term_months=12,
            purpose="Повторная заявка с обновлёнными условиями",
            submitted_at=timezone.now(),
            snapshot={"organization_name": "ООО Север", "version": 2},
        )
        NodeJob.objects.create(
            version=second_version,
            status=NodeJob.Status.COMPLETED,
            result={"result": {"status": "needs_review"}},
            completed_at=timezone.now(),
        )
        Application.objects.filter(pk=self.application.pk).update(current_version=2)
        self.application.refresh_from_db()

        response = self._bank()
        self.assertEqual(response.context["node_review"]["status"], "needs_review")

    def test_bank_access_unchanged_for_business_user(self):
        self._set_node(result={"result": {"status": "complete"}})
        self.client.force_login(self.business_user)
        response = self.client.get(
            reverse("bank-application-detail", args=[self.application.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_rendered_pages_for_all_four_statuses_are_safe(self):
        for status, tone in INNER_STATUSES:
            with self.subTest(status=status):
                marker = f"RAW-LEAK-{status}"
                error_code = f"MODEL-EXCEPTION-{status}"
                self._set_node(
                    result={
                        "result": {
                            "status": status,
                            "summary": marker,
                            "error": {"code": error_code, "retryable": True},
                        }
                    }
                )
                bank = self._bank()
                self.client.force_login(self.business_user)
                client = self.client.get(
                    reverse("business-application-detail", args=[self.application.pk])
                )
                self.assertEqual(bank.status_code, 200)
                self.assertEqual(client.status_code, 200)

                review = bank.context["node_review"]
                self.assertEqual(review["status"], status)
                self.assertEqual(review["tone"], tone)
                self.assertNotIn(marker, review["message"])
                self.assertNotIn(error_code, review["message"])

                client_html = client.content.decode("utf-8")
                self.assertNotIn(marker, client_html)
                self.assertNotIn(error_code, client_html)
                self.assertNotIn("retryable", client_html)
                self.assertNotIn("Ответ внешнего узла", client_html)
                self.assertNotIn("node_result_json", client.context)
                self.assertNotIn("node_job", client.context)

    def test_rendered_processing_error_message_is_generic(self):
        self._set_node(
            result={
                "result": {
                    "status": "processing_error",
                    "summary": "RAW-EXCEPTION-TEXT",
                    "error": {"code": "document_processing_failed", "retryable": True},
                }
            }
        )
        self.client.force_login(self.business_user)
        response = self.client.get(
            reverse("business-application-detail", args=[self.application.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["node_review"]["message"], PROCESSING_ERROR_MESSAGE
        )
        html = response.content.decode("utf-8")
        for leaked in (
            "RAW-EXCEPTION-TEXT",
            "document_processing_failed",
            "retryable",
        ):
            self.assertNotIn(leaked, html)

    def test_old_arbitrary_callback_is_accepted_and_idempotent(self):
        job = self._set_node(status=NodeJob.Status.WAITING, external_job_id="b" * 32)
        payload = {
            "job_id": "b" * 32,
            "status": "completed",
            "result": {"legacy": "opaque"},
            "files": [],
            "verification_status": "needs_review",
        }
        first, created = complete_node_job(payload)
        self.assertTrue(created)
        second, created_again = complete_node_job(payload)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.COMPLETED)
        self.assertIsNone(node_review(job))
