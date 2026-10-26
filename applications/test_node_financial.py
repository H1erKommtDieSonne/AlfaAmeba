"""Банковская сводка ``financial_analysis`` (schema v2) внешнего узла.

Контракт: ``node_financial_summary`` отдаёт ограниченный словарь без числовых
финансовых показателей, сырых сводок/исключений и решений ``model_outputs``.
Клиенту сводка не передаётся.
"""

import json
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import CreditTestCase
from node_integration.models import NodeJob
from node_integration.presentation import (
    MODEL_STATUS_MESSAGE,
    node_financial_summary,
    node_review,
)
from node_integration.services import complete_node_job

from .models import Application, ApplicationVersion
from .test_node_ui import bank_detail, current_version, submit_application

SUMMARY_KEYS = {
    "is_mock",
    "readiness",
    "warnings",
    "warnings_truncated",
    "complete_months",
    "annual_ready",
    "forecast_ready",
    "processing",
    "model_status_message",
}
READINESS_CODES = [
    "annual_model",
    "monthly_cash_flow",
    "next_month_receipts_and_operating_payments",
    "balance_after_all_debt",
    "dscr",
    "debt_to_ebitda",
    "collateral_valuation",
]

READY_ALL = {code: True for code in READINESS_CODES}


def _stub(status=NodeJob.Status.COMPLETED, result=None):
    return SimpleNamespace(status=status, result=result)


def _inner(financial=None, **overrides):
    inner = {
        "schema_version": 2,
        "status": "complete",
        "summary": "RAW-SUMMARY-SECRET",
        "issues": [],
        "financial_analysis": financial,
        "processing": {"duration_ms": 12, "llm_chunks": 0},
    }
    inner.update(overrides)
    return {"result": inner}


def _financial(**overrides):
    financial = {
        "schema_version": 2,
        "is_mock": False,
        "readiness": dict(READY_ALL),
        "warnings": [],
        "cash_flow": {"complete_months": 3},
        "annual_model_input": {"ready": True},
        "next_month_forecast": {"ready": True},
        "statistics": {"dscr": {"value": 1.5, "status": "available"}},
        "model_outputs": {"status": "not_run", "decision": "APPROVE-MODEL"},
    }
    financial.update(overrides)
    return financial


class NodeFinancialSummaryUnitTests(SimpleTestCase):
    def test_none_for_absent_unfinished_or_bad_schema(self):
        self.assertIsNone(node_financial_summary(None))
        for status in ("PENDING", "SENDING", "WAITING", "FAILED", "UNCERTAIN"):
            with self.subTest(status=status):
                self.assertIsNone(
                    node_financial_summary(_stub(status=status, result=_inner(_financial())))
                )
        bad_schema = [
            None,
            {},
            {"result": None},
            {"result": {}},
            {"result": {"schema_version": True, "financial_analysis": _financial()}},
            {"result": {"schema_version": "2", "financial_analysis": _financial()}},
            {"result": {"schema_version": 2.0, "financial_analysis": _financial()}},
            {"result": {"schema_version": 1, "financial_analysis": _financial()}},
        ]
        for result in bad_schema:
            with self.subTest(result=result):
                self.assertIsNone(node_financial_summary(_stub(result=result)))

    def test_none_for_missing_or_wrong_financial_block(self):
        for financial in (None, "x", [], {}, {"schema_version": 1}, {"schema_version": True}):
            with self.subTest(financial=financial):
                self.assertIsNone(node_financial_summary(_stub(result=_inner(financial))))

    def test_valid_v2_projection_contract(self):
        summary = node_financial_summary(_stub(result=_inner(_financial())))
        self.assertEqual(set(summary), SUMMARY_KEYS)
        self.assertEqual(
            [item["code"] for item in summary["readiness"]], READINESS_CODES
        )
        self.assertTrue(all(item["ready"] is True for item in summary["readiness"]))
        self.assertTrue(all(item["label"] for item in summary["readiness"]))
        self.assertIs(summary["is_mock"], False)
        self.assertEqual(summary["complete_months"], 3)
        self.assertIs(summary["annual_ready"], True)
        self.assertIs(summary["forecast_ready"], True)
        self.assertEqual(summary["processing"], {"duration_ms": 12, "llm_chunks": 0})
        self.assertEqual(summary["model_status_message"], MODEL_STATUS_MESSAGE)
        self.assertFalse(summary["warnings_truncated"])

    def test_is_mock_and_flags_preserve_unknown_vs_false(self):
        summary = node_financial_summary(
            _stub(
                result=_inner(
                    _financial(
                        is_mock=0,
                        readiness={"annual_model": 1, "dscr": "yes"},
                        cash_flow={"complete_months": True},
                        annual_model_input={"ready": 1},
                        next_month_forecast={"ready": "true"},
                    )
                )
            )
        )
        self.assertIsNone(summary["is_mock"])
        self.assertIsNone(summary["complete_months"])
        self.assertIsNone(summary["annual_ready"])
        self.assertIsNone(summary["forecast_ready"])
        by_code = {item["code"]: item["ready"] for item in summary["readiness"]}
        self.assertIsNone(by_code["annual_model"])
        self.assertIsNone(by_code["dscr"])
        # Отсутствующие флаги тоже None, а не false.
        self.assertIsNone(by_code["monthly_cash_flow"])

    def test_no_raw_summary_statistics_or_model_decision_leak(self):
        summary = node_financial_summary(_stub(result=_inner(_financial())))
        serialized = json.dumps(summary, ensure_ascii=False)
        for leaked in (
            "RAW-SUMMARY-SECRET",
            "APPROVE-MODEL",
            "model_outputs",
            "statistics",
            "decision",
        ):
            self.assertNotIn(leaked, serialized)

    def test_warnings_whitelist_and_bounds(self):
        warnings = [
            {"code": "seasonality_not_estimated", "severity": "low", "fields": []},
            {"code": "stale_bank_history", "severity": "medium", "message": "RAW"},
            {"code": "conflicting_metrics", "severity": "high", "fields": ["revenue", 5, ""]},
            {"code": "no_severity_legacy"},
            {"code": "bad_severity", "severity": "critical"},
            "not-a-dict",
            {"code": [], "severity": "low"},
            {"code": {"a": 1}, "severity": "low"},
            {"code": "x", "severity": ["low"]},
        ]
        summary = node_financial_summary(_stub(result=_inner(_financial(warnings=warnings))))
        self.assertEqual(
            [item["code"] for item in summary["warnings"]],
            ["seasonality_not_estimated", "stale_bank_history", "conflicting_metrics"],
        )
        self.assertEqual(summary["warnings"][0]["label"], "Низкая важность")
        self.assertEqual(summary["warnings"][2]["label"], "Высокая важность")
        self.assertEqual(summary["warnings"][2]["fields"], ["revenue"])
        self.assertNotIn("message", summary["warnings"][1])

    def test_warnings_and_fields_are_bounded(self):
        long_code = "c" * 500
        long_field = "f" * 500
        warnings = [
            {"code": f"c{i}", "severity": "low", "fields": [long_field] * 1000}
            for i in range(1000)
        ]
        summary = node_financial_summary(
            _stub(result=_inner(_financial(warnings=warnings)))
        )
        self.assertEqual(len(summary["warnings"]), 20)
        self.assertTrue(summary["warnings_truncated"])
        self.assertEqual(len(summary["warnings"][0]["fields"]), 10)
        self.assertEqual(len(summary["warnings"][0]["fields"][0]), 120)
        self.assertLessEqual(len(summary["warnings"][0]["code"]), 64)

        exact = [
            {"code": f"c{i}", "severity": "low", "fields": []} for i in range(20)
        ]
        summary = node_financial_summary(
            _stub(result=_inner(_financial(warnings=exact)))
        )
        self.assertEqual(len(summary["warnings"]), 20)
        self.assertFalse(summary["warnings_truncated"])

        huge = {"code": long_code, "severity": "low", "fields": []}
        summary = node_financial_summary(
            _stub(result=_inner(_financial(warnings=[huge])))
        )
        self.assertEqual(len(summary["warnings"][0]["code"]), 64)

    def test_projection_is_small_and_html_is_left_to_template(self):
        warnings = [
            {"code": "<script>alert(1)</script>", "severity": "high", "fields": ["<b>x</b>"]}
        ]
        summary = node_financial_summary(
            _stub(result=_inner(_financial(warnings=warnings)))
        )
        # Функция не экранирует HTML: это делает шаблон; здесь только границы.
        self.assertEqual(summary["warnings"][0]["code"], "<script>alert(1)</script>")
        self.assertEqual(summary["warnings"][0]["fields"], ["<b>x</b>"])
        self.assertLess(len(json.dumps(summary, ensure_ascii=False)), 20000)

    def test_legacy_callback_returns_none_but_node_review_unaffected(self):
        legacy = {"result": {"status": "complete", "summary": "OLD"}}
        job = _stub(result=legacy)
        self.assertIsNone(node_financial_summary(job))
        self.assertEqual(node_review(job)["status"], "complete")


class NodeFinancialSummaryViewTests(CreditTestCase):
    def setUp(self):
        self.application = submit_application(self)
        self.version = current_version(self.application)

    def _set_node(self, result, *, status=NodeJob.Status.COMPLETED, external_job_id=None):
        defaults = {"status": status, "result": result}
        if external_job_id is not None:
            defaults["external_job_id"] = external_job_id
        if status == NodeJob.Status.COMPLETED:
            defaults["completed_at"] = timezone.now()
        return NodeJob.objects.update_or_create(version=self.version, defaults=defaults)[0]

    def _bank(self):
        return bank_detail(self, self.application, self.bank_user)

    def test_bank_context_exposes_summary_client_does_not(self):
        self._set_node(_inner(_financial()))
        bank = self._bank()
        self.assertEqual(bank.status_code, 200)
        summary = bank.context["node_financial_summary"]
        self.assertEqual(set(summary), SUMMARY_KEYS)
        self.assertEqual(summary["model_status_message"], MODEL_STATUS_MESSAGE)

        self.client.force_login(self.business_user)
        client = self.client.get(
            reverse("business-application-detail", args=[self.application.pk])
        )
        self.assertEqual(client.status_code, 200)
        self.assertNotIn("node_financial_summary", client.context)
        self.assertNotIn("financial_analysis", client.context)
        html = client.content.decode("utf-8")
        for leaked in ("RAW-SUMMARY-SECRET", "APPROVE-MODEL", "financial_analysis"):
            self.assertNotIn(leaked, html)

    def test_only_current_version_summary_is_presented(self):
        self._set_node(_inner(_financial(is_mock=True)))
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
            result=_inner(_financial(is_mock=False)),
            completed_at=timezone.now(),
        )
        Application.objects.filter(pk=self.application.pk).update(current_version=2)
        self.application.refresh_from_db()

        summary = self._bank().context["node_financial_summary"]
        self.assertIs(summary["is_mock"], False)

    def test_legacy_callback_accepted_no_mutation_and_no_summary(self):
        job = self._set_node(
            {"legacy": "opaque"}, status=NodeJob.Status.WAITING, external_job_id="b" * 32
        )
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
        self.assertIsNone(node_financial_summary(job))
        self.assertIsNone(self._bank().context["node_financial_summary"])

    def test_archive_synthetic_callback_shape_is_extracted(self):
        job = self._set_node(
            {}, status=NodeJob.Status.WAITING, external_job_id="a" * 32
        )
        payload = {
            "job_id": "a" * 32,
            "status": "completed",
            "verification_status": "not_verified",
            "model": "qwen3.5:35b-a3b",
            "files": [{"filename": "bank.csv", "status": "checked"}],
            "result": {
                "schema_version": 2,
                "status": "complete",
                "summary": "RAW-SUMMARY-SECRET",
                "issues": [],
                "missing_documents": [],
                "recommendations": ["RAW-REC"],
                "documents_checked": 1,
                "financial_analysis": _financial(),
                "processing": {
                    "started_at": "2026-10-24T18:32:35+00:00",
                    "completed_at": "2026-10-24T18:32:35+00:00",
                    "duration_ms": 1,
                    "files_received": 1,
                    "accepted_observations": 3,
                    "llm_chunks": 0,
                },
            },
        }
        job, created = complete_node_job(payload)
        self.assertTrue(created)
        self.assertEqual(job.status, NodeJob.Status.COMPLETED)
        summary = node_financial_summary(job)
        self.assertEqual(set(summary), SUMMARY_KEYS)
        self.assertEqual(summary["processing"], {"duration_ms": 1, "llm_chunks": 0})
        self.assertNotIn("RAW-SUMMARY-SECRET", json.dumps(summary, ensure_ascii=False))

    # --- Отрисовка банковской карточки (перенесено из QA-временного теста) ---

    def _attack_result(self, *, is_mock, ready=True):
        return _inner(
            _financial(
                is_mock=is_mock,
                readiness={code: ready for code in READINESS_CODES},
                warnings=[
                    {
                        "code": "seasonality_not_estimated",
                        "severity": "low",
                        "fields": ["revenue"],
                        "message": "RAW-WARN",
                    },
                    {
                        "code": "<script>alert(1)</script>",
                        "severity": "high",
                        "fields": ["<b>x</b>"],
                    },
                ],
            )
        )

    def test_bank_v2_panel_renders_rich_summary_without_raw_json(self):
        for is_mock in (True, False):
            with self.subTest(is_mock=is_mock):
                self._set_node(self._attack_result(is_mock=is_mock))
                response = self._bank()
                html = response.content.decode("utf-8")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["node_result_json"], "")
                financial = response.context["financial"]
                self.assertEqual(financial["state"], "ready")
                # Богатая панель имеет приоритет; компактной сводки нет.
                self.assertContains(response, "Что учла модель")
                self.assertContains(response, "Что выдал анализ")
                self.assertNotContains(response, "Финансовый разбор документов")
                if is_mock:
                    self.assertContains(response, "Демо-данные")
                else:
                    self.assertNotContains(response, "Демо-данные")
                # Богатая панель намеренно показывает сообщение сигнала узла.
                self.assertIn("RAW-WARN", html)
                for leak in (
                    "RAW-SUMMARY-SECRET",
                    "APPROVE-MODEL",
                    "model_outputs",
                    "statistics",
                    "Ответ внешнего узла (JSON)",
                ):
                    self.assertNotIn(leak, html)
                self.assertNotIn("<script>alert(1)</script>", html)
                self.assertNotIn("<b>x</b>", html)

    def test_v2_unknown_flags_use_rich_panel_not_compact_false(self):
        self._set_node(
            _inner(
                _financial(
                    is_mock=None,
                    readiness={},
                    cash_flow={},
                    annual_model_input={},
                    next_month_forecast={},
                    warnings=[],
                )
            )
        )
        response = self._bank()
        self.assertEqual(response.context["financial"]["state"], "ready")
        self.assertContains(response, "Что учла модель")
        self.assertContains(response, "Что выдал анализ")
        # Компактная сводка не дублирует богатую и не подменяет неизвестное false.
        self.assertNotContains(response, "Финансовый разбор документов")
        self.assertNotContains(response, "Источник данных не указан")
        self.assertNotContains(response, "Неизвестно")
        self.assertNotContains(response, "Данные готовы")

    def test_active_attempt_supersedes_wrong_historic_result(self):
        self._set_node(_inner(_financial(cash_flow={"complete_months": 3})))
        pending = NodeJob.objects.create(
            version=self.version,
            status=NodeJob.Status.PENDING,
            result=None,
        )
        response = self._bank()
        # Пока активная попытка не завершена, показываем прошлый готовый результат.
        self.assertEqual(response.context["financial"]["cash_flow"]["complete_months"], 3)
        self.assertContains(response, "3 полн. мес.")

        # Активная попытка завершается новым результатом — берём именно его.
        NodeJob.objects.filter(pk=pending.pk).update(
            status=NodeJob.Status.COMPLETED,
            result=_inner(_financial(cash_flow={"complete_months": 9})),
            completed_at=timezone.now(),
        )
        response = self._bank()
        self.assertEqual(response.context["financial"]["cash_flow"]["complete_months"], 9)
        self.assertContains(response, "9 полн. мес.")
        self.assertNotContains(response, "3 полн. мес.")
        self.assertNotContains(response, "Финансовый разбор документов")


    def test_processing_error_card_keeps_both_report_sections(self):
        self._set_node(
            {
                "result": {
                    "schema_version": 2,
                    "status": "processing_error",
                    "summary": "RAW-PE",
                    "financial_analysis": None,
                }
            }
        )
        response = self._bank()
        self.assertContains(response, "Анализ финмодели")
        self.assertContains(response, "Расчёт ML-моделей")
        self.assertContains(
            response,
            "Итоговый анализ не запускался: не удалось обработать документы.",
        )
        self.assertContains(
            response,
            "Не выполнен: обработка документов завершилась технической ошибкой. "
            "Это не отказ по заявке.",
        )
        self.assertNotContains(response, "RAW-PE")

    def test_ready_card_shows_ml_and_neural_sections(self):
        payload = _inner(
            _financial(
                model_outputs={
                    "status": "completed",
                    "risk_grade": "B",
                    "safe_credit_limit_rub_thousands": 1500,
                    "approval_probability_percent": 35,
                }
            )
        )
        payload["result"]["final_analysis"] = {
            "schema_version": 1,
            "status": "completed",
            "is_mock": False,
            "summary": "Итоговый текст нейросети.",
            "not_a_decision": True,
        }
        self._set_node(payload)
        response = self._bank()
        self.assertContains(response, "Анализ финмодели")
        self.assertContains(response, "Итоговый текст нейросети.")
        self.assertContains(response, "Расчёт ML-моделей")
        self.assertContains(response, "Безопасный лимит")
        html = response.content.decode("utf-8").replace("\u00a0", " ")
        self.assertIn("35 %", html)

    def test_v2_without_financial_still_suppresses_raw_json(self):
        self._set_node(
            {
                "result": {
                    "schema_version": 2,
                    "status": "processing_error",
                    "summary": "RAW-PE",
                    "financial_analysis": None,
                }
            }
        )
        response = self._bank()
        self.assertEqual(response.context["node_result_json"], "")
        self.assertIsNone(response.context["node_financial_summary"])
        html = response.content.decode("utf-8")
        self.assertNotIn("RAW-PE", html)
        self.assertContains(response, "Структурированный ответ получен")

    def test_legacy_bank_raw_json_is_still_shown_escaped(self):
        self._set_node({"score": 42, "note": "<b>x</b>"})
        response = self._bank()
        self.assertContains(response, "Ответ внешнего узла (JSON)")
        self.assertTrue(response.context["node_result_json"])
        html = response.content.decode("utf-8")
        self.assertIn("&quot;score&quot;: 42", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)

    def test_client_rendered_has_no_financial_summary(self):
        self._set_node(self._attack_result(is_mock=True))
        self.client.force_login(self.business_user)
        response = self.client.get(
            reverse("business-application-detail", args=[self.application.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("node_financial_summary", response.context)
        html = response.content.decode("utf-8")
        for leak in (
            "Финансовый разбор документов",
            "RAW-SUMMARY-SECRET",
            "RAW-WARN",
            "APPROVE-MODEL",
            "financial_analysis",
            "Готовность данных",
        ):
            self.assertNotIn(leak, html)
