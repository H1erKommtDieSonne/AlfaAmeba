import copy
import json
from pathlib import Path

import jsonschema
from django.conf import settings
from django.test import SimpleTestCase

from .demo import contract_result, working_capital_result
from .financial import feedback_draft, financial_view, money, month_label, percent
from .models import NodeJob

FIXTURES = Path(__file__).parent / "fixtures" / "financial_v2"
SCHEMA = Path(settings.BASE_DIR) / "docs" / "schemas" / "financial-result-v2.schema.json"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def completed(payload):
    return NodeJob(status=NodeJob.Status.COMPLETED, result=payload)


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
    def test_not_completed_or_missing_job(self):
        self.assertIsNone(financial_view(None))
        self.assertIsNone(financial_view(NodeJob(status=NodeJob.Status.WAITING)))

    def test_processing_error(self):
        payload = {"status": "completed", "result": {"status": "processing_error", "financial_analysis": None}}
        self.assertEqual(financial_view(completed(payload)), {"state": "processing_error"})

    def test_old_result_is_unsupported(self):
        payload = {"status": "completed", "result": {"status": "complete", "summary": "старый формат"}}
        self.assertEqual(financial_view(completed(payload)), {"state": "unsupported"})

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
