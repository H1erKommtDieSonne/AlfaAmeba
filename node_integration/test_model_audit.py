"""Optional ``financial_analysis.model_audit`` provenance block (schema v2).

The external node added a ``model_audit`` block that is pure provenance (runtime
versions, model keys, feature assumptions, thresholds). It is NOT a credit
decision and the bank UI never renders it. This block is optional: the schema
must accept both payloads with and without it (backward compatibility) and must
reject malformed audit payloads without relaxing the rest of the financial
schema.
"""

import copy
import json
import sqlite3
from pathlib import Path
from unittest import skipUnless

import jsonschema
from django.conf import settings
from django.test import SimpleTestCase

FIXTURES = Path(__file__).parent / "fixtures" / "financial_v2"
SCHEMA = Path(settings.BASE_DIR) / "docs" / "schemas" / "financial-result-v2.schema.json"
# Real sanitized artifact from the isolated CREDS-LIVE-E2E run (throwaway, in temp).
ISOLATED_DB = Path(r"C:\Temp\opencode\creds-e2e\20261025-040012\e2e.db")

VALID_AUDIT = {
    "schema_version": 1,
    "is_mock": True,
    "input_unit": "RUB_THOUSANDS",
    "reporting_year": 2025,
    "target_year": 2026,
    "application_id": "00000000-0000-0000-0000-000000000000",
    "document_version": 1,
    "feature_assumptions": [
        {"field": "revenue", "is_observed": True, "method": "reported"},
        {"field": "ebitda", "is_observed": False, "method": "derived"},
    ],
    "model": {
        "artifact_sha256": "0" * 64,
        "duration_ms": 12.5,
        "effective_okved_group": "G",
        "model_keys": ["risk_grade", "limit"],
        "runtime": {
            "joblib": "1.4.0",
            "lightgbm": "4.3.0",
            "numpy": "1.26.0",
            "pandas": "2.2.0",
            "scikit-learn": "1.4.0",
        },
        "version": "v1",
    },
    "model_outputs": {
        "status": "completed",
        "decision": "approve",
        "risk_grade": None,
        "note": "provenance only",
        "forecast_revenue_rub_thousands": 1.0,
        "safe_credit_limit_rub_thousands": 2.0,
        "net_margin_percent": 3.0,
        "operating_margin_percent": 4.0,
        "loss_probability_percent": 5.0,
        "liquidity_crisis_probability_percent": 6.0,
        "default_probability_percent": None,
        "approval_probability_percent": None,
        "operating_cash_flow_forecast_rub": None,
    },
    "recommendation": {
        "code": "code",
        "final_bank_decision": False,
        "label": "label",
        "limit_formula": "formula",
        "policy_version": "policy-1",
        "reason": "reason",
        "thresholds": {
            "liquidity_crisis_probability_percent_gt": 10.0,
            "loss_probability_percent_gt": 20.0,
            "net_margin_percent_lt": 1.0,
        },
    },
    "warnings": [{"code": "code", "message": "message", "severity": "low"}],
}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def base_financial():
    return copy.deepcopy(load("node-result.json")["result"]["financial_analysis"])


class ModelAuditSchemaTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.validator = jsonschema.Draft202012Validator(
            json.loads(SCHEMA.read_text(encoding="utf-8"))
        )

    def assertValid(self, financial):
        errors = sorted(self.validator.iter_errors(financial), key=str)
        self.assertEqual(errors, [], "\n".join(e.message for e in errors[:5]))

    def assertInvalid(self, financial):
        self.assertTrue(list(self.validator.iter_errors(financial)))

    def test_existing_fixtures_remain_valid(self):
        self.assertValid(load("node-result.json")["result"]["financial_analysis"])
        self.assertValid(load("node-result-needs-documents.json")["result"]["financial_analysis"])

    def test_optional_absent_is_valid(self):
        financial = base_financial()
        self.assertNotIn("model_audit", financial)
        self.assertValid(financial)

    def test_optional_audit_present_is_valid(self):
        financial = base_financial()
        financial["model_audit"] = copy.deepcopy(VALID_AUDIT)
        self.assertValid(financial)

    def test_audit_without_unknown_optional_keys_is_valid(self):
        financial = base_financial()
        financial["model_audit"] = {"schema_version": 1, "is_mock": True}
        self.assertValid(financial)

    def test_malformed_audit_rejected(self):
        cases = {
            "not_an_object": [],
            "unexpected_top_key": {**VALID_AUDIT, "unexpected": 1},
            "nested_extra_key": {
                **VALID_AUDIT,
                "model": {**VALID_AUDIT["model"], "extra": 1},
            },
            "wrong_runtime_type": {
                **VALID_AUDIT,
                "model": {**VALID_AUDIT["model"], "runtime": {"joblib": 5}},
            },
            "feature_item_extra_key": {
                **VALID_AUDIT,
                "feature_assumptions": [
                    {**VALID_AUDIT["feature_assumptions"][0], "extra": 1}
                ],
            },
            "thresholds_wrong_type": {
                **VALID_AUDIT,
                "recommendation": {
                    **VALID_AUDIT["recommendation"],
                    "thresholds": {"net_margin_percent_lt": "x"},
                },
            },
        }
        for name, audit in cases.items():
            financial = base_financial()
            financial["model_audit"] = audit
            with self.subTest(case=name):
                self.assertInvalid(financial)

    def test_root_not_relaxed_to_additional_properties(self):
        financial = base_financial()
        financial["unexpected_root_key"] = 1
        self.assertInvalid(financial)


@skipUnless(ISOLATED_DB.exists(), "isolated E2E artifact not present")
class IsolatedArtifactSchemaTests(SimpleTestCase):
    """Validate the real saved node result once; borrower values are never persisted."""

    def test_real_isolated_financial_analysis_is_valid(self):
        validator = jsonschema.Draft202012Validator(
            json.loads(SCHEMA.read_text(encoding="utf-8"))
        )
        con = sqlite3.connect(
            "file:" + str(ISOLATED_DB).replace("\\", "/") + "?mode=ro", uri=True
        )
        try:
            row = con.execute("SELECT result FROM node_integration_nodejob").fetchone()
        finally:
            con.close()
        financial = json.loads(row[0])["result"]["financial_analysis"]
        self.assertIn("model_audit", financial)
        errors = sorted(validator.iter_errors(financial), key=str)
        self.assertEqual(errors, [], "\n".join(e.message for e in errors[:5]))
