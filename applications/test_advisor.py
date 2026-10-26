from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.testing import CreditTestCase
from products.models import Product

from .forms import ApplicationForm
from .models import Application


def attachment(name="document.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 demo", content_type="application/pdf")


class AdvisorTests(CreditTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for code in ["investment", "refinancing", "contract-finance"]:
            Product.objects.create(code=code, name=code, description="Demo",
                                   min_amount=100000, max_amount=30000000,
                                   min_term_months=3, max_term_months=60)

    def setUp(self):
        self.client.force_login(self.business_user)

    def advice(self, **data):
        return self.client.get(reverse("financing-advice"), data).json()

    def test_gap_routes_and_unavailable_products(self):
        for kind, expected in [("short", "overdraft"), ("receivable", "factoring"), ("recurring", "credit-line")]:
            result = self.advice(need="gap", gap_kind=kind)
            self.assertEqual(result["items"][0]["code"], expected)
            self.assertFalse(result["items"][0]["available"])
        self.assertEqual(self.advice(need="gap", gap_kind="unknown")["items"], [])

    def test_debt_is_not_automatically_refinancing(self):
        for kind in ["suppliers", "overdue", ""]:
            self.assertEqual(self.advice(need="debt", debt_kind=kind)["items"], [])
        self.assertEqual(self.advice(need="debt", debt_kind="loans")["items"][0]["code"], "refinancing")

    def test_limits_inactive_catalog_and_access(self):
        self.assertFalse(self.advice(need="growth", amount="1")["items"][0]["available"])
        Product.objects.filter(code="investment").update(active=False)
        self.assertFalse(self.advice(need="growth")["items"][0]["available"])
        self.assertEqual(self.client.get(reverse("financing-advice"), {"amount": "NaN"}).status_code, 400)
        self.client.force_login(self.bank_user)
        self.assertEqual(self.client.get(reverse("financing-advice")).status_code, 403)

    def test_checklist_depends_on_product_and_collateral(self):
        result = self.advice(need="growth", product="investment", collateral="on")
        self.assertEqual([item["code"] for item in result["checklist"]], ["statement", "estimate", "collateral"])

    def test_server_rejects_missing_required_documents_and_wrong_product(self):
        payload = self.valid_payload()
        payload.update(need="growth", product="investment")
        response = self.client.post(reverse("business-cabinet"), payload)
        self.assertContains(response, "Прикрепите документ для выбранного продукта")
        self.assertFalse(Application.objects.exists())
        payload = self.valid_payload()
        payload.update(need="debt", debt_kind="overdue")
        response = self.client.post(reverse("business-cabinet"), payload)
        self.assertContains(response, "Выберите доступный вариант")
        self.assertFalse(Application.objects.exists())

    def test_investment_submission_saves_rules_and_typed_documents(self):
        payload = self.valid_payload()
        payload.update(need="equipment", product="investment", document_estimate=[attachment()])
        response = self.client.post(reverse("business-cabinet"), payload)
        self.assertEqual(response.status_code, 302)
        version = Application.objects.get().versions.get()
        self.assertEqual(version.snapshot["needs"]["need"], "equipment")
        self.assertEqual(set(version.documents.values_list("requirement_code", flat=True)), {"statement", "estimate"})
        self.assertEqual(version.snapshot["recommendation"]["rules_version"], "needs-2")
        self.assertContains(self.client.get(reverse("business-application-detail", args=[version.application_id])), "Смета или предложение поставщика")

    def test_revision_reuses_documents_and_preserves_previous_version(self):
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()
        old = application.versions.get()
        application.status = Application.Status.NEEDS_REVISION
        application.save()
        payload = self.valid_payload()
        payload.pop("document_statement")
        payload.update(need="growth", product="investment", document_estimate=[attachment()])
        response = self.client.post(reverse("business-application-detail", args=[application.pk]), payload)
        self.assertEqual(response.status_code, 302)
        current = application.versions.get(version_number=2)
        self.assertEqual(current.documents.get(requirement_code="statement").file.name, old.documents.get().file.name)
        self.assertEqual(old.documents.count(), 1)
        self.assertEqual(current.documents.count(), 2)
        self.assertEqual(old.snapshot["needs"]["need"], "working")

    def test_required_typed_upload_format_and_size_are_validated(self):
        payload = self.valid_payload()
        payload.pop("document_statement")
        form = ApplicationForm(payload, {"document_statement": [attachment("script.exe")]})
        self.assertFalse(form.is_valid())
        self.assertIn("document_statement", form.errors)

        oversized = attachment("statement.pdf")
        oversized.size = ApplicationForm.MAX_DOCUMENT_SIZE + 1
        form = ApplicationForm(payload, {"document_statement": [oversized]})
        self.assertFalse(form.is_valid())
        self.assertIn("document_statement", form.errors)

    def test_generic_attachment_does_not_satisfy_required_slot(self):
        payload = self.valid_payload()
        payload["documents"] = payload.pop("document_statement")
        response = self.client.post(reverse("business-cabinet"), payload)
        self.assertContains(response, "Прикрепите документ для выбранного продукта")
        self.assertFalse(Application.objects.exists())

    def test_collateral_requires_its_own_attachment(self):
        payload = self.valid_payload()
        payload["collateral"] = "on"
        response = self.client.post(reverse("business-cabinet"), payload)
        self.assertIn("document_collateral", response.context["form"].errors)

    def test_total_and_count_guards_on_actual_generic_uploads(self):
        payload = self.valid_payload()
        payload.pop("document_statement")
        files = []
        for index in range(6):
            file = attachment(f"generic-{index}.pdf")
            file.size = 9 * 1024 * 1024
            files.append(file)
        form = ApplicationForm(payload, {"documents": files})
        self.assertFalse(form.is_valid())
        self.assertIn("не более 5", " ".join(form.non_field_errors()))
        self.assertIn("Общий размер документов превышает 50 МБ", form.non_field_errors())

    def test_revision_replacement_keeps_old_file(self):
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()
        old = application.versions.get().documents.get()
        application.status = Application.Status.NEEDS_REVISION
        application.save()
        response = self.client.post(reverse("business-application-detail", args=[application.pk]), self.valid_payload())
        self.assertEqual(response.status_code, 302)
        new = application.versions.get(version_number=2).documents.get()
        self.assertNotEqual(old.file.name, new.file.name)
        self.assertTrue(old.file.storage.exists(old.file.name))
