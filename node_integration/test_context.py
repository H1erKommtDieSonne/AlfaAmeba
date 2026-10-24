"""Фокусные тесты неизменяемого ``application_context`` для узла проверки."""

import json
import uuid
from types import SimpleNamespace

from django.test import SimpleTestCase

from applications.models import ApplicationVersion

from .models import NodeJob
from .outbound import (
    MAX_CONTEXT_BYTES,
    InvalidOutboundRequest,
    build_application_context,
    run_once,
    serialize_application_context,
)
from .test_outbound import OutboundTestBase

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
# Поля контракта v2, которые есть всегда; остальные — только когда известны.
V2_ALWAYS_KEYS = {"document_version", "has_collateral", "contract_appendices_required", "analysis_date"}
# Заглушка без суммы, срока и ИНН, но с известным продуктом working-capital.
CONTEXT_KEYS = LEGACY_KEYS | V2_ALWAYS_KEYS | {"loan_type"}


def _stub(**overrides):
    values = {
        "application_id": uuid.uuid4(),
        "pk": uuid.uuid4(),
        "version_number": 3,
        "product_id": "working-capital",
        "snapshot": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ApplicationContextBuildTests(SimpleTestCase):
    def test_snapshot_context_is_canonical_and_minimized(self):
        version = _stub(
            snapshot={
                "product_code": "contract-finance",
                "needs": {"collateral": True},
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
                "inn": "7707083893",
                "purpose": "не передаём",
            }
        )
        context = build_application_context(version)
        self.assertEqual(set(context), LEGACY_KEYS | V2_ALWAYS_KEYS | {"loan_type", "borrower_inn"})
        self.assertEqual(context["schema_version"], 2)
        # Контракт v2: ИНН нужен узлу, чтобы отсечь значения другого лица.
        self.assertEqual(context["borrower_inn"], "7707083893")
        self.assertEqual(context["loan_type"], "contract")
        self.assertIs(context["contract_appendices_required"], True)
        self.assertIs(context["has_collateral"], True)
        self.assertEqual(context["application_id"], str(version.application_id))
        self.assertEqual(context["version_id"], str(version.pk))
        self.assertEqual(context["version_number"], 3)
        self.assertEqual(context["product_code"], "contract-finance")
        self.assertIs(context["collateral"], True)
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
        for forbidden in ("ООО Секрет", "не передаём", "uploaded", "drop-me"):
            self.assertNotIn(forbidden, serialized)

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
                self.assertIsNone(build_application_context(_stub(snapshot=snapshot))["collateral"])
        self.assertIs(
            build_application_context(_stub(snapshot={"needs": {"collateral": False}}))["collateral"],
            False,
        )
        self.assertIs(
            build_application_context(_stub(snapshot={"needs": {"collateral": True}}))["collateral"],
            True,
        )

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
                self.assertEqual(set(context), CONTEXT_KEYS)
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
        self.assertEqual(
            [item["code"] for item in context["checklist"]], ["statement", "contract"]
        )
        self.assertEqual(context["checklist_source"], "snapshot")

    def test_oversize_context_fails_locally_without_network(self):
        job = self._make_job()
        ApplicationVersion.objects.filter(pk=job.version_id).update(
            snapshot={"checklist": [{"code": "c", "reason": "я" * 1000} for _ in range(120)]}
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
