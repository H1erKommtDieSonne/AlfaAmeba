import copy
import json
from pathlib import Path

import jsonschema
from django.conf import settings
from django.test import SimpleTestCase

from django.template.loader import render_to_string

from .demo import contract_result, working_capital_result
from .financial import (
    MODEL_OUTPUT_FIELDS,
    MODEL_STATUS_COMPLETED,
    MODEL_STATUS_FAILED,
    MODEL_STATUS_NOT_RUN,
    feedback_draft,
    financial_view,
    money,
    month_label,
    percent,
)
from .models import NodeJob

FIXTURES = Path(__file__).parent / "fixtures" / "financial_v2"
SCHEMA = Path(settings.BASE_DIR) / "docs" / "schemas" / "financial-result-v2.schema.json"

# Полный обязательный набор ключей ModelOutputs схемы v2 (13 штук).
MODEL_OUTPUT_KEYS = (
    "status",
    "forecast_revenue_rub_thousands",
    "safe_credit_limit_rub_thousands",
    "net_margin_percent",
    "operating_margin_percent",
    "loss_probability_percent",
    "liquidity_crisis_probability_percent",
    "default_probability_percent",
    "approval_probability_percent",
    "risk_grade",
    "decision",
    "operating_cash_flow_forecast_rub",
    "note",
)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def completed(payload):
    return NodeJob(status=NodeJob.Status.COMPLETED, result=payload)


def model_outputs(**overrides):
    block = {key: None for key in MODEL_OUTPUT_KEYS}
    block["status"] = MODEL_STATUS_NOT_RUN
    block["note"] = ""
    block.update(overrides)
    return block


def with_model_outputs(block):
    payload = copy.deepcopy(load("node-result.json"))
    payload["result"]["financial_analysis"]["model_outputs"] = block
    return payload


class SchemaTests(SimpleTestCase):
    """Демо-данные и примеры узла обязаны проходить ту же JSON Schema, что публикует узел."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))

    def assertValid(self, payload):
        errors = sorted(self.validator.iter_errors(payload["result"]["financial_analysis"]), key=str)
        self.assertEqual(errors, [], "\n".join(error.message for error in errors[:5]))

    def test_node_examples_match_schema(self):
        self.assertValid(load("node-result.json"))
        self.assertValid(load("node-result-needs-documents.json"))

    def test_demo_results_match_schema(self):
        self.assertValid(working_capital_result(application_id="demo-1"))
        self.assertValid(contract_result(application_id="demo-2"))


class FinancialViewNodeExampleTests(SimpleTestCase):
    def test_complete_example(self):
        view = financial_view(completed(load("node-result.json")))

        self.assertEqual(view["state"], "ready")
        self.assertTrue(view["is_mock"])
        self.assertEqual((view["readiness"]["ready_count"], view["readiness"]["total"]), (4, 7))
        self.assertEqual(view["cash_flow"]["complete_months"], 3)
        self.assertEqual(view["cash_flow"]["latest_balance_display"], "23,1 млн ₽")
        labels = {item["key"]: item for item in view["statistics"]}
        self.assertEqual(labels["current_ratio"]["label"], "Текущая ликвидность")
        self.assertEqual(labels["current_ratio"]["display"], "1,29")
        self.assertEqual(labels["net_margin"]["display"], "4,9 %")
        self.assertEqual(labels["dscr"]["display"], "нет данных")
        self.assertIn("оборотные активы", labels["current_ratio"]["formula"])
        # Посчитанные показатели идут раньше недоступных.
        self.assertTrue(view["statistics"][0]["available"])
        self.assertFalse(view["statistics"][-1]["available"])

    def test_chart_data_follows_months_and_forecast_bridge(self):
        view = financial_view(completed(load("node-result.json")))
        charts = view["chart_data"]

        self.assertEqual(charts["cashflow"]["labels"], ["июл 2026", "авг 2026", "сен 2026"])
        self.assertEqual(charts["cashflow"]["receipts"], [1000000.0, 2000000.0, 3000000.0])
        self.assertEqual(charts["forecast"]["labels"], ["сен 2026", "окт 2026", "ноя 2026"])
        stress = next(item for item in charts["forecast"]["scenarios"] if item["name"] == "stress")
        self.assertEqual(stress["values"], [23100000.0, 23680000.0, 24060000.0])
        self.assertEqual(charts["concentration"]["labels"], ["buyer-1"])

    def test_needs_documents_requests_required_first(self):
        view = financial_view(completed(load("node-result-needs-documents.json")))
        requests = view["client_requests"]

        self.assertEqual(view["required_requests"], 3)
        self.assertEqual([item["required"] for item in requests], [True, True, True, False, False])
        self.assertEqual(requests[0]["title"], "Оценка обеспечения")
        self.assertIn("Проверка комплекта документов", requests[1]["required_for"])
        self.assertEqual(len(view["issues"]), 3)

    def test_warnings_without_severity_go_last_and_without_badge(self):
        payload = load("node-result.json")
        warnings = payload["result"]["financial_analysis"]["warnings"]
        warnings.append({"code": "stale_bank_history", "severity": "high", "message": "Выписка устарела", "fields": []})

        view = financial_view(completed(payload))

        self.assertEqual(view["warnings"][0]["severity_label"], "Высокий")
        self.assertEqual(view["warnings"][0]["tone"], "negative")
        self.assertEqual(view["warnings"][-1]["severity_label"], "")


class FinancialViewDemoTests(SimpleTestCase):
    def test_working_capital_demo(self):
        view = financial_view(completed(working_capital_result(application_id="demo-1")))

        self.assertEqual(view["readiness"]["ready_count"], 6)
        self.assertEqual(len(view["concentration"]), 6)
        self.assertEqual(view["concentration"][0]["share_display"], "42 %")
        self.assertTrue(view["forecast"]["ready"])
        self.assertFalse(view["forecast"]["stress"]["has_gap"])
        # Выписка, отчётность и стресс-сценарий в порядке; 42 % — ниже порога подсказки о концентрации,
        # про неё говорит предупреждение узла со своим severity.
        self.assertEqual([item["tone"] for item in view["highlights"]], ["positive"] * 3)
        self.assertEqual(view["warnings"][0]["code"], "customer_concentration")
        self.assertFalse(view["model_outputs"]["run"])

    def test_contract_demo_has_no_forecast_and_flags_files(self):
        view = financial_view(completed(contract_result(application_id="demo-2")))

        self.assertFalse(view["forecast"]["ready"])
        self.assertIsNone(view["chart_data"]["forecast"])
        self.assertEqual(view["warnings"][0]["severity"], "high")
        contract = view["files"][0]
        self.assertTrue(contract["problems"])
        self.assertEqual(view["issues"][0]["locator"], "страница 3")
        self.assertIn("Читаемая версия контракта", view["feedback_draft"])


class FinancialViewStateTests(SimpleTestCase):
    def test_not_completed_or_missing_job_keeps_report_state(self):
        no_job = financial_view(None)
        self.assertEqual(no_job["state"], "no_job")
        self.assertEqual(no_job["final_analysis"]["state"], "not_run")
        self.assertFalse(no_job["model_outputs"]["run"])

        pending = financial_view(NodeJob(status=NodeJob.Status.WAITING))
        self.assertEqual(pending["state"], "pending")
        self.assertEqual(
            pending["final_analysis"]["message"],
            "Выполняется обработка заявки. Результат появится после завершения.",
        )
        self.assertFalse(pending["model_outputs"]["run"])

        failed = financial_view(NodeJob(status=NodeJob.Status.FAILED))
        self.assertEqual(failed["state"], "failed")
        self.assertIn("не выполнялся", failed["final_analysis"]["message"])

    def test_processing_error(self):
        payload = {"status": "completed", "result": {"status": "processing_error", "financial_analysis": None}}
        view = financial_view(completed(payload))
        self.assertEqual(view["state"], "processing_error")
        self.assertEqual(view["final_analysis"]["state"], "not_run")
        self.assertIn("не удалось обработать документы", view["final_analysis"]["message"])
        self.assertIn("технической ошибкой", view["model_outputs"]["note"])
        self.assertFalse(view["model_outputs"]["run"])

    def test_old_result_is_unsupported(self):
        payload = {"status": "completed", "result": {"status": "complete", "summary": "старый формат"}}
        view = financial_view(completed(payload))
        self.assertEqual(view["state"], "unsupported")
        self.assertEqual(
            view["final_analysis"]["message"],
            "Для этой обработки итоговый анализ нейросети не выполнялся.",
        )
        self.assertFalse(view["model_outputs"]["run"])

    def test_garbage_fields_do_not_break_view(self):
        payload = load("node-result.json")
        analysis = payload["result"]["financial_analysis"]
        analysis["statistics"] = ["не объект"]
        analysis["cash_flow"]["months"] = ["строка", {"month": "2026-09", "closing_balance": "много"}]
        analysis["warnings"] = [{"message": 42}, None]
        analysis["client_requests"] = "нет"
        analysis["readiness"] = None
        payload["files"] = [{"filename": None}]

        view = financial_view(completed(copy.deepcopy(payload)))

        self.assertEqual(view["state"], "ready")
        self.assertEqual(view["statistics"], [])
        self.assertEqual(len(view["cash_flow"]["months"]), 1)
        self.assertIsNone(view["cash_flow"]["months"][0]["closing"])
        self.assertEqual(view["warnings"], [])
        self.assertEqual(view["client_requests"], [])
        self.assertEqual(view["readiness"]["ready_count"], 0)
        self.assertEqual(view["files"], [])


class FormattingTests(SimpleTestCase):
    def test_money(self):
        self.assertEqual(money(None), "—")
        self.assertEqual(money(23_100_000), "23,1 млн ₽")
        self.assertEqual(money(2_000_000), "2 млн ₽")
        self.assertEqual(money(766_666.67), "767 тыс. ₽")
        self.assertEqual(money(950), "950 ₽")
        self.assertEqual(money(-1_250_000), "-1,2 млн ₽")

    def test_percent_and_month(self):
        self.assertEqual(percent(0.42), "42 %")
        self.assertEqual(month_label("2026-07"), "июл 2026")
        self.assertEqual(month_label("июль"), "июль")

    def test_feedback_draft(self):
        self.assertEqual(feedback_draft([]), "")
        draft = feedback_draft([
            {"title": "Контракт", "message": "Загрузите контракт.", "required": True},
            {"title": "DSCR", "message": "Нужен CFADS.", "required": False},
        ])
        self.assertIn("1. Контракт. Загрузите контракт.", draft)
        self.assertIn("— DSCR. Нужен CFADS.", draft)


class ModelOutputsSchemaTests(SimpleTestCase):
    """Статусы ``model_outputs`` (completed/failed/not_run) проходят схему v2."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))

    def assertValid(self, block):
        analysis = with_model_outputs(block)["result"]["financial_analysis"]
        errors = sorted(self.validator.iter_errors(analysis), key=str)
        self.assertEqual(errors, [], "\n".join(error.message for error in errors[:5]))

    def test_all_required_keys_present(self):
        self.assertEqual(len(MODEL_OUTPUT_KEYS), 13)

    def test_not_run_block_is_valid(self):
        self.assertValid(model_outputs(status=MODEL_STATUS_NOT_RUN))

    def test_completed_block_is_valid(self):
        self.assertValid(model_outputs(
            status=MODEL_STATUS_COMPLETED,
            forecast_revenue_rub_thousands=60000.0,
            safe_credit_limit_rub_thousands=1000.0,
            net_margin_percent=5.0,
            operating_margin_percent=6.5,
            loss_probability_percent=10.0,
            liquidity_crisis_probability_percent=20.0,
            decision="ОДОБРЕНО (рекомендация модели)",
            note="Рекомендация модели, не решение банка.",
        ))

    def test_failed_block_is_valid(self):
        self.assertValid(model_outputs(status=MODEL_STATUS_FAILED, note="техническая ошибка"))

    def test_adapter_completed_output_matches_schema(self):
        from mlinstance.adapter import assemble_model_outputs

        block = assemble_model_outputs({
            "forecast_revenue_rub_thousands": 60000.0,
            "net_margin_percent": 5.0,
            "operating_margin_percent": 6.5,
            "loss_probability_percent": 10.0,
            "liquidity_crisis_probability_percent": 20.0,
        })
        self.assertValid(block)


class ModelOutputsViewTests(SimpleTestCase):
    """Строгий белый список статусов: показываем только корректный completed."""

    def view(self, block):
        return financial_view(completed(with_model_outputs(block)))["model_outputs"]

    def test_completed_shows_finite_values_and_mandatory_disclaimer(self):
        outputs = self.view(model_outputs(
            status=MODEL_STATUS_COMPLETED,
            forecast_revenue_rub_thousands=0.0,   # нуль — легитимное значение
            safe_credit_limit_rub_thousands=0.0,
            net_margin_percent=-12.5,             # отрицательная маржа допустима
            operating_margin_percent=0.0,
            loss_probability_percent=0.0,
            liquidity_crisis_probability_percent=100.0,
            default_probability_percent=None,     # неподдержанное — null, без подделки
            approval_probability_percent=None,
            decision="ОДОБРЕНО (рекомендация модели)",
            note=None,                            # даже без note узла оговорка обязательна
        ))

        self.assertTrue(outputs["run"])
        self.assertFalse(outputs["failed"])
        labels = {entry["label"]: entry["display"] for entry in outputs["entries"]}
        self.assertEqual(labels["Прогноз выручки"], money(0.0))
        self.assertEqual(labels["Безопасный лимит"], money(0.0))
        self.assertEqual(labels["Чистая маржа (прогноз)"], percent(-0.125))
        self.assertEqual(labels["Вероятность убытка"], percent(0.0))
        self.assertEqual(labels["Рекомендация модели"], "ОДОБРЕНО (рекомендация модели)")
        self.assertNotIn("Вероятность дефолта", labels)
        self.assertNotIn("Вероятность одобрения", labels)
        self.assertEqual(outputs["disclaimer"], "Не является решением банка.")

    def test_nonfinite_out_of_range_and_negative_values_are_not_shown(self):
        outputs = self.view(model_outputs(
            status=MODEL_STATUS_COMPLETED,
            forecast_revenue_rub_thousands=float("inf"),
            safe_credit_limit_rub_thousands=-1.0,
            net_margin_percent=float("nan"),
            operating_margin_percent=float("inf"),
            loss_probability_percent=150.0,
            liquidity_crisis_probability_percent=-0.1,
            decision="текст",
        ))

        self.assertTrue(outputs["run"])
        labels = {entry["label"] for entry in outputs["entries"]}
        for label in (
            "Прогноз выручки", "Безопасный лимит", "Чистая маржа (прогноз)",
            "Операционная маржа (прогноз)", "Вероятность убытка",
            "Вероятность дефицита ликвидности",
        ):
            self.assertNotIn(label, labels)
        self.assertIn("Рекомендация модели", labels)

    def test_unknown_missing_and_unhashable_status_never_run(self):
        for status in ("нечто", "", 123, None, ["completed"], {"status": MODEL_STATUS_COMPLETED}):
            outputs = self.view(model_outputs(status=status, forecast_revenue_rub_thousands=999.0))
            self.assertFalse(outputs["run"], status)
            self.assertFalse(outputs["failed"], status)
            self.assertEqual(outputs["entries"], [], status)
            self.assertEqual(outputs["disclaimer"], "", status)

    def test_legacy_not_run_has_no_entries(self):
        outputs = self.view(model_outputs(status=MODEL_STATUS_NOT_RUN))
        self.assertFalse(outputs["run"])
        self.assertFalse(outputs["failed"])
        self.assertEqual(outputs["entries"], [])
        self.assertEqual(outputs["disclaimer"], "")

    def test_failed_shows_safe_message_without_outputs_or_raw_note(self):
        outputs = self.view(model_outputs(status=MODEL_STATUS_FAILED, note="Traceback: secret exception"))

        self.assertFalse(outputs["run"])
        self.assertTrue(outputs["failed"])
        self.assertEqual(outputs["entries"], [])
        self.assertNotIn("Traceback", outputs["note"])
        self.assertNotIn("secret", outputs["note"])
        self.assertIn("Не является решением банка", outputs["note"])

    def test_decision_label_is_advisory_not_bank_decision(self):
        labels = {label for _, label, _ in MODEL_OUTPUT_FIELDS}
        self.assertIn("Рекомендация модели", labels)
        self.assertNotIn("Решение модели", labels)


class ModelOutputsTemplateTests(SimpleTestCase):
    """Шаблон карточки: ветки completed/failed/not_run и отсутствие сырых данных."""

    def render(self, block):
        view = financial_view(completed(with_model_outputs(block)))
        return render_to_string(
            "applications/bank/_analysis_side.html",
            {"financial": view, "can_confirm_inputs": False, "financial_inputs": None},
        )

    def test_completed_branch_renders_disclaimer(self):
        html = self.render(model_outputs(
            status=MODEL_STATUS_COMPLETED,
            forecast_revenue_rub_thousands=1000.0,
            decision="ОДОБРЕНО (рекомендация модели)",
        ))
        self.assertIn("Не является решением банка", html)
        self.assertIn("Рекомендация модели", html)

    def test_failed_branch_renders_message_without_raw_note(self):
        html = self.render(model_outputs(status=MODEL_STATUS_FAILED, note="RAW-TRACE-SECRET"))
        self.assertIn("не выполнен", html)
        self.assertNotIn("RAW-TRACE-SECRET", html)

    def test_not_run_branch_still_renders_placeholder(self):
        html = self.render(model_outputs(status=MODEL_STATUS_NOT_RUN))
        self.assertIn("ML-модель ещё не запускалась", html)
