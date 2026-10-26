"""Фокусные тесты отчёта: ML-анализ и итоговый анализ нейросети видны отдельно.

Проверяется только безопасное представление и шаблон карточки сотрудника:
никаких сырых исключений, промптов и решений модели вместо банка.
"""

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from node_integration.final_analysis import (
    EXTRACTION_ERROR_MESSAGE,
    PENDING_MESSAGE,
)
from node_integration.financial import financial_view
from node_integration.models import NodeJob


def _analysis(**overrides):
    analysis = {
        "schema_version": 2,
        "is_mock": False,
        "readiness": {},
        "warnings": [],
        "cash_flow": {},
        "annual_model_input": {},
        "next_month_forecast": {},
        "statistics": {},
        "client_requests": [],
        "model_outputs": {"status": "not_run"},
    }
    analysis.update(overrides)
    return analysis


def _final(**overrides):
    block = {
        "schema_version": 1,
        "status": "completed",
        "is_mock": False,
        "summary": "Итог по документам.",
        "key_points": ["Пункт"],
        "risks": ["Риск"],
        "missing": [],
        "conflicts": [],
        "next_steps": ["Проверить источник"],
        "limitations": ["Только по загруженным документам"],
        "provenance": {"model": "final-model", "prompt_version": "final-analysis-1"},
        "not_a_decision": True,
    }
    block.update(overrides)
    return block


def _inner(analysis=None, final=None):
    inner = {"schema_version": 2, "status": "complete"}
    if analysis is not None:
        inner["financial_analysis"] = analysis
    if final is not None:
        inner["final_analysis"] = final
    return inner


def _view(inner):
    return financial_view(
        NodeJob(status=NodeJob.Status.COMPLETED, result={"result": inner})
    )


def _render(view):
    return render_to_string(
        "applications/bank/_analysis_side.html",
        {"financial": view, "can_confirm_inputs": False},
    )


def _spaces(text):
    """NBSP из форматирования чисел сравниваем как обычный пробел."""
    return text.replace("\u00a0", " ")


class FinancialReportUITests(SimpleTestCase):
    def test_processing_error_keeps_both_sections_and_safe_reasons(self):
        view = _view(
            {
                "schema_version": 2,
                "status": "processing_error",
                "summary": "RAW-EXCEPTION-SECRET",
                "financial_analysis": None,
            }
        )
        html = _render(view)

        self.assertIn("Анализ финмодели", html)
        self.assertIn("Расчёт ML-моделей", html)
        self.assertIn(EXTRACTION_ERROR_MESSAGE, html)
        self.assertIn("не удалось обработать документы", html)
        self.assertNotIn("RAW-EXCEPTION-SECRET", html)
        self.assertNotIn("Traceback", html)

    def test_ready_shows_numeric_ml_and_network_summary_separately(self):
        view = _view(
            _inner(
                _analysis(
                    model_outputs={
                        "status": "completed",
                        "risk_grade": "A",
                        "safe_credit_limit_rub_thousands": 1000,
                        "approval_probability_percent": 42,
                        "decision": "Рекомендация модели",
                    }
                ),
                _final(summary="Нейросеть согласна с расчётом."),
            )
        )
        html = _render(view)

        self.assertIn("Анализ финмодели", html)
        self.assertIn(
            "Нейросетевое заключение по типу и цели кредита, финансовым данным "
            "и прогнозам ML.",
            html,
        )
        self.assertIn("Нейросеть согласна с расчётом.", html)
        self.assertIn("Расчёт ML-моделей", html)
        self.assertIn("Безопасный лимит", html)
        self.assertIn("42 %", _spaces(html))
        self.assertIn("ML-модель банка", html)
        self.assertIn("Нейросеть: final-model", html)

    def test_ready_without_final_block_has_legacy_message(self):
        view = _view(_inner(_analysis()))
        html = _render(view)

        self.assertIn(
            "Для этой обработки итоговый анализ нейросети не выполнялся.", html
        )
        self.assertIn("ML-модель ещё не запускалась", html)
        self.assertNotIn("Безопасный лимит", html)

    def test_pending_and_failed_mock_do_not_invent_results(self):
        pending = _render(financial_view(NodeJob(status=NodeJob.Status.WAITING)))
        self.assertIn(PENDING_MESSAGE, pending)
        self.assertIn("Анализ финмодели", pending)
        self.assertIn("Расчёт ML-моделей", pending)
        self.assertNotIn("Безопасный лимит", pending)

        view = _view(
            _inner(
                _analysis(
                    is_mock=True,
                    model_outputs={
                        "status": "failed",
                        "note": "RAW-TRACE-SECRET",
                        "approval_probability_percent": 99,
                    },
                ),
                _final(status="failed", summary="RAW-PROMPT-SECRET"),
            )
        )
        html = _render(view)
        self.assertIn("Демо-данные", html)
        self.assertIn("не выполнен", html)
        self.assertNotIn("RAW-TRACE-SECRET", html)
        self.assertNotIn("RAW-PROMPT-SECRET", html)
        self.assertNotIn("99,0 %", html)

    def test_html_is_escaped_in_both_sections(self):
        view = _view(
            _inner(
                _analysis(
                    model_outputs={
                        "status": "completed",
                        "decision": '<script>alert("ml")</script>',
                    }
                ),
                _final(summary='<img src=x onerror="xss">', risks=["<b>риск</b>"]),
            )
        )
        html = _render(view)

        self.assertIn("&lt;script&gt;alert(&quot;ml&quot;)&lt;/script&gt;", html)
        self.assertIn("&lt;img src=x onerror=&quot;xss&quot;&gt;", html)
        self.assertIn("&lt;b&gt;риск&lt;/b&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
