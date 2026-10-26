"""Фокусные тесты необязательного ``result.final_analysis`` и ``purpose``.

Тексты здесь синтетические: ни фикстуры, ни демо-результаты не выдаются за
настоящий ответ LLM. Проверяются только безопасное представление, шаблон,
отправка цели кредита и приём callback без изменения проводного контракта.
"""

import copy
import json
from decimal import Decimal

from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application, ApplicationVersion
from core.testing import CreditTestCase

from .final_analysis import (
    ABSENT_MESSAGE,
    DISCLAIMER,
    FAILED_MESSAGE,
    INVALID_MESSAGE,
    final_analysis_view,
)
from .financial import financial_view
from .models import NodeJob
from .outbound import build_application_context
from .services import canonical_json, sha256_json

MISSING = object()
TOKEN = "test-final-analysis-callback-token"


def _financial(is_mock=False):
    return {
        "schema_version": 2,
        "is_mock": is_mock,
        "readiness": {},
        "warnings": [],
        "cash_flow": {},
        "annual_model_input": {},
        "next_month_forecast": {},
        "statistics": {},
        "client_requests": [],
        "model_outputs": {"status": "not_run"},
    }


def _inner(final=MISSING, *, financial_is_mock=False):
    inner = {
        "schema_version": 2,
        "status": "complete",
        "financial_analysis": _financial(financial_is_mock),
    }
    if final is not MISSING:
        inner["final_analysis"] = copy.deepcopy(final)
    return inner


def _job(final=MISSING, *, financial_is_mock=False):
    return NodeJob(
        status=NodeJob.Status.COMPLETED,
        result={"result": _inner(final, financial_is_mock=financial_is_mock)},
    )


def _block(**overrides):
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
        "provenance": {
            "model": "final-model",
            "prompt_version": "final-analysis-1",
            "generated_at": "2026-10-26T12:00:00Z",
            "evidence_hash": "a" * 64,
        },
        "not_a_decision": True,
    }
    block.update(overrides)
    return block


def _render(view):
    return render_to_string(
        "applications/bank/_analysis_side.html",
        {"financial": view, "can_confirm_inputs": False},
    )


class FinalAnalysisProjectionTests(SimpleTestCase):
    def test_completed_valid_is_bounded_and_projected(self):
        block = _block(
            summary="s" * 2200,
            key_points=[f"p{index}" for index in range(10)],
            risks=["r" * 600],
            unknown_secret="RAW-UNKNOWN",
        )
        final = financial_view(_job(block))["final_analysis"]

        self.assertEqual(final["state"], "completed")
        self.assertEqual(len(final["summary"]), 2000)
        self.assertEqual(len(final["key_points"]), 8)
        self.assertEqual(len(final["risks"][0]), 500)
        self.assertEqual(final["provenance"]["prompt_version"], "final-analysis-1")
        self.assertIs(final["not_a_decision"], True)
        self.assertNotIn("unknown_secret", final)
        self.assertNotIn("RAW-UNKNOWN", repr(final))

    def test_completed_render_escapes_html_and_shows_disclaimer(self):
        block = _block(
            summary='<script>alert("x")</script>',
            risks=["<b>риск</b>"],
            is_mock=True,
        )
        view = financial_view(_job(block, financial_is_mock=False))
        html = _render(view)

        self.assertTrue(view["is_mock"])
        self.assertIn("Анализ финмодели", html)
        self.assertIn(
            "Нейросетевое заключение по типу и цели кредита, финансовым данным "
            "и прогнозам ML.",
            html,
        )
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", html)
        self.assertIn("&lt;b&gt;риск&lt;/b&gt;", html)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertNotIn("<b>риск</b>", html)
        self.assertIn(DISCLAIMER, html)
        self.assertIn("Демо-данные", html)

    def test_absent_block_is_not_run_and_keeps_existing_outputs(self):
        view = financial_view(_job(MISSING))
        html = _render(view)

        self.assertEqual(view["state"], "ready")
        self.assertEqual(view["final_analysis"]["state"], "not_run")
        self.assertEqual(view["final_analysis"]["message"], ABSENT_MESSAGE)
        for key in ("statistics", "model_outputs", "readiness", "warnings"):
            self.assertIn(key, view)
        self.assertIn("Анализ финмодели", html)
        self.assertIn("Расчёт ML-моделей", html)
        self.assertIn("не выполнялся", html)

    def test_failed_and_not_run_are_fixed_and_hide_raw_text(self):
        cases = [
            (_block(status="failed", summary="RAW-EXCEPTION-SECRET"), "failed", FAILED_MESSAGE),
            (_block(status="not_run", summary="RAW-PROMPT-SECRET"), "not_run", ABSENT_MESSAGE),
        ]
        for block, state, message in cases:
            with self.subTest(state=state):
                final = financial_view(_job(block))["final_analysis"]
                html = _render(financial_view(_job(block)))
                self.assertEqual(final["state"], state)
                self.assertEqual(final["message"], message)
                self.assertEqual(final["summary"], "")
                self.assertNotIn("RAW-EXCEPTION-SECRET", html)
                self.assertNotIn("RAW-PROMPT-SECRET", html)

    def test_malformed_unknown_and_bool_schema_are_not_accepted(self):
        broken = [
            "not-a-dict",
            _block(schema_version=True),
            _block(schema_version="1"),
            _block(status="processing"),
            _block(not_a_decision=False),
            _block(
                summary=123,
                key_points=[],
                risks=[],
                missing=[],
                conflicts=[],
                next_steps=[],
                limitations=[],
            ),
        ]
        for block in broken:
            with self.subTest(block=block):
                final = final_analysis_view({"final_analysis": block})
                self.assertEqual(final["state"], "invalid")
                self.assertEqual(final["message"], INVALID_MESSAGE)
        # True == 1 в Python, поэтому булева версия схемы не должна проходить.
        self.assertEqual(final_analysis_view({"final_analysis": _block(schema_version=True)})["state"], "invalid")

    def test_malformed_provenance_is_ignored_or_filtered(self):
        final = final_analysis_view(
            {
                "final_analysis": _block(
                    provenance={
                        "model": "safe-model",
                        "prompt_version": "future-prompt",
                        "generated_at": 123,
                        "evidence_hash": "h" * 200,
                        "secret": "RAW-PROVENANCE",
                    }
                )
            }
        )
        self.assertEqual(final["state"], "completed")
        self.assertEqual(final["provenance"]["model"], "safe-model")
        self.assertNotIn("prompt_version", final["provenance"])
        self.assertNotIn("RAW-PROVENANCE", repr(final))

    def test_mock_flag_is_or_between_blocks(self):
        cases = [
            (False, True, True),
            (True, False, True),
            (False, False, False),
        ]
        for financial_mock, final_mock, expected in cases:
            with self.subTest(financial=financial_mock, final=final_mock):
                view = financial_view(
                    _job(_block(is_mock=final_mock), financial_is_mock=financial_mock)
                )
                self.assertIs(view["is_mock"], expected)


class PurposeContextTests(SimpleTestCase):
    def _version(self, purpose, *, snapshot_purpose="STALE-SNAPSHOT"):
        # Несохранённая модель с заранее заданной связанной заявкой: context
        # читает реальные поля ApplicationVersion, но не ходит в БД.
        application = Application(inn="7707083893")
        return ApplicationVersion(
            application=application,
            version_number=1,
            product_id="working-capital",
            amount=Decimal("1500000.00"),
            term_months=12,
            purpose=purpose,
            submitted_at=None,
            snapshot={
                "needs": {"collateral": False},
                "purpose": snapshot_purpose,
            },
        )

    def test_purpose_comes_from_real_version_not_snapshot(self):
        context = build_application_context(self._version("Цель " + "x" * 1200))
        self.assertEqual(context["purpose"], ("Цель " + "x" * 1200)[:1000])
        self.assertNotIn("STALE-SNAPSHOT", json.dumps(context, ensure_ascii=False))

    def test_absent_or_blank_purpose_is_omitted(self):
        for purpose in ("", "   "):
            with self.subTest(purpose=purpose):
                self.assertNotIn("purpose", build_application_context(self._version(purpose)))


@override_settings(DOCUMENT_NODE_CALLBACK_TOKEN=TOKEN)
class FinalAnalysisCallbackTests(CreditTestCase):
    def setUp(self):
        submitted_at = timezone.now()
        application = Application.objects.create(
            organization=self.organization,
            organization_name=self.organization.name,
            inn=self.organization.inn,
            created_by=self.business_user,
            status=Application.Status.SUBMITTED,
            submitted_at=submitted_at,
        )
        self.version = ApplicationVersion.objects.create(
            application=application,
            version_number=1,
            product=self.product,
            amount="1500000.00",
            term_months=12,
            purpose="Закупка сезонной партии товара на три месяца",
            submitted_at=submitted_at,
            snapshot={"needs": {"collateral": False}},
        )
        self.job = NodeJob.objects.create(
            version=self.version,
            external_job_id="f" * 32,
            status=NodeJob.Status.WAITING,
        )

    def _payload(self):
        return {
            "job_id": self.job.external_job_id,
            "status": "completed",
            "result": {
                "schema_version": 2,
                "financial_analysis": _financial(),
                "final_analysis": _block(),
            },
            "files": [],
            "verification_status": "needs_review",
            "model": "synthetic-node",
        }

    def _post(self, payload):
        return self.client.post(
            reverse("document-node-callback"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )

    def test_callback_accepts_stores_canonical_and_repeat_is_idempotent(self):
        payload = self._payload()
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.result, json.loads(canonical_json(payload)))
        self.assertEqual(self.job.result_sha256, sha256_json(payload))
        self.assertEqual(
            self.job.result["result"]["final_analysis"]["status"], "completed"
        )
        first_completed_at = self.job.completed_at
        first_hash = self.job.result_sha256

        reordered = {
            "verification_status": payload["verification_status"],
            "model": payload["model"],
            "files": payload["files"],
            "result": {
                "final_analysis": payload["result"]["final_analysis"],
                "financial_analysis": payload["result"]["financial_analysis"],
                "schema_version": payload["result"]["schema_version"],
            },
            "status": payload["status"],
            "job_id": payload["job_id"],
        }
        self.assertNotEqual(json.dumps(payload), json.dumps(reordered))
        self.assertEqual(canonical_json(payload), canonical_json(reordered))
        repeat = self._post(reordered)
        self.assertEqual(repeat.status_code, 200)
        self.job.refresh_from_db()
        self.assertEqual(self.job.result_sha256, first_hash)
        self.assertEqual(self.job.result, json.loads(canonical_json(payload)))
        self.assertEqual(self.job.completed_at, first_completed_at)
