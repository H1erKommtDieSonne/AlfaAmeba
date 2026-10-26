from django.urls import reverse

from core.testing import CreditTestCase

from .models import Application, ApplicationComment, ApplicationEvent


class RevisionDesignTests(CreditTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        self.application = Application.objects.get()
        self.application.status = Application.Status.NEEDS_REVISION
        self.application.save(update_fields=("status", "updated_at"))
        self.url = reverse("business-application-detail", args=[self.application.pk])

    def request_revision(self, message):
        return ApplicationEvent.objects.create(
            application=self.application,
            actor=self.bank_user,
            event_type=ApplicationEvent.Type.STATUS_CHANGED,
            to_status=Application.Status.NEEDS_REVISION,
            message=message,
            metadata={"action": "request_revision"},
        )

    def test_revision_has_compact_product_select_without_banner(self):
        self.request_revision("Старое замечание")
        latest = self.request_revision("Уточните цель\n<script>alert(1)</script>")
        ApplicationComment.objects.create(
            application=self.application, author=self.bank_user,
            audience=ApplicationComment.Audience.INTERNAL,
            text="Секретный внутренний комментарий",
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["revision_request"], latest)
        self.assertNotContains(response, 'id="revision-brief-title"')
        self.assertContains(response, '<select name="product" id="id_product" class="form-control" required>')
        self.assertNotContains(response, 'type="radio" name="product"')
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertNotContains(response, "Секретный внутренний комментарий")
        self.assertContains(response, 'id="revision-credit-title"')
        self.assertContains(response, 'id="revision-documents-title"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="remove_document_ids"')

    def test_no_reason_has_clear_fallback_and_no_approval_claim(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Сообщения банка")
        self.assertNotContains(response, 'id="revision-brief-title"')
        self.assertContains(response, "Предыдущая версия и документы сохранятся.")

    def test_request_is_not_shown_after_resubmission_status(self):
        self.request_revision("Нужно обновить выписку")
        self.application.status = Application.Status.SUBMITTED
        self.application.save(update_fields=("status", "updated_at"))
        response = self.client.get(self.url)
        self.assertIsNone(response.context["revision_request"])
        self.assertNotContains(response, 'id="revision-brief-title"')
        self.assertNotContains(response, 'id="revision-form"')

    def test_invalid_submission_keeps_reason_and_explains_file_reselection(self):
        self.request_revision("Уточните сумму и приложите выписку")
        payload = self.valid_payload()
        payload["amount"] = "0"
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Версия пока не отправлена")
        self.assertContains(response, "Если выбирали новые файлы, приложите их повторно.")
        self.assertContains(response, 'name="product"')
        self.assertEqual(self.application.versions.count(), 1)
