from django.urls import reverse
from django.utils import timezone

from analytics.models import AnalysisRun
from core.testing import CreditTestCase

from .models import Application, ApplicationVersion


def demo_result(**overrides):
    result = {
        "schema_version": "1.0",
        "is_demo": True,
        "model_version": "stub-1",
        "policy_version": "demo-1",
        "scenario_id": "normal",
        "recommendation": "PROCEED_TO_REVIEW",
        "summary": "Демонстрационный результат, не кредитное решение",
        "reason_codes": ["DEMO_ONLY", "EVIDENCE_OK"],
        "components": {
            "evidence": {"status": "OK", "summary": "Документы разобраны"},
            "radar": {"status": "OK", "summary": "Демо-показатели в норме"},
            "cashflow": {"status": "OK", "summary": "Денежный поток условно стабилен"},
            "scoring": {"status": "OK", "summary": "Демо-скоринг без реального PD"},
        },
    }
    result.update(overrides)
    return result


class BankAnalysisCardTests(CreditTestCase):
    def _submit(self):
        self.client.force_login(self.business_user)
        response = self.client.post(reverse("business-cabinet"), self.valid_payload())
        self.assertEqual(response.status_code, 302, response.content.decode())
        return Application.objects.get()

    def _current_run(self, application):
        return AnalysisRun.objects.get(
            version__application=application,
            version__version_number=application.current_version,
        )

    def _store_result(self, run, result=None, status=AnalysisRun.Status.READY):
        run.status = status
        run.result = result or demo_result()
        run.completed_at = timezone.now()
        run.save(update_fields=("status", "result", "completed_at"))

    def _fail(self, run):
        run.status = AnalysisRun.Status.FAILED
        run.last_error = "Traceback (most recent call last): secret internal error"
        run.completed_at = timezone.now()
        run.save(update_fields=("status", "last_error", "completed_at"))

    def _bank_detail(self, application, user):
        self.client.force_login(user)
        return self.client.get(reverse("bank-application-detail", args=[application.pk]))

    def test_bank_and_senior_see_demo_recommendation_and_four_components(self):
        application = self._submit()
        self._store_result(self._current_run(application))

        for user in (self.bank_user, self.senior_approver):
            with self.subTest(user=user.email):
                response = self._bank_detail(application, user)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "ДЕМО")
                self.assertContains(response, "Передать на ручное рассмотрение")
                self.assertContains(
                    response, "Демонстрационный результат, не кредитное решение"
                )
                self.assertContains(response, "Версия заявки №1")
                self.assertContains(response, "stub-1")
                self.assertContains(response, "demo-1")
                for title in (
                    "Документы",
                    "Радар показателей",
                    "Денежный поток",
                    "Скоринг",
                ):
                    self.assertContains(response, title)
                self.assertContains(response, "Коды оснований")
                self.assertContains(response, "не заменяет решение банка")

    def test_partial_result_is_labelled_and_shows_incomplete_component(self):
        application = self._submit()
        result = demo_result(scenario_id="missing_data")
        result["components"]["evidence"] = {
            "status": "INCOMPLETE",
            "summary": "Часть документов не распознана",
        }
        self._store_result(
            self._current_run(application), result, AnalysisRun.Status.PARTIAL
        )

        response = self._bank_detail(application, self.bank_user)
        self.assertContains(response, "Частичный результат")
        self.assertContains(response, "Данные неполные")
        self.assertContains(response, "Часть документов не распознана")

    def test_model_strings_are_escaped(self):
        application = self._submit()
        result = demo_result(
            summary="<b>demo summary</b>",
            reason_codes=["<i>reason</i>"],
        )
        result["components"]["radar"]["summary"] = "<script>alert(1)</script>"
        self._store_result(self._current_run(application), result)

        response = self._bank_detail(application, self.bank_user)
        self.assertContains(response, "&lt;b&gt;demo summary&lt;/b&gt;")
        self.assertContains(response, "&lt;i&gt;reason&lt;/i&gt;")
        self.assertNotContains(response, "<b>demo summary</b>")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_business_and_bank_admin_cannot_open_bank_card(self):
        application = self._submit()
        self._store_result(self._current_run(application))

        for user in (self.business_user, self.bank_admin):
            with self.subTest(user=user.email):
                response = self._bank_detail(application, user)
                self.assertEqual(response.status_code, 403)

    def test_business_own_page_has_no_internal_analysis_marker(self):
        application = self._submit()
        self._store_result(self._current_run(application))

        self.client.force_login(self.business_user)
        response = self.client.get(
            reverse("business-application-detail", args=[application.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ДЕМО")
        self.assertNotContains(response, "Передать на ручное рассмотрение")
        self.assertNotContains(response, "Коды оснований")

    def test_only_current_version_result_is_displayed(self):
        application = self._submit()
        self._store_result(self._current_run(application))
        ApplicationVersion.objects.create(
            application=application,
            version_number=2,
            product=self.product,
            amount="1600000.00",
            term_months=12,
            purpose="Повторная заявка с обновлёнными условиями",
            submitted_at=timezone.now(),
            snapshot={"organization_name": "ООО Север", "version": 2},
        )
        Application.objects.filter(pk=application.pk).update(current_version=2)
        application.refresh_from_db()

        response = self._bank_detail(application, self.bank_user)
        self.assertContains(
            response, "Для этой версии заявки аналитика недоступна"
        )
        self.assertNotContains(
            response, "Демонстрационный результат, не кредитное решение"
        )
        self.assertNotContains(response, "Передать на ручное рассмотрение")

    def test_waiting_run_has_running_state_without_result(self):
        application = self._submit()
        run = self._current_run(application)
        self.assertEqual(run.status, AnalysisRun.Status.WAITING)

        response = self._bank_detail(application, self.bank_user)
        self.assertContains(response, "Ожидает обработки документов")
        self.assertContains(response, "ожидает извлечения документов")
        self.assertNotContains(response, "Передать на ручное рассмотрение")

    def test_failed_run_shows_generic_explanation_without_raw_error(self):
        application = self._submit()
        self._fail(self._current_run(application))

        response = self._bank_detail(application, self.bank_user)
        self.assertContains(response, "Ошибка")
        self.assertContains(response, "Аналитика завершилась с ошибкой")
        self.assertNotContains(response, "Traceback")
        self.assertNotContains(response, "secret internal error")

    def test_no_analysis_state_keeps_manual_actions_available(self):
        application = self._submit()
        AnalysisRun.objects.filter(
            version__application=application,
            version__version_number=application.current_version,
        ).delete()

        response = self._bank_detail(application, self.bank_user)
        self.assertContains(
            response, "Для этой версии заявки аналитика недоступна"
        )
        self.assertContains(response, "Взять в работу")
        self.assertContains(
            response, "Ручные действия по заявке доступны независимо"
        )

    def test_manual_take_and_approval_still_work_with_failed_analysis(self):
        application = self._submit()
        self._fail(self._current_run(application))
        detail_url = reverse("bank-application-detail", args=[application.pk])

        self.client.force_login(self.bank_user)
        taken = self.client.post(
            detail_url, {"form_type": "action", "action": "take", "note": ""}
        )
        self.assertRedirects(taken, detail_url)
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.IN_REVIEW)

        sent = self.client.post(
            detail_url,
            {
                "form_type": "action",
                "action": "send_to_approval",
                "note": "",
            },
        )
        self.assertRedirects(sent, detail_url)
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.PENDING_APPROVAL)

        self.client.force_login(self.senior_approver)
        approved = self.client.post(
            detail_url,
            {"form_type": "action", "action": "approve", "note": ""},
        )
        self.assertRedirects(approved, detail_url)
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.APPROVED)

        page = self.client.get(detail_url)
        self.assertContains(page, "Аналитика завершилась с ошибкой")
