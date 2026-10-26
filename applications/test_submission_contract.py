from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.testing import CreditTestCase, make_user
from documents.models import ApplicationDocument
from organizations.models import (
    Organization,
    OrganizationAccessRequest,
    OrganizationMembership,
)
from products.advisor import recommend
from products.models import Product

from .forms import ApplicationForm
from .models import Application, ApplicationVersion


def attachment(name="document.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 demo", content_type="application/pdf")


def stored_document(version, name="legacy.xls", size=20 * 1024 * 1024, requirement_code=""):
    return ApplicationDocument.objects.create(
        version=version,
        file=SimpleUploadedFile(name, b"legacy"),
        original_name=name,
        requirement_code=requirement_code,
        content_type="application/octet-stream",
        size=size,
        sha256="0" * 64,
    )


class OrganizationIdentityTests(CreditTestCase):
    def test_existing_member_entered_name_is_used_without_renaming_organization(self):
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload["organization_name"] = "ООО Другое название"

        response = self.client.post(reverse("business-cabinet"), payload)

        self.assertEqual(response.status_code, 302, response.content.decode())
        application = Application.objects.get()
        self.assertEqual(application.organization_name, "ООО Другое название")
        self.assertEqual(
            application.versions.get().snapshot["organization_name"],
            "ООО Другое название",
        )
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.name, "ООО Север")

    def test_snapshot_uses_entered_identity_not_canonical_name(self):
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload["organization_name"] = "  ООО   Север  "

        response = self.client.post(reverse("business-cabinet"), payload)

        self.assertEqual(response.status_code, 302, response.content.decode())
        application = Application.objects.get()
        version = application.versions.get()
        self.assertEqual(application.organization_name, "ООО   Север")
        self.assertEqual(application.inn, self.organization.inn)
        self.assertEqual(version.snapshot["organization_name"], "ООО   Север")
        self.assertEqual(version.snapshot["inn"], self.organization.inn)

    def test_revision_rejects_name_and_inn_changes_before_new_version(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()
        application.status = Application.Status.NEEDS_REVISION
        application.save(update_fields=("status", "updated_at"))
        payload = self.valid_payload()
        payload["organization_name"] = "ООО Другое название"
        payload["inn"] = "7801234567"

        response = self.client.post(
            reverse("business-application-detail", args=[application.pk]), payload
        )

        self.assertEqual(response.status_code, 200)
        errors = response.context["revision_form"].errors
        self.assertIn("organization_name", errors)
        self.assertIn("inn", errors)
        application.refresh_from_db()
        self.assertEqual(application.current_version, 1)
        self.assertEqual(application.versions.count(), 1)

    def test_initial_membership_is_deterministic_and_ignores_viewer(self):
        user = make_user("multi@example.test")
        viewer_org = Organization.objects.create(
            name="АО Витрина",
            inn="1111111111",
            verification_status=Organization.VerificationStatus.VERIFIED,
        )
        submitter_org = Organization.objects.create(
            name="ООО Рабочая",
            inn="2222222222",
            verification_status=Organization.VerificationStatus.VERIFIED,
        )
        OrganizationMembership.objects.create(
            organization=viewer_org,
            user=user,
            role=OrganizationMembership.Role.VIEWER,
        )
        OrganizationMembership.objects.create(
            organization=submitter_org,
            user=user,
            role=OrganizationMembership.Role.SUBMITTER,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("business-cabinet"))

        initial = response.context["form"].initial
        self.assertEqual(initial["organization_name"], submitter_org.name)
        self.assertEqual(initial["inn"], submitter_org.inn)

    def test_viewer_can_submit_own_application_without_changing_membership(self):
        viewer = make_user("viewer@example.test")
        membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=viewer,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.client.force_login(viewer)

        response = self.client.post(reverse("business-cabinet"), self.valid_payload())

        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertTrue(Application.objects.filter(created_by=viewer).exists())
        membership.refresh_from_db()
        self.assertEqual(membership.role, OrganizationMembership.Role.VIEWER)


class SameInnCrossUserPrivacyTests(CreditTestCase):
    def test_second_user_same_inn_gets_own_application_and_no_access_to_first(self):
        self.organization.verification_status = Organization.VerificationStatus.PENDING
        self.organization.save(update_fields=("verification_status", "updated_at"))
        self.client.force_login(self.business_user)
        first_payload = self.valid_payload()
        first_payload["organization_name"] = "ООО Север"
        self.client.post(reverse("business-cabinet"), first_payload)
        first = Application.objects.get(created_by=self.business_user)
        first_document = first.versions.get().documents.get()

        second_user = make_user("second@example.test")
        self.client.force_login(second_user)
        second_payload = self.valid_payload()
        second_payload["organization_name"] = "ООО Другой бизнес"
        response = self.client.post(reverse("business-cabinet"), second_payload)

        self.assertEqual(response.status_code, 302, response.content.decode())
        second = Application.objects.get(created_by=second_user)
        self.assertEqual(second.organization_name, "ООО Другой бизнес")
        self.assertEqual(second.versions.get().snapshot["organization_name"], "ООО Другой бизнес")
        self.assertEqual(Organization.objects.count(), 1)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.name, "ООО Север")
        self.assertEqual(
            self.organization.verification_status, Organization.VerificationStatus.PENDING
        )
        self.assertFalse(
            OrganizationMembership.objects.filter(user=second_user).exists()
        )
        self.assertFalse(
            OrganizationAccessRequest.objects.filter(user=second_user).exists()
        )

        listing = self.client.get(reverse("business-applications"))
        self.assertContains(listing, "ООО Другой бизнес")
        self.assertNotContains(listing, "ООО Север")

        detail_url = reverse("business-application-detail", args=[first.pk])
        self.assertEqual(self.client.get(detail_url).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("document-download", args=[first_document.pk])).status_code,
            403,
        )
        first.status = Application.Status.NEEDS_REVISION
        first.save(update_fields=("status", "updated_at"))
        revision = self.valid_payload()
        self.assertEqual(self.client.post(detail_url, revision).status_code, 403)

        own_detail_url = reverse("business-application-detail", args=[second.pk])
        self.assertEqual(self.client.get(own_detail_url).status_code, 200)
        own_document = second.versions.get().documents.get()
        self.assertEqual(
            self.client.get(reverse("document-download", args=[own_document.pk])).status_code,
            200,
        )


class DocumentContractTests(CreditTestCase):
    def test_accept_contract_excludes_xls_and_old_unbounded_copy(self):
        accept = ApplicationForm().fields["documents"].widget.attrs["accept"]
        extensions = set(accept.split(","))
        self.assertIn(".pdf", extensions)
        self.assertIn(".xlsx", extensions)
        self.assertNotIn(".xls", extensions)
        self.assertEqual(ApplicationForm.MAX_DOCUMENT_SIZE, 10 * 1024 * 1024)
        self.assertEqual(ApplicationForm.MAX_DOCUMENTS_COUNT, 5)

    def test_xls_and_oversize_files_are_rejected(self):
        payload = self.valid_payload()
        payload.pop("document_statement")
        files = {"document_statement": [attachment("statement.xls")]}
        form = ApplicationForm(payload, files)
        self.assertFalse(form.is_valid())
        self.assertIn("document_statement", form.errors)

        oversized = attachment("statement.pdf")
        oversized.size = ApplicationForm.MAX_DOCUMENT_SIZE + 1
        payload = self.valid_payload()
        payload.pop("document_statement")
        form = ApplicationForm(payload, {"document_statement": [oversized]})
        self.assertFalse(form.is_valid())
        self.assertIn("document_statement", form.errors)

    def test_five_files_allowed_and_six_rejected(self):
        payload = self.valid_payload()
        statement = payload.pop("document_statement")
        form = ApplicationForm(
            payload,
            {
                "document_statement": statement,
                "documents": [attachment(f"extra-{index}.pdf") for index in range(4)],
            },
        )
        self.assertTrue(form.is_valid(), form.errors)

        payload = self.valid_payload()
        statement = payload.pop("document_statement")
        form = ApplicationForm(
            payload,
            {
                "document_statement": statement,
                "documents": [attachment(f"extra-{index}.pdf") for index in range(5)],
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn(
            "не более 5", " ".join(form.non_field_errors())
        )

    def test_revision_drops_stale_typed_docs_but_appends_generic_and_retains_required(self):
        investment = Product.objects.create(
            code="investment",
            name="Инвестиционный кредит",
            description="Демо",
            min_amount=Decimal("100000.00"),
            max_amount=Decimal("30000000.00"),
            min_term_months=3,
            max_term_months=60,
        )
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload.update(need="equipment", product=investment.pk)
        payload["document_estimate"] = [attachment("estimate.pdf")]
        payload["documents"] = [attachment("annual.pdf")]
        self.client.post(reverse("business-cabinet"), payload)
        application = Application.objects.get()
        first = application.versions.get()
        self.assertEqual(
            set(first.documents.values_list("requirement_code", flat=True)),
            {"statement", "estimate", ""},
        )

        application.status = Application.Status.NEEDS_REVISION
        application.save(update_fields=("status", "updated_at"))
        revision = self.valid_payload()
        revision.pop("document_statement")
        revision["documents"] = [attachment("annual-2.pdf")]
        response = self.client.post(
            reverse("business-application-detail", args=[application.pk]), revision
        )

        self.assertEqual(response.status_code, 302, response.content.decode())
        current = application.versions.get(version_number=2)
        codes = set(current.documents.values_list("requirement_code", flat=True))
        self.assertEqual(codes, {"statement", ""})
        self.assertFalse(current.documents.filter(requirement_code="estimate").exists())
        self.assertEqual(
            set(current.documents.filter(requirement_code="").values_list("original_name", flat=True)),
            {"annual.pdf", "annual-2.pdf"},
        )

    def test_switch_to_working_ignores_stale_fresh_and_retained_typed_files(self):
        contract_product = Product.objects.create(
            code="contract-finance",
            name="Контрактный кредит",
            description="Демо",
            min_amount=Decimal("100000.00"),
            max_amount=Decimal("30000000.00"),
            min_term_months=3,
            max_term_months=24,
        )
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload.update(
            need="contract",
            product=contract_product.pk,
            collateral="on",
        )
        payload["document_contract"] = [attachment("contract.pdf")]
        payload["document_collateral"] = [attachment("collateral.pdf")]
        payload["documents"] = [attachment("annual.pdf")]
        self.client.post(reverse("business-cabinet"), payload)
        application = Application.objects.get()
        first = application.versions.get()
        self.assertEqual(
            set(first.documents.values_list("requirement_code", flat=True)),
            {"statement", "contract", "collateral", ""},
        )

        application.status = Application.Status.NEEDS_REVISION
        application.save(update_fields=("status", "updated_at"))
        revision = self.valid_payload()
        revision.pop("document_statement")
        revision["document_contract"] = [attachment("stale-contract.exe")]
        revision["document_collateral"] = [attachment("stale-collateral.pdf")]
        revision["document_estimate"] = [attachment("stale-estimate.pdf")]
        revision["documents"] = [attachment("annual-2.pdf")]

        response = self.client.post(
            reverse("business-application-detail", args=[application.pk]), revision
        )

        self.assertEqual(response.status_code, 302, response.content.decode())
        current = application.versions.get(version_number=2)
        self.assertEqual(
            set(current.documents.values_list("requirement_code", flat=True)),
            {"statement", ""},
        )
        self.assertEqual(
            set(current.documents.filter(requirement_code="").values_list("original_name", flat=True)),
            {"annual.pdf", "annual-2.pdf"},
        )
        self.assertEqual(current.documents.count(), 3)

    def test_legacy_invalid_generic_can_be_excluded_from_carryover(self):
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload["documents"] = [attachment(f"generic-{index}.pdf") for index in range(4)]
        self.client.post(reverse("business-cabinet"), payload)
        application = Application.objects.get()
        first = application.versions.get()
        original_ids = set(first.documents.values_list("pk", flat=True))
        legacy = stored_document(first)

        application.status = Application.Status.NEEDS_REVISION
        application.save(update_fields=("status", "updated_at"))
        revision = self.valid_payload()
        revision.pop("document_statement")
        revision["remove_document_ids"] = [str(legacy.pk)]

        response = self.client.post(
            reverse("business-application-detail", args=[application.pk]), revision
        )

        self.assertEqual(response.status_code, 302, response.content.decode())
        current = application.versions.get(version_number=2)
        self.assertEqual(current.documents.count(), 5)
        self.assertFalse(current.documents.filter(pk=legacy.pk).exists())
        first.refresh_from_db()
        self.assertEqual(first.documents.count(), 6)
        self.assertEqual(
            set(first.documents.values_list("pk", flat=True)), original_ids | {legacy.pk}
        )
        legacy.refresh_from_db()
        self.assertEqual(legacy.original_name, "legacy.xls")
        self.assertEqual(legacy.size, 20 * 1024 * 1024)

    def test_removing_required_typed_without_replacement_is_invalid(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()
        first = application.versions.get()
        statement = first.documents.get(requirement_code="statement")

        application.status = Application.Status.NEEDS_REVISION
        application.save(update_fields=("status", "updated_at"))
        revision = self.valid_payload()
        revision.pop("document_statement")
        revision["remove_document_ids"] = [str(statement.pk)]

        response = self.client.post(
            reverse("business-application-detail", args=[application.pk]), revision
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("document_statement", response.context["revision_form"].errors)
        self.assertFalse(application.versions.filter(version_number=2).exists())
        self.assertTrue(first.documents.filter(pk=statement.pk).exists())

    def test_foreign_and_unknown_document_ids_are_rejected_without_leak(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()
        first = application.versions.get()
        application.status = Application.Status.NEEDS_REVISION
        application.save(update_fields=("status", "updated_at"))
        foreign_version = ApplicationVersion.objects.create(
            application=application,
            version_number=99,
            product=self.product,
            amount=Decimal("1500000.00"),
            term_months=12,
            purpose="Тестовая версия для чужого документа",
            submitted_at=timezone.now(),
            snapshot={},
        )
        foreign = stored_document(foreign_version, name="foreign-secret.pdf", size=100)
        revision = self.valid_payload()
        revision["remove_document_ids"] = [
            str(foreign.pk),
            "00000000-0000-0000-0000-000000000000",
        ]

        response = self.client.post(
            reverse("business-application-detail", args=[application.pk]), revision
        )

        self.assertEqual(response.status_code, 200)
        errors = response.context["revision_form"].errors
        self.assertIn("remove_document_ids", errors)
        self.assertNotIn("foreign-secret.pdf", " ".join(errors["remove_document_ids"]))
        self.assertFalse(application.versions.filter(version_number=2).exists())
        self.assertTrue(first.documents.exists())

    def test_retained_documents_count_towards_five_file_limit(self):
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload["documents"] = [attachment(f"generic-{index}.pdf") for index in range(4)]
        self.client.post(reverse("business-cabinet"), payload)
        application = Application.objects.get()
        application.status = Application.Status.NEEDS_REVISION
        application.save(update_fields=("status", "updated_at"))
        revision = self.valid_payload()
        revision.pop("document_statement")
        revision["documents"] = [attachment("add-1.pdf"), attachment("add-2.pdf")]

        response = self.client.post(
            reverse("business-application-detail", args=[application.pk]), revision
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "не более 5", " ".join(response.context["revision_form"].non_field_errors())
        )
        self.assertEqual(application.versions.count(), 1)


class AdvisorContractTests(CreditTestCase):
    def test_amount_and_term_limitations_are_exposed_together(self):
        result = recommend(
            {"need": "working", "amount": Decimal("1"), "term_months": 120},
            [self.product],
        )
        item = result["items"][0]
        self.assertFalse(item["available"])
        self.assertIn("Сумма", item["limitation"])
        self.assertIn("Срок", item["limitation"])
        self.assertIn("сотрудник", result["message"])

    def test_unsupported_alternative_is_reference_only_with_consultation(self):
        result = recommend({"need": "gap", "gap_kind": "receivable"}, [self.product])
        item = result["items"][0]
        self.assertEqual(item["code"], "factoring")
        self.assertFalse(item["available"])
        self.assertIn("справочная", item["limitation"])
        self.assertIn("сотрудник", result["message"])

    def test_financing_advice_api_shape_and_selection(self):
        self.client.force_login(self.business_user)
        response = self.client.get(
            reverse("financing-advice"),
            {"need": "working", "product": "working-capital", "amount": "1500000", "term_months": "12"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            set(data),
            {"items", "message", "checklist", "selection_matches", "rules_version"},
        )
        self.assertTrue(data["selection_matches"])
        self.assertEqual(data["rules_version"], "needs-2")
        self.assertEqual(data["checklist"][0]["code"], "statement")
