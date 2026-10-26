from django.urls import reverse
from django.utils import timezone

from core.testing import CreditTestCase, make_user

from .models import Application, ApplicationVersion


def submit_application(test_case):
    test_case.client.force_login(test_case.business_user)
    response = test_case.client.post(reverse("business-cabinet"), test_case.valid_payload())
    test_case.assertEqual(response.status_code, 302, response.content.decode())
    return Application.objects.get()


def current_version(application):
    return application.versions.get(version_number=application.current_version)


def bank_detail(test_case, application, user):
    test_case.client.force_login(user)
    return test_case.client.get(
        reverse("bank-application-detail", args=[application.pk])
    )


class BankNodeProcessingTests(CreditTestCase):
    def setUp(self):
        self.application = submit_application(self)
        self.version = current_version(self.application)

    def _job_model(self):
        from node_integration.models import NodeJob

        return NodeJob

    def test_bank_and_senior_see_panel_but_business_and_admin_do_not(self):
        for user in (self.bank_user, self.senior_approver):
            with self.subTest(user=user.email):
                response = bank_detail(self, self.application, user)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Внешний узел")
                self.assertContains(response, "не решение банка")

        for user in (self.business_user, self.bank_admin):
            with self.subTest(user=user.email):
                response = bank_detail(self, self.application, user)
                self.assertEqual(response.status_code, 403)

    def test_legacy_version_without_node_job_shows_absence_message(self):
        NodeJob = self._job_model()
        NodeJob.objects.filter(version=self.version).delete()

        response = bank_detail(self, self.application, self.bank_user)
        self.assertContains(response, "задание внешнего узла не создано")
        self.assertContains(response, "до подключения интеграции")
        self.assertNotContains(response, "Ответ внешнего узла (JSON)")

    def test_statuses_are_distinct_and_uncertain_has_no_resubmission(self):
        NodeJob = self._job_model()
        cases = [
            ("PENDING", "поставлено в очередь на отправку"),
            ("SENDING", "Задание отправляется во внешний узел"),
            ("WAITING", "Ожидается callback от внешнего узла"),
            ("UNCERTAIN", "Неизвестно, принял ли внешний узел задание"),
        ]
        for status, text in cases:
            with self.subTest(status=status):
                NodeJob.objects.update_or_create(
                    version=self.version, defaults={"status": status}
                )
                response = bank_detail(self, self.application, self.bank_user)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, text)
                self.assertContains(response, "Обновить статус")
                self.assertContains(response, "страница не обновляется автоматически")

        self.assertContains(
            bank_detail(self, self.application, self.bank_user),
            "Автоматическая повторная отправка не выполняется, чтобы не создать дубль",
        )

    def test_failed_status_shows_safe_error_and_no_auto_retry(self):
        NodeJob = self._job_model()
        NodeJob.objects.update_or_create(
            version=self.version,
            defaults={
                "status": "FAILED",
                "last_error": "<script>alert('node')</script>",
            },
        )
        response = bank_detail(self, self.application, self.bank_user)
        self.assertContains(response, "Ошибка внешнего узла")
        self.assertContains(response, "&lt;script&gt;alert(")
        self.assertNotContains(response, "<script>alert('node')</script>")
        self.assertContains(response, "Автоматическая повторная отправка не выполняется")

    def test_completed_json_is_indented_escaped_and_not_in_business_view(self):
        NodeJob = self._job_model()
        NodeJob.objects.update_or_create(
            version=self.version,
            defaults={
                "status": "COMPLETED",
                "result": {"score": 42, "note": "<script>alert(1)</script>"},
                "completed_at": timezone.now(),
            },
        )

        response = bank_detail(self, self.application, self.bank_user)
        self.assertContains(response, "Ответ внешнего узла (JSON)")
        self.assertContains(response, "&quot;score&quot;: 42")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")

        self.client.force_login(self.business_user)
        business_response = self.client.get(
            reverse("business-application-detail", args=[self.application.pk])
        )
        self.assertEqual(business_response.status_code, 200)
        self.assertNotContains(business_response, "Внешний узел")
        self.assertNotContains(business_response, "Ответ внешнего узла")
        self.assertNotContains(business_response, "&quot;score&quot;: 42")

    def test_only_current_version_node_result_is_shown(self):
        NodeJob = self._job_model()
        NodeJob.objects.update_or_create(
            version=self.version,
            defaults={
                "status": "COMPLETED",
                "result": {"marker": "stale-version-payload"},
                "completed_at": timezone.now(),
            },
        )
        second_version = ApplicationVersion.objects.create(
            application=self.application,
            version_number=2,
            product=self.product,
            amount="1600000.00",
            term_months=12,
            purpose="Повторная заявка с обновлёнными условиями",
            submitted_at=timezone.now(),
            snapshot={"organization_name": "ООО Север", "version": 2},
        )
        NodeJob.objects.create(
            version=second_version,
            status="COMPLETED",
            result={"marker": "fresh-version-payload"},
            completed_at=timezone.now(),
        )
        Application.objects.filter(pk=self.application.pk).update(current_version=2)
        self.application.refresh_from_db()

        response = bank_detail(self, self.application, self.bank_user)
        self.assertContains(response, "fresh-version-payload")
        self.assertNotContains(response, "stale-version-payload")

    def test_manual_status_actions_work_before_callback(self):
        NodeJob = self._job_model()
        NodeJob.objects.update_or_create(
            version=self.version, defaults={"status": "PENDING"}
        )
        detail_url = reverse("bank-application-detail", args=[self.application.pk])

        self.client.force_login(self.bank_user)
        response = self.client.post(
            detail_url, {"form_type": "action", "action": "take", "note": ""}
        )
        self.assertRedirects(response, detail_url)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.IN_REVIEW)

        response = self.client.get(detail_url)
        self.assertContains(response, "поставлено в очередь на отправку")
        self.assertNotContains(response, "Ответ внешнего узла (JSON)")


class BankDocumentDownloadAccessTests(CreditTestCase):
    def setUp(self):
        self.application = submit_application(self)
        self.version = current_version(self.application)
        self.document = self.version.documents.get()
        self.download_url = reverse("document-download", args=[self.document.pk])

    def test_bank_card_links_to_original_downloads(self):
        self.client.force_login(self.bank_user)
        response = self.client.get(
            reverse("bank-application-detail", args=[self.application.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Скачать оригинал")
        self.assertContains(response, self.download_url)

    def test_download_is_scoped_to_application_business_access(self):
        self.client.force_login(self.bank_user)
        allowed = self.client.get(self.download_url)
        self.assertEqual(allowed.status_code, 200)
        b"".join(allowed.streaming_content)
        allowed.close()

        self.client.force_login(self.business_user)
        allowed = self.client.get(self.download_url)
        self.assertEqual(allowed.status_code, 200)
        allowed.close()

        self.client.force_login(make_user("unrelated@example.test"))
        self.assertEqual(self.client.get(self.download_url).status_code, 403)

        self.client.force_login(self.bank_admin)
        self.assertEqual(self.client.get(self.download_url).status_code, 403)

        self.client.logout()
        anonymous = self.client.get(self.download_url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("login"), anonymous.url)
