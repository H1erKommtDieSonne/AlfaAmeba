from io import StringIO

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core.testing import CreditTestCase
from node_integration.demo import contract_result, working_capital_result
from node_integration.models import NodeJob

from .models import Application, ApplicationComment


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


def complete_node_job(application, payload):
    job = NodeJob.objects.get(version__application=application)
    job.status = NodeJob.Status.COMPLETED
    job.external_job_id = application.pk.hex
    job.result = payload
    job.completed_at = timezone.now()
    job.save()


class QueueTests(CreditTestCase):
    def test_whole_row_opens_card_and_keeps_filter(self):
        application = submit(self)
        self.client.force_login(self.bank_user)

        response = self.client.get(reverse("bank-queue"), {"status": "SUBMITTED"})

        url = reverse("bank-application-detail", args=[application.pk]) + "?status=SUBMITTED"
        self.assertContains(response, f'data-href="{url}"')
        self.assertContains(response, 'class="is-clickable"')

    def test_card_pages_through_queue_in_same_order(self):
        first = submit(self)
        second = submit(self)
        self.client.force_login(self.bank_user)

        # Очередь — от свежих к старым: вторая заявка первая.
        response = self.client.get(reverse("bank-application-detail", args=[second.pk]), {"status": ""})
        self.assertContains(response, "1 из 2")
        self.assertContains(response, reverse("bank-application-detail", args=[first.pk]) + "?status=")
        self.assertContains(response, f'href="{reverse("bank-queue")}?status="')

        response = self.client.get(reverse("bank-application-detail", args=[first.pk]), {"status": ""})
        self.assertContains(response, "2 из 2")
        self.assertContains(response, 'aria-disabled="true">Следующая')

    def test_card_outside_filter_has_no_paging(self):
        application = submit(self)
        self.client.force_login(self.bank_user)

        response = self.client.get(reverse("bank-application-detail", args=[application.pk]), {"status": "APPROVED"})

        self.assertNotContains(response, "Соседние заявки в очереди")

    def test_action_redirect_keeps_filter(self):
        application = submit(self)
        self.client.force_login(self.bank_user)
        url = reverse("bank-application-detail", args=[application.pk]) + "?status=SUBMITTED"

        response = self.client.post(url, {"form_type": "action", "action": "take", "note": ""})

        self.assertRedirects(response, url, fetch_redirect_response=False)


class EmployeeCardTests(CreditTestCase):
    def setUp(self):
        self.application = submit(self)

    def detail(self):
        self.client.force_login(self.bank_user)
        return self.client.get(reverse("bank-application-detail", args=[self.application.pk]))

    def test_without_node_result_shows_placeholder_and_no_charts(self):
        response = self.detail()

        self.assertContains(response, "Что предоставил клиент")
        self.assertContains(response, "Анализа пока нет")
        self.assertNotContains(response, "chart.umd.min.js")
        self.assertNotContains(response, "financial-chart-data")

    def test_ready_result_shows_both_sides_and_charts(self):
        complete_node_job(self.application, working_capital_result(application_id=self.application.pk))

        response = self.detail()

        for text in ("Что выдал анализ", "Ключевые выводы", "Проблемные сигналы", "Текущая ликвидность",
                     "Сценарии на ноя 2026", "Что учла модель", "Демо-данные", "Средний"):
            self.assertContains(response, text)
        self.assertContains(response, "chart.umd.min.js")
        self.assertContains(response, 'id="financial-chart-data"')
        self.assertContains(response, 'data-chart="cashflow"')
        # Годовые показатели клиента — с источником.
        self.assertContains(response, "otchetnost_2025.pdf · страница 2, строка 2110")
        # Технические панели коллег никуда не делись, просто свёрнуты.
        self.assertContains(response, "Технические детали")
        self.assertContains(response, "Внешний узел")

    def test_feedback_button_needs_application_in_review(self):
        complete_node_job(self.application, contract_result(application_id=self.application.pk))

        response = self.detail()
        self.assertContains(response, "Что нужно от клиента")
        self.assertContains(response, "сначала возьмите заявку в работу")
        self.assertNotContains(response, "Отправить фидбек клиенту")

        take(self, self.application)
        response = self.detail()
        self.assertContains(response, "Отправить фидбек клиенту")
        self.assertContains(response, "1. Читаемая версия контракта.")

    def test_feedback_returns_application_with_client_message(self):
        complete_node_job(self.application, contract_result(application_id=self.application.pk))
        take(self, self.application)

        self.client.post(
            reverse("bank-application-detail", args=[self.application.pk]),
            {"form_type": "action", "action": "request_revision", "note": "Пришлите приложение № 1 к контракту."},
        )

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.NEEDS_REVISION)
        comment = ApplicationComment.objects.get(application=self.application)
        self.assertEqual(comment.audience, ApplicationComment.Audience.CLIENT)
        self.assertEqual(comment.text, "Пришлите приложение № 1 к контракту.")

    def test_node_issues_are_listed_with_file_and_page(self):
        complete_node_job(self.application, contract_result(application_id=self.application.pk))

        response = self.detail()

        self.assertContains(response, "Нечитаемая страница в PDF")
        self.assertContains(response, "<span>kontrakt_vektor.pdf</span><span>страница 3</span>", html=False)

    def test_processing_error_does_not_ask_client_for_documents(self):
        complete_node_job(self.application, {"status": "completed", "result": {"status": "processing_error", "financial_analysis": None}})

        response = self.detail()

        self.assertContains(response, "технической ошибке обработки")
        self.assertNotContains(response, "Отправить фидбек клиенту")


class SeedDemoTests(CreditTestCase):
    def test_seed_creates_demo_applications_once(self):
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())

        demo = Application.objects.filter(versions__node_jobs__status=NodeJob.Status.COMPLETED).distinct()
        self.assertEqual(demo.count(), 2)
        self.assertTrue(all(item.status == Application.Status.IN_REVIEW for item in demo))
