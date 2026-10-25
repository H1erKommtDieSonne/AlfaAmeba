"""Фокусные тесты неизменяемого ``application_context`` для узла проверки."""

import json
import uuid
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from applications.models import ApplicationVersion

from .models import NodeJob
from .outbound import (
    MAX_CONTEXT_BYTES,
    InvalidOutboundRequest,
    build_application_context,
    run_once,
    serialize_application_context,
    validate_application_context,
)
from .test_outbound import OutboundTestBase

# Легаси-ключи контекста (v1) плюс новые аналитические поля v2.
LEGACY_KEYS = {
    "schema_version",
    "application_id",
    "version_id",
    "version_number",
    "product_code",
    "collateral",
    "checklist",
    "checklist_source",
}
# v2 fields always present; others only when known.
V2_ALWAYS_KEYS = {"document_version", "has_collateral"}
CONTEXT_KEYS = LEGACY_KEYS | V2_ALWAYS_KEYS | {"loan_type", "analysis_date"}
# Fields that must never be sent to the node (personal/service data).
FORBIDDEN_KEYS = {"organization_name", "purpose", "recommendation", "snapshot"}

SUBMITTED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt_timezone.utc)


def _stub(**overrides):
    values = {
        "application_id": uuid.uuid4(),
        "pk": uuid.uuid4(),
        "version_number": 3,
        "product_id": "working-capital",
        "snapshot": {},
        "amount": Decimal("1500000.00"),
        "term_months": 12,
        "submitted_at": SUBMITTED,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ApplicationContextBuildTests(SimpleTestCase):
    def test_snapshot_context_is_canonical_and_minimized(self):
        version = _stub(
            snapshot={
                "product_code": "contract-finance",
                "needs": {"collateral": True},
                "inn": "7707083893",
                "amount": "2500000.50",
                "term_months": 24,
                "submitted_at": "2026-03-04T05:06:07+00:00",
                "checklist": [
                    {
                        "code": "statement",
                        "name": "Выписка по счёту",
                        "reason": "Подтвердить обороты",
                        "uploaded": True,
                        "extra": "drop-me",
                    }
                ],
                "organization_name": "ООО Секрет",
                "purpose": "не передаём",
                "recommendation": "одобрить",
            }
        )
        context = build_application_context(version)
        self.assertEqual(set(context), LEGACY_KEYS | V2_ALWAYS_KEYS | {
            "loan_type", "borrower_inn", "loan_amount_rub", "term_months", "analysis_date",
        })
        self.assertEqual(context["schema_version"], 2)
        # Contract v2: INN is required to reject another person data.
        self.assertEqual(context["borrower_inn"], "7707083893")
        self.assertEqual(context["loan_type"], "contract")
        # Appendices are never implied by loan type alone.
        self.assertNotIn("contract_appendices_required", context)
        self.assertIs(context["has_collateral"], True)
        self.assertEqual(context["application_id"], str(version.application_id))
        self.assertEqual(context["version_id"], str(version.pk))
        self.assertEqual(context["version_number"], 3)
        self.assertEqual(context["document_version"], 3)
        self.assertEqual(context["product_code"], "contract-finance")
        self.assertIs(context["collateral"], True)
        self.assertIs(context["has_collateral"], True)
        self.assertEqual(context["loan_type"], "contract")
        self.assertEqual(context["borrower_inn"], "7707083893")
        self.assertEqual(context["loan_amount_rub"], 2500000.5)
        self.assertIsInstance(context["loan_amount_rub"], float)
        self.assertEqual(context["term_months"], 24)
        self.assertEqual(context["analysis_date"], "2026-03-04")
        self.assertEqual(context["checklist_source"], "snapshot")
        self.assertEqual(
            context["checklist"],
            [
                {
                    "code": "statement",
                    "name": "Выписка по счёту",
                    "reason": "Подтвердить обороты",
                }
            ],
        )
        serialized = json.dumps(context, ensure_ascii=False)
        for forbidden in ("ООО Секрет", "не передаём", "одобрить", "uploaded", "drop-me"):
            self.assertNotIn(forbidden, serialized)
        # ИНН намеренно передаётся узлу проверки документов.
        self.assertIn("7707083893", serialized)
        self.assertFalse(FORBIDDEN_KEYS & set(context))

    def test_loan_type_mapping_and_unknown_product_omitted(self):
        cases = {
            "working-capital": "working_capital",
            "contract-finance": "contract",
            "investment": "investment",
            "refinancing": "refinancing",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                context = build_application_context(
                    _stub(snapshot={"product_code": code, "needs": {"collateral": False}})
                )
                self.assertEqual(context["loan_type"], expected)
        for code in ("leasing", "overdraft", "factoring", "credit-line"):
            with self.subTest(code=code):
                context = build_application_context(
                    _stub(snapshot={"product_code": code, "needs": {"collateral": False}})
                )
                self.assertNotIn("loan_type", context)

    def test_term_and_analysis_date_fall_back_to_version_fields(self):
        context = build_application_context(
            _stub(
                snapshot={"needs": {"collateral": False}, "product_code": "working-capital"},
                amount=Decimal("100.00"),
                term_months=36,
                submitted_at=SUBMITTED,
            )
        )
        self.assertEqual(context["term_months"], 36)
        self.assertEqual(context["analysis_date"], "2026-01-02")
        # Сумма берётся только из снимка, поле версии для неё не фолбэк.
        self.assertEqual(context["loan_amount_rub"], 100.0)

    def test_contract_appendices_only_from_explicit_snapshot_flag(self):
        for value, expected in ((True, True), (False, False)):
            with self.subTest(value=value):
                context = build_application_context(
                    _stub(
                        snapshot={
                            "needs": {"collateral": False},
                            "product_code": "contract-finance",
                            "contract_appendices_required": value,
                        }
                    )
                )
                self.assertEqual(context["loan_type"], "contract")
                self.assertIs(context["contract_appendices_required"], expected)
        # Even contract type does not imply appendices without the explicit flag.
        context = build_application_context(
            _stub(snapshot={"needs": {"collateral": False}, "product_code": "contract-finance"})
        )
        self.assertNotIn("contract_appendices_required", context)
        # Non-strict values are not guessed.
        context = build_application_context(
            _stub(
                snapshot={
                    "needs": {"collateral": False},
                    "contract_appendices_required": "true",
                }
            )
        )
        self.assertNotIn("contract_appendices_required", context)

    def test_explicit_analysis_date_overrides_snapshot_and_version(self):
        context = build_application_context(
            _stub(snapshot={"needs": {"collateral": False}}),
            analysis_date=datetime(2026, 10, 24, 12, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(context["analysis_date"], "2026-10-24")

    def test_collateral_unknown_is_explicit_null_not_false(self):
        cases = [
            {},
            {"needs": None},
            {"needs": "x"},
            {"needs": {}},
            {"needs": {"collateral": "on"}},
            {"needs": {"collateral": 0}},
            {"needs": {"collateral": "false"}},
        ]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot):
                context = build_application_context(_stub(snapshot=snapshot))
                self.assertIsNone(context["collateral"])
                self.assertIsNone(context["has_collateral"])
        self.assertIs(
            build_application_context(_stub(snapshot={"needs": {"collateral": False}}))["collateral"],
            False,
        )
        self.assertIs(
            build_application_context(_stub(snapshot={"needs": {"collateral": True}}))["collateral"],
            True,
        )

    def test_unknown_collateral_is_rejected_before_network(self):
        context = build_application_context(_stub(snapshot={}))
        self.assertIsNone(context["has_collateral"])
        with self.assertRaises(InvalidOutboundRequest) as raised:
            serialize_application_context(context)
        self.assertIn("залог", str(raised.exception).lower())
        with self.assertRaises(InvalidOutboundRequest):
            validate_application_context(context)

    def test_invalid_present_analytical_fields_are_rejected(self):
        bad_snapshots = [
            {"needs": {"collateral": False}, "inn": "123"},
            {"needs": {"collateral": False}, "inn": 7707083893},
            {"needs": {"collateral": False}, "amount": True},
            {"needs": {"collateral": False}, "amount": "not-a-number"},
            {"needs": {"collateral": False}, "amount": "-5"},
            {"needs": {"collateral": False}, "amount": "1e19"},
            {"needs": {"collateral": False}, "term_months": True},
            {"needs": {"collateral": False}, "term_months": "12"},
            {"needs": {"collateral": False}, "term_months": 0},
            {"needs": {"collateral": False}, "term_months": 601},
            {"needs": {"collateral": False}, "submitted_at": "not-a-date"},
        ]
        for snapshot in bad_snapshots:
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(InvalidOutboundRequest):
                    build_application_context(_stub(snapshot=snapshot))
        # Невалидное поле версии-фолбэка тоже отклоняется.
        with self.assertRaises(InvalidOutboundRequest):
            build_application_context(
                _stub(snapshot={"needs": {"collateral": False}}, term_months=0)
            )

    def test_missing_optional_fields_are_omitted_and_safe(self):
        version = _stub(
            snapshot={"needs": {"collateral": False}},
            product_id=None,
            amount=None,
            term_months=None,
            submitted_at=None,
        )
        context = build_application_context(version)
        for absent in ("loan_type", "borrower_inn", "loan_amount_rub", "term_months", "analysis_date"):
            self.assertNotIn(absent, context)
        # Без необязательных дат/полей контекст всё ещё сериализуется.
        text = serialize_application_context(context)
        self.assertIn("has_collateral", text)

    def test_product_code_prefers_snapshot_then_version(self):
        self.assertEqual(
            build_application_context(
                _stub(snapshot={"product_code": "legacy-old-code"})
            )["product_code"],
            "legacy-old-code",
        )
        self.assertEqual(
            build_application_context(
                _stub(snapshot={"product_code": "bad code!"})
            )["product_code"],
            "working-capital",
        )
        self.assertEqual(
            build_application_context(_stub(snapshot={}))["product_code"],
            "working-capital",
        )

    def test_checklist_fallback_uses_product_rules_only_when_known(self):
        known = _stub(snapshot={"product_code": "working-capital"})
        context = build_application_context(known)
        self.assertEqual(context["checklist_source"], "fallback")
        self.assertEqual([item["code"] for item in context["checklist"]], ["statement"])
        self.assertIn("name", context["checklist"][0])

        unknown = _stub(product_id=None, snapshot={})
        context = build_application_context(unknown)
        self.assertIsNone(context["product_code"])
        self.assertEqual(context["checklist"], [])
        self.assertEqual(context["checklist_source"], "empty")

        # Исторический снимок со своим кодом не подменяется текущим каталогом.
        historic = _stub(snapshot={"product_code": "retired-product"})
        context = build_application_context(historic)
        self.assertEqual(context["product_code"], "retired-product")
        self.assertEqual(context["checklist_source"], "fallback")

    def test_malformed_snapshot_shapes_never_raise(self):
        cases = [
            None,
            "not-a-mapping",
            ["x"],
            {"needs": ["x"], "checklist": {"code": "x"}},
            {"checklist": ["bad", {"code": 123}, {"no": "code"}, None, 5]},
            {"needs": {"collateral": []}, "checklist": []},
            {"checklist": [{"code": "statement", "name": None, "reason": 42}]},
        ]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot):
                context = build_application_context(_stub(snapshot=snapshot))
                self.assertTrue(V2_ALWAYS_KEYS <= set(context))
                self.assertIsInstance(context["checklist"], list)
        # Пустой/битый список снимка уходит в fallback, а не в падение.
        context = build_application_context(
            _stub(snapshot={"checklist": [{"code": ""}, {"code": "ok"}]})
        )
        self.assertEqual([item["code"] for item in context["checklist"]], ["ok"])
        self.assertEqual(context["checklist_source"], "snapshot")

    def test_serializer_is_utf8_not_escaped_and_bounded(self):
        text = serialize_application_context(
            {"name": "Выписка", "product_code": "working-capital"}
        )
        self.assertIn("Выписка", text)
        self.assertNotIn("\\u0412", text)
        self.assertLessEqual(len(text.encode("utf-8")), MAX_CONTEXT_BYTES)

    def test_serializer_rejects_oversize_nan_and_surrogate(self):
        oversize = {"checklist": [{"reason": "я" * 1000} for _ in range(120)]}
        with self.assertRaises(InvalidOutboundRequest):
            serialize_application_context(oversize)
        with self.assertRaises(InvalidOutboundRequest):
            serialize_application_context({"x": float("nan")})
        with self.assertRaises(InvalidOutboundRequest):
            serialize_application_context({"x": "\ud800"})


class ApplicationContextSendTests(OutboundTestBase):
    def _context_part(self, request):
        parts = self._multipart_parts(request)
        names = [part.get_param("name", header="content-disposition") for part in parts]
        self.assertEqual(names[1], "application_context")
        self.assertEqual(names[0], "system_prompt")
        return parts[1]

    def test_application_context_is_sent_as_valid_utf8_json(self):
        job = self._make_job()
        ApplicationVersion.objects.filter(pk=job.version_id).update(
            snapshot={
                "product_code": "contract-finance",
                "needs": {"collateral": True},
                "inn": "7707083893",
                "checklist": [
                    {"code": "statement", "name": "Выписка по счёту", "reason": "Обороты"},
                    {"code": "contract", "name": "Контракт", "reason": "Предмет сделки"},
                ],
            }
        )
        self._set_response(payload=self._success_payload())
        with self._settings():
            run_once()

        raw = self._context_part(self.server.requests[0]).get_payload(decode=True)
        self.assertIn("Выписка".encode("utf-8"), raw)
        context = json.loads(raw.decode("utf-8"))
        self.assertEqual(context["product_code"], "contract-finance")
        self.assertIs(context["collateral"], True)
        self.assertIs(context["has_collateral"], True)
        self.assertEqual(context["loan_type"], "contract")
        self.assertEqual(context["borrower_inn"], "7707083893")
        self.assertEqual(context["document_version"], job.version.version_number)
        self.assertEqual(
            [item["code"] for item in context["checklist"]], ["statement", "contract"]
        )
        self.assertEqual(context["checklist_source"], "snapshot")

    def test_unknown_collateral_fails_locally_without_network(self):
        job = self._make_job()
        ApplicationVersion.objects.filter(pk=job.version_id).update(
            snapshot={"product_code": "working-capital"}
        )
        with self._settings():
            run_once()
        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.FAILED)
        self.assertEqual(job.attempts, 1)
        self.assertIsNone(job.external_job_id)
        self.assertEqual(self.server.requests, [])
        self.assertIn("залог", job.last_error.lower())

    def test_oversize_context_fails_locally_without_network(self):
        job = self._make_job()
        ApplicationVersion.objects.filter(pk=job.version_id).update(
            snapshot={
                "needs": {"collateral": False},
                "checklist": [{"code": "c", "reason": "я" * 1000} for _ in range(120)],
            }
        )

        with self._settings():
            run_once()

        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.FAILED)
        self.assertEqual(job.attempts, 1)
        self.assertIsNone(job.external_job_id)
        self.assertEqual(self.server.requests, [])
        self.assertIn("контекст", job.last_error.lower())

    def test_context_does_not_change_file_payloads(self):
        job = self._make_job()
        self._set_response(payload=self._success_payload())
        with self._settings():
            run_once()
        parts = self._multipart_parts(self.server.requests[0])
        files = [p for p in parts if p.get_param("name", header="content-disposition") == "files"]
        self.assertEqual(
            [(p.get_filename(), p.get_payload(decode=True)) for p in files],
            [("document-0.pdf", b"%PDF-1.4 synthetic 0")],
        )
