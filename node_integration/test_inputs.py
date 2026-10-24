import json
import threading
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from applications.models import Application
from core.testing import CreditTestCase

from .demo import contract_result
from .forms import FinancialInputsForm, annuity_payment, statement_formset
from .models import FinancialInputs, NodeFeedback, NodeJob
from .outbound import build_application_context, fetch_provider_reports
from .services import NodeJobBusy, request_recalculation


def submit(test_case):
    test_case.client.force_login(test_case.business_user)
    test_case.client.post(reverse("business-cabinet"), test_case.valid_payload())
    return Application.objects.order_by("-created_at").first()


def take(test_case, application):
    test_case.client.force_login(test_case.bank_user)
    test_case.client.post(
        reverse("bank-application-detail", args=[application.pk]),
        {"form_type": "action", "action": "take", "note": ""},
    )


def complete(job, payload):
    job.status = NodeJob.Status.COMPLETED
    job.external_job_id = job.pk.hex
    job.result = payload
    job.completed_at = timezone.now()
    job.save()
    return job


def inputs_post(**overrides):
    data = {
        "form_type": "node_inputs",
        "annual_rate_percent": "18",
        "proposed_monthly_payment_rub": "",
        "accounts": "40702810900000012345\n40702810900000012345\n40702810100000099999",
        "debt_status": "COMPLETE",
        "debt_schedule_start": "2026-10-01",
        "debt_schedule_end": "2026-11-30",
        "reporting_year": "2025",
        "statements-TOTAL_FORMS": "1",
        "statements-INITIAL_FORMS": "0",
        "statements-MIN_NUM_FORMS": "0",
        "statements-MAX_NUM_FORMS": "5",
        "statements-0-filename": "account-statement.pdf",
        "statements-0-account_id": "40702810900000012345",
        "statements-0-period_start": "2026-04-01",
        "statements-0-period_end": "2026-09-30",
        "statements-0-opening_balance_rub": "6000000",
        "statements-0-closing_balance_rub": "11200000",
        "statements-0-complete": "on",
    }
    data.update(overrides)
    return data


class InputsFormTests(CreditTestCase):
    def setUp(self):
        self.application = submit(self)
        self.version = self.application.versions.get()

    def test_payment_defaults_to_annuity_and_accounts_are_deduplicated(self):
        form = FinancialInputsForm(inputs_post(), version=self.version)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["proposed_monthly_payment_rub"], annuity_payment(Decimal("1500000"), 12, Decimal("18")))
        self.assertEqual(form.cleaned_data["accounts"], ["40702810900000012345", "40702810100000099999"])

    def test_annuity_with_zero_rate_is_even_principal(self):
        self.assertEqual(annuity_payment(Decimal("1200000"), 12, Decimal("0")), Decimal("100000.00"))

    def test_debt_schedule_needs_dates_in_order(self):
        form = FinancialInputsForm(inputs_post(debt_schedule_end=""), version=self.version)
        self.assertIn("debt_schedule_end", form.errors)
        form = FinancialInputsForm(inputs_post(debt_schedule_end="2026-09-01"), version=self.version)
        self.assertIn("debt_schedule_end", form.errors)

    def test_dates_dropped_when_debt_not_confirmed_complete(self):
        form = FinancialInputsForm(inputs_post(debt_status="NONE"), version=self.version)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["debt_schedule_start"])

    def test_rejects_bad_rate_year_and_account(self):
        form = FinancialInputsForm(inputs_post(annual_rate_percent="150", reporting_year="1990",
                                               accounts="счёт с пробелом"), version=self.version)
        self.assertEqual(set(form.errors), {"annual_rate_percent", "reporting_year", "accounts"})

    def test_statement_rows_need_all_fields_and_known_account(self):
        data = inputs_post(**{"statements-0-closing_balance_rub": "", "statements-0-account_id": "40702810000000000000"})
        statements = statement_formset(data, version=self.version, accounts=["40702810900000012345"])

        self.assertFalse(statements.is_valid())
        self.assertIn("closing_balance_rub", statements.forms[0].errors)

        data = inputs_post(**{"statements-0-account_id": "40702810000000000000"})
        statements = statement_formset(data, version=self.version, accounts=["40702810900000012345"])
        self.assertFalse(statements.is_valid())
        self.assertIn("account_id", statements.forms[0].errors)

    def test_empty_statement_row_is_ignored(self):
        statements = statement_formset(inputs_post(**{"statements-0-filename": ""}), version=self.version, accounts=[])

        self.assertTrue(statements.is_valid(), statements.errors)
        self.assertEqual(statements.periods(), [])

    def test_unbound_formset_offers_row_per_document(self):
        statements = statement_formset(version=self.version)
        self.assertEqual([form.initial.get("filename") for form in statements.forms], ["account-statement.pdf"])


class ContextWithInputsTests(CreditTestCase):
    def setUp(self):
        self.application = submit(self)
        self.version = self.application.versions.get()

    def test_confirmed_inputs_go_to_context_by_contract_names(self):
        inputs = FinancialInputs.objects.create(
            version=self.version, confirmed_by=self.bank_user, annual_rate_percent=Decimal("18.00"),
            proposed_monthly_payment_rub=Decimal("137515.20"), expected_account_ids=["40702810900000012345"],
            statement_periods=[{"filename": "account-statement.pdf", "account_id": "40702810900000012345",
                                "period_start": "2026-04-01", "period_end": "2026-09-30",
                                "opening_balance_rub": 6000000.0, "closing_balance_rub": 11200000.0, "complete": True},
                               {"filename": "битая строка"}],
            debt_status=FinancialInputs.DebtStatus.COMPLETE, debt_schedule_start=date(2026, 10, 1),
            debt_schedule_end=date(2026, 11, 30), reporting_year=2025,
        )

        context = build_application_context(self.version, inputs=inputs, analysis_date=date(2026, 10, 24))

        self.assertEqual(context["borrower_inn"], "7707083893")
        self.assertEqual(context["analysis_date"], "2026-10-24")
        self.assertEqual(context["annual_rate_percent"], 18.0)
        self.assertEqual(context["proposed_monthly_payment_rub"], 137515.2)
        self.assertEqual(context["expected_account_ids"], ["40702810900000012345"])
        self.assertEqual(len(context["statement_periods"]), 1)
        self.assertTrue(context["statement_periods"][0]["complete"])
        self.assertEqual((context["debt_schedule_start"], context["debt_schedule_end"]), ("2026-10-01", "2026-11-30"))
        self.assertTrue(context["debt_schedule_complete"])
        self.assertNotIn("no_existing_debt_confirmed", context)
        self.assertEqual(context["reporting_year"], 2025)

    def test_unconfirmed_debt_sends_nothing_about_debt(self):
        inputs = FinancialInputs.objects.create(version=self.version, confirmed_by=self.bank_user)

        context = build_application_context(self.version, inputs=inputs)

        for key in ("no_existing_debt_confirmed", "debt_schedule_complete", "annual_rate_percent", "expected_account_ids"):
            self.assertNotIn(key, context)


class RecalculationTests(CreditTestCase):
    def setUp(self):
        self.application = submit(self)
        self.version = self.application.versions.get()
        self.job = NodeJob.objects.get(version=self.version)

    def test_pending_attempt_is_reused(self):
        job, created = request_recalculation(self.version, user=self.bank_user)
        self.assertEqual((job, created), (self.job, False))

    def test_attempt_in_flight_blocks_duplicate(self):
        NodeJob.objects.filter(pk=self.job.pk).update(status=NodeJob.Status.WAITING)
        with self.assertRaises(NodeJobBusy):
            request_recalculation(self.version, user=self.bank_user)

    def test_finished_attempt_gets_new_one(self):
        complete(self.job, contract_result(application_id=self.application.pk))

        job, created = request_recalculation(self.version, user=self.bank_user)

        self.assertTrue(created)
        self.assertNotEqual(job.pk, self.job.pk)
        self.assertEqual(job.status, NodeJob.Status.PENDING)
        self.assertEqual(job.requested_by, self.bank_user)
        self.assertEqual(NodeJob.objects.filter(version=self.version).count(), 2)


class _ProviderHandler(BaseHTTPRequestHandler):
    status = 200
    body = b""

    def do_GET(self):
        self.server.paths.append(self.path)
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, *args):
        pass


class ProviderReportTests(CreditTestCase):
    def setUp(self):
        self.application = submit(self)
        self.version = self.application.versions.get()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        self.server.paths, self.server.status = [], 200
        self.server.body = json.dumps({"provider": "mock-bo", "is_mock": True, "metrics": {"revenue": 85000.0}}).encode()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_report_is_fetched_for_company_and_passed_whole(self):
        with override_settings(DOCUMENT_NODE_PROVIDER_URL=self.url, DOCUMENT_NODE_TIMEOUT_SECONDS=2):
            reports = fetch_provider_reports(self.version, today=date(2026, 10, 24))

        self.assertEqual(reports, [{"provider": "mock-bo", "is_mock": True, "metrics": {"revenue": 85000.0}}])
        self.assertEqual(self.server.paths, ["/api/v1/organizations/7707083893/metrics?year=2025"])

    def test_provider_errors_mean_no_report(self):
        self.server.status = 500
        with override_settings(DOCUMENT_NODE_PROVIDER_URL=self.url, DOCUMENT_NODE_TIMEOUT_SECONDS=2):
            self.assertEqual(fetch_provider_reports(self.version), [])
        with override_settings(DOCUMENT_NODE_PROVIDER_URL=""):
            self.assertEqual(fetch_provider_reports(self.version), [])
        with override_settings(DOCUMENT_NODE_PROVIDER_URL="http://127.0.0.1:1", DOCUMENT_NODE_TIMEOUT_SECONDS=1):
            self.assertEqual(fetch_provider_reports(self.version), [])


class CardInputsTests(CreditTestCase):
    def setUp(self):
        self.application = submit(self)
        self.version = self.application.versions.get()
        self.first_job = complete(NodeJob.objects.get(version=self.version), contract_result(application_id=self.application.pk))
        self.url = reverse("bank-application-detail", args=[self.application.pk])

    def test_reviewer_confirms_inputs_and_requests_recalculation(self):
        take(self, self.application)

        response = self.client.post(self.url, inputs_post())

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        inputs = FinancialInputs.objects.get(version=self.version)
        self.assertEqual(inputs.confirmed_by, self.bank_user)
        self.assertEqual(len(inputs.statement_periods), 1)
        latest = NodeJob.objects.filter(version=self.version).order_by("-created_at").first()
        self.assertEqual(latest.status, NodeJob.Status.PENDING)
        self.assertNotEqual(latest.pk, self.first_job.pk)

        page = self.client.get(self.url)
        self.assertContains(page, "Пересчёт ждёт отправки узлу")
        # Пока узел считает, видно прошлый результат, а не пустую карточку.
        self.assertContains(page, "Что выдал анализ")
        self.assertContains(page, "Нечитаемая страница в PDF")
        self.assertContains(page, "Подтверждено")

    def test_old_attempt_feedback_goes_to_history(self):
        take(self, self.application)
        NodeFeedback.objects.create(job=self.first_job, issues=[{"code": "old", "message": "Старое замечание"}])
        self.client.post(self.url, inputs_post())
        new_job = NodeJob.objects.filter(version=self.version).order_by("-created_at").first()
        complete(new_job, contract_result(application_id=self.application.pk))

        page = self.client.get(self.url)

        self.assertNotContains(page, "Пересчёт ждёт отправки узлу")
        self.assertContains(page, "Замечания к предыдущим версиям")

    def test_only_reviewer_with_application_in_work_can_confirm(self):
        self.client.force_login(self.bank_user)
        self.assertEqual(self.client.post(self.url, inputs_post()).status_code, 403)  # ещё SUBMITTED
        take(self, self.application)
        self.client.force_login(self.senior_approver)
        self.assertEqual(self.client.post(self.url, inputs_post()).status_code, 403)
        self.assertFalse(FinancialInputs.objects.exists())

    def test_invalid_form_is_shown_with_errors(self):
        take(self, self.application)

        response = self.client.post(self.url, inputs_post(debt_schedule_end=""))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Укажите, за какой период приложены графики всех долгов")
        self.assertFalse(FinancialInputs.objects.exists())


class DemoCompleteCommandTests(CreditTestCase):
    def test_completes_pending_jobs_only_in_debug(self):
        application = submit(self)
        job = NodeJob.objects.get(version__application=application)

        with self.assertRaises(CommandError):
            call_command("node_demo_complete", stdout=StringIO())
        with override_settings(DEBUG=True):
            call_command("node_demo_complete", stdout=StringIO())

        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.COMPLETED)
        self.assertEqual(job.result["result"]["financial_analysis"]["schema_version"], 2)
