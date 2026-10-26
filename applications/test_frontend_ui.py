from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.testing import CreditTestCase

from .models import Application


class FrontendTemplateTests(CreditTestCase):
    def test_create_page_has_reactive_review_and_document_contract(self):
        self.client.force_login(self.business_user)

        response = self.client.get(reverse("business-cabinet"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-max-files="5"')
        self.assertContains(response, 'data-max-file-size="10485760"')
        self.assertContains(response, 'data-max-total-size="52428800"')
        self.assertContains(response, 'data-accept=".pdf,.png,.jpg,.jpeg,.xlsx,.csv,.docx"')
        self.assertContains(response, "x-text=\"review.organization_name\"")
        self.assertContains(response, "x-text=\"review.inn\"")
        self.assertContains(response, "x-text=\"review.product\"")
        self.assertContains(response, "x-text=\"review.amount\"")
        self.assertContains(response, "x-text=\"review.term\"")
        self.assertContains(response, "x-text=\"review.purpose\"")
        self.assertContains(response, "x-text=\"review.filesSummary\"")
        self.assertNotContains(response, "fieldValue(")
        self.assertNotContains(response, "documentSummary()")

    def test_list_success_and_revision_show_id_and_version_separately(self):
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload["documents"] = [
            SimpleUploadedFile("annual-report.pdf", b"%PDF-1.4 annual", content_type="application/pdf")
        ]
        self.client.post(reverse("business-cabinet"), payload)
        application = Application.objects.get()
        version = application.versions.get()
        short_id = str(application.pk)[:8].upper()

        success = self.client.get(reverse("application-success", args=[application.pk]))
        self.assertEqual(success.status_code, 200)
        self.assertContains(success, "ID заявки")
        self.assertContains(success, short_id)
        self.assertContains(success, str(application.pk))
        self.assertContains(success, "Версия")
        self.assertNotContains(success, "01 / Отправлено")

        listing = self.client.get(reverse("business-applications"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "ID заявки")
        self.assertContains(listing, short_id)
        self.assertContains(listing, str(application.pk))
        self.assertContains(listing, "Версия 1")
        self.assertNotContains(listing, "№1</td>")

        application.status = Application.Status.NEEDS_REVISION
        application.save(update_fields=("status", "updated_at"))
        detail = self.client.get(reverse("business-application-detail", args=[application.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "data-readonly-identity=\"true\"")
        self.assertContains(detail, 'data-max-files="5"')
        self.assertContains(detail, "x-effect=\"syncProductOptions()\"")
        self.assertContains(detail, short_id)
        self.assertContains(detail, str(application.pk))
        self.assertContains(detail, "Версия 1")
        self.assertContains(detail, 'name="inn"')
        self.assertContains(detail, 'name="organization_name"')
        self.assertNotContains(detail, 'name="inn" disabled')
        self.assertNotContains(detail, 'name="organization_name" disabled')
        self.assertContains(detail, "Название и ИНН зафиксированы")
        self.assertContains(detail, 'name="remove_document_ids"')
        self.assertContains(detail, "Не переносить в новую версию")
        for document in version.documents.all():
            self.assertContains(detail, f'value="{document.pk}"')
            self.assertContains(detail, f'data-document-id="{document.pk}"')
        self.assertEqual(detail.content.decode().count('name="remove_document_ids"'), 2)

        revision = self.valid_payload()
        revision.pop("document_statement")
        revision["remove_document_ids"] = "00000000-0000-0000-0000-000000000000"
        invalid_remove = self.client.post(
            reverse("business-application-detail", args=[application.pk]), revision
        )
        self.assertEqual(invalid_remove.status_code, 200)
        self.assertContains(invalid_remove, "remove-document-ids-error")
        self.assertContains(invalid_remove, "Форма не отправлена")

        invalid_any = self.valid_payload()
        invalid_any.pop("document_statement")
        invalid_any["amount"] = "1"
        invalid_amount = self.client.post(
            reverse("business-application-detail", args=[application.pk]), invalid_any
        )
        self.assertEqual(invalid_amount.status_code, 200)
        self.assertContains(invalid_amount, "Форма не отправлена")
