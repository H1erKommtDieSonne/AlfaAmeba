"""Тесты адаптера годовых ML-моделей (``mlinstance.adapter``).

Только стандартная библиотека: настоящие ``numpy``/``pandas``/``scikit-learn``/
``joblib`` и обученный бандл здесь не загружаются и не выполняются. Обученный
``scorer`` подменяется простым вызываемым объектом, поэтому проверяется контракт
адаптера, а не качество моделей.
"""

from django.test import SimpleTestCase

from mlinstance.adapter import (
    RAW_OUTPUT_FIELDS,
    SCHEMA_VERSION,
    STATUS_COMPLETED,
    STATUS_FAILED,
    UNSUPPORTED_NULL_FIELDS,
    assemble_model_outputs,
    build_response,
)

METRICS = {
    "revenue": 50000.0,
    "net_profit_current": 3000.0,
    "operating_profit_current": 3500.0,
    "total_assets": 40000.0,
    "equity": 15000.0,
    "current_assets": 25000.0,
    "short_term_liabilities": 18000.0,
    "cash": 2000.0,
    "receivables": 12000.0,
    "okved_group": "46.1",
}

RAW = {
    "forecast_revenue_rub_thousands": 60000.0,
    "net_margin_percent": 5.0,
    "operating_margin_percent": 6.5,
    "loss_probability_percent": 10.0,
    "liquidity_crisis_probability_percent": 20.0,
}


def request(**overrides):
    payload = {
        "schema_version": 1,
        "application_id": "app-1",
        "document_version": 3,
        "is_mock": True,
        "annual_model_input": {
            "metrics": dict(METRICS),
            "unit": "RUB_THOUSANDS",
            "currency": "RUB",
            "period_start": None,
            "period_end": "2025-12-31",
            "as_of": None,
            "ready": True,
            "missing_fields": [],
            "sources": {"revenue": [{"locator": "стр. 2110", "is_mock": True}]},
        },
    }
    payload.update(overrides)
    return payload


class Counter:
    def __init__(self, raw=None, error=None):
        self.calls = 0
        self.raw = raw if raw is not None else dict(RAW)
        self.error = error

    def __call__(self, metrics):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.raw)


class MappingTests(SimpleTestCase):
    def test_maps_five_raw_outputs(self):
        response = build_response(request(), Counter())

        self.assertEqual(response["schema_version"], SCHEMA_VERSION)
        self.assertEqual(response["application_id"], "app-1")
        self.assertEqual(response["document_version"], 3)
        outputs = response["model_outputs"]
        self.assertEqual(outputs["status"], STATUS_COMPLETED)
        self.assertEqual(outputs["forecast_revenue_rub_thousands"], 60000.0)
        self.assertEqual(outputs["net_margin_percent"], 5.0)
        self.assertEqual(outputs["operating_margin_percent"], 6.5)
        self.assertEqual(outputs["loss_probability_percent"], 10.0)
        self.assertEqual(outputs["liquidity_crisis_probability_percent"], 20.0)

    def test_unsupported_slots_are_null(self):
        outputs = build_response(request(), Counter())["model_outputs"]
        for name in UNSUPPORTED_NULL_FIELDS:
            self.assertIsNone(outputs[name], name)
        for name in RAW_OUTPUT_FIELDS:
            self.assertIsNotNone(outputs[name], name)

    def test_all_required_model_outputs_present(self):
        outputs = build_response(request(), Counter())["model_outputs"]
        for name in RAW_OUTPUT_FIELDS + UNSUPPORTED_NULL_FIELDS + ("safe_credit_limit_rub_thousands", "decision", "note", "status"):
            self.assertIn(name, outputs)

    def test_is_mock_is_passed_through_unchanged(self):
        self.assertIs(build_response(request(is_mock=True), Counter())["is_mock"], True)
        self.assertIs(build_response(request(is_mock=False), Counter())["is_mock"], False)

    def test_sources_envelope_is_ignored_not_copied(self):
        response = build_response(request(), Counter())
        self.assertNotIn("sources", response)
        self.assertNotIn("sources", response["model_outputs"])

    def test_decision_is_advisory_not_bank_decision(self):
        outputs = build_response(request(), Counter())["model_outputs"]
        self.assertIn("рекомендация модели", outputs["decision"])
        self.assertIn("не решение банка", outputs["note"])


class SafeLimitTests(SimpleTestCase):
    def test_safe_limit_is_twenty_percent_of_monthly_revenue(self):
        outputs = build_response(request(), Counter())["model_outputs"]
        self.assertAlmostEqual(outputs["safe_credit_limit_rub_thousands"], 0.20 * (60000.0 / 12))

    def test_low_margin_applies_half_discount(self):
        raw = dict(RAW, net_margin_percent=0.5)
        outputs = build_response(request(), Counter(raw))["model_outputs"]
        self.assertAlmostEqual(outputs["safe_credit_limit_rub_thousands"], 0.20 * (60000.0 / 12) * 0.5)
        self.assertIn("ДИСКОНТОМ", outputs["decision"])

    def test_high_loss_probability_recommends_rejection(self):
        raw = dict(RAW, loss_probability_percent=41.0)
        outputs = build_response(request(), Counter(raw))["model_outputs"]
        self.assertIn("высокая вероятность убытка", outputs["decision"])

    def test_high_liquidity_probability_recommends_rejection(self):
        raw = dict(RAW, liquidity_crisis_probability_percent=51.0)
        outputs = build_response(request(), Counter(raw))["model_outputs"]
        self.assertIn("кассового разрыва", outputs["decision"])

    def test_negative_margin_is_allowed_not_artificially_clamped(self):
        raw = dict(RAW, net_margin_percent=-12.5)
        outputs = build_response(request(), Counter(raw))["model_outputs"]
        self.assertEqual(outputs["net_margin_percent"], -12.5)


class RequestValidationTests(SimpleTestCase):
    def test_invalid_requests_raise_and_never_call_scorer(self):
        counter = Counter()
        bad_requests = []

        not_ready = request()
        not_ready["annual_model_input"]["ready"] = False
        bad_requests.append(not_ready)

        with_missing = request()
        with_missing["annual_model_input"]["missing_fields"] = ["cash"]
        bad_requests.append(with_missing)

        bad_unit = request()
        bad_unit["annual_model_input"]["unit"] = "RUB"
        bad_requests.append(bad_unit)

        non_finite = request()
        non_finite["annual_model_input"]["metrics"]["cash"] = float("nan")
        bad_requests.append(non_finite)

        boolean_number = request()
        boolean_number["annual_model_input"]["metrics"]["revenue"] = True
        bad_requests.append(boolean_number)

        negative_revenue = request()
        negative_revenue["annual_model_input"]["metrics"]["revenue"] = -1.0
        bad_requests.append(negative_revenue)

        missing_field = request()
        del missing_field["annual_model_input"]["metrics"]["equity"]
        bad_requests.append(missing_field)

        bad_okved = request()
        bad_okved["annual_model_input"]["metrics"]["okved_group"] = "finance"
        bad_requests.append(bad_okved)

        bad_is_mock = request(is_mock="yes")
        bad_requests.append(bad_is_mock)

        bad_version = request(schema_version=2)
        bad_requests.append(bad_version)

        for payload in bad_requests:
            with self.assertRaises(ValueError):
                build_response(payload, counter)
        self.assertEqual(counter.calls, 0)

    def test_negative_profits_and_equity_are_allowed(self):
        counter = Counter()
        payload = request()
        payload["annual_model_input"]["metrics"].update(
            {"net_profit_current": -500.0, "operating_profit_current": -200.0, "equity": -1000.0}
        )
        build_response(payload, counter)
        self.assertEqual(counter.calls, 1)


class RawOutputTests(SimpleTestCase):
    def test_assemble_rejects_non_finite_and_boolean_numbers(self):
        for bad in (float("nan"), float("inf"), True, "5", None):
            raw = dict(RAW, net_margin_percent=bad)
            with self.assertRaises(ValueError):
                assemble_model_outputs(raw)

    def test_probabilities_must_be_within_zero_hundred(self):
        for name in ("loss_probability_percent", "liquidity_crisis_probability_percent"):
            for bad in (-0.1, 100.1):
                with self.assertRaises(ValueError):
                    assemble_model_outputs(dict(RAW, **{name: bad}))

    def test_negative_forecast_revenue_is_rejected(self):
        # Отрицательная выручка противоречит схеме и безопасному лимиту; ноль — нет.
        with self.assertRaises(ValueError):
            assemble_model_outputs(dict(RAW, forecast_revenue_rub_thousands=-1.0))

    def test_zero_forecast_revenue_is_legitimate(self):
        outputs = assemble_model_outputs(dict(RAW, forecast_revenue_rub_thousands=0.0))
        self.assertEqual(outputs["status"], STATUS_COMPLETED)
        self.assertEqual(outputs["forecast_revenue_rub_thousands"], 0.0)
        self.assertEqual(outputs["safe_credit_limit_rub_thousands"], 0.0)


class InferenceErrorTests(SimpleTestCase):
    def test_scorer_error_yields_failed_without_leaking_or_false_success(self):
        response = build_response(request(), Counter(error=OverflowError("exp overflow")))

        outputs = response["model_outputs"]
        self.assertEqual(outputs["status"], STATUS_FAILED)
        for name in RAW_OUTPUT_FIELDS + ("safe_credit_limit_rub_thousands",) + UNSUPPORTED_NULL_FIELDS:
            self.assertIsNone(outputs[name], name)
        self.assertIsNone(outputs["decision"])
        self.assertNotIn("overflow", outputs["note"])
        self.assertEqual(response["is_mock"], True)

    def test_non_finite_scorer_output_becomes_failed(self):
        response = build_response(request(), Counter(dict(RAW, forecast_revenue_rub_thousands=float("inf"))))
        self.assertEqual(response["model_outputs"]["status"], STATUS_FAILED)
        self.assertIsNone(response["model_outputs"]["forecast_revenue_rub_thousands"])

    def test_negative_forecast_revenue_becomes_failed(self):
        response = build_response(request(), Counter(dict(RAW, forecast_revenue_rub_thousands=-1.0)))
        self.assertEqual(response["model_outputs"]["status"], STATUS_FAILED)
        self.assertIsNone(response["model_outputs"]["forecast_revenue_rub_thousands"])
