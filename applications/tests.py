from django.core.exceptions import ValidationError
from django.urls import reverse

from core.testing import CreditTestCase
from documents.models import DocumentProcessingJob
from organizations.models import OrganizationMembership

from .models import Application, ApplicationComment, ApplicationEvent


class BusinessCabinetTests(CreditTestCase):
    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("business-cabinet"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('business-cabinet')}")

    def test_application_form_renders_as_functional_four_step_wizard(self):
        self.client.force_login(self.business_user)
        response = self.client.get(reverse("business-cabinet"))

        self.assertContains(response, 'x-data="applicationWizard(')
        self.assertContains(response, 'data-form-step="4"')
        self.assertContains(response, 'name="amount"')
        self.assertContains(response, 'step="any"')

    def test_submission_creates_snapshot_visible_to_bank(self):
        self.client.force_login(self.business_user)
        response = self.client.post(reverse("business-cabinet"), self.valid_payload())
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertNotEqual(response.url, reverse("organization-access-status"))
        application = Application.objects.get()
        version = application.versions.get()

        self.assertRedirects(response, reverse("application-success", args=[application.pk]))
        self.assertEqual(application.created_by, self.business_user)
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertEqual(application.organization.inn, "7707083893")
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization=application.organization,
                user=self.business_user,
                role=OrganizationMembership.Role.OWNER,
            ).exists()
        )
        self.assertEqual(version.snapshot["organization_name"], "ООО Север")
        self.assertEqual(version.snapshot["amount"], "1500000.00")
        self.assertEqual(version.documents.count(), 1)
        self.assertEqual(version.processing_job.status, DocumentProcessingJob.Status.PENDING)

        self.client.force_login(self.bank_user)
        queue = self.client.get(reverse("bank-queue"))
        self.assertContains(queue, "ООО Север")
        self.assertContains(queue, "1 500 000")

        version.purpose = "Попытка изменить уже отправленную версию"
        with self.assertRaises(ValidationError):
            version.save()

    def test_product_limits_are_validated_on_the_server(self):
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload["amount"] = "1000.00"

        response = self.client.post(reverse("business-cabinet"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Для этого продукта сумма от")
        self.assertFalse(Application.objects.exists())


class CabinetSeparationTests(CreditTestCase):
    def test_business_user_cannot_open_bank_queue(self):
        self.client.force_login(self.business_user)
        response = self.client.get(reverse("bank-queue"))
        self.assertEqual(response.status_code, 403)

    def test_bank_employee_has_no_business_application_branch(self):
        self.client.force_login(self.bank_user)

        dashboard = self.client.get(reverse("dashboard"))
        self.assertRedirects(dashboard, reverse("bank-queue"), fetch_redirect_response=False)
        business = self.client.get(reverse("business-cabinet"))
        self.assertRedirects(business, reverse("dashboard"), fetch_redirect_response=False)
        queue = self.client.get(reverse("bank-queue"))
        self.assertNotContains(queue, "Новая заявка")
        self.assertNotContains(queue, "Администрирование")


class ReviewFlowTests(CreditTestCase):
    def test_bank_can_request_revision_and_client_submits_new_version(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()

        self.client.force_login(self.bank_user)
        detail_url = reverse("bank-application-detail", args=[application.pk])
        self.client.post(detail_url, {"form_type": "action", "action": "take", "note": ""})
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.IN_REVIEW)
        self.assertEqual(application.assigned_to, self.bank_user)

        self.client.post(
            detail_url,
            {
                "form_type": "action",
                "action": "request_revision",
                "note": "Уточните период закупки товара",
            },
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.NEEDS_REVISION)
        self.assertTrue(
            ApplicationComment.objects.filter(
                application=application,
                audience=ApplicationComment.Audience.CLIENT,
            ).exists()
        )

        self.client.force_login(self.business_user)
        client_detail = reverse("business-application-detail", args=[application.pk])
        page = self.client.get(client_detail)
        self.assertContains(page, "Уточните период закупки товара")
        payload = self.valid_payload()
        payload["purpose"] = "Закупка сезонной партии товара на период с мая по август"
        response = self.client.post(client_detail, payload)
        self.assertRedirects(response, client_detail)

        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.SUBMITTED)
        self.assertEqual(application.current_version, 2)
        self.assertEqual(application.versions.count(), 2)
        self.assertTrue(
            ApplicationEvent.objects.filter(
                application=application,
                event_type=ApplicationEvent.Type.REVISION_SUBMITTED,
            ).exists()
        )

    def test_only_senior_approver_can_make_final_decision(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()
        detail_url = reverse("bank-application-detail", args=[application.pk])

        self.client.force_login(self.bank_user)
        self.client.post(detail_url, {"form_type": "action", "action": "take", "note": ""})
        self.client.post(
            detail_url,
            {"form_type": "action", "action": "send_to_approval", "note": "Проверки завершены"},
        )
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.PENDING_APPROVAL)

        forbidden = self.client.post(
            detail_url,
            {"form_type": "action", "action": "approve", "note": ""},
        )
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_login(self.bank_admin)
        admin_forbidden = self.client.post(
            detail_url,
            {"form_type": "action", "action": "approve", "note": ""},
        )
        self.assertEqual(admin_forbidden.status_code, 403)

        self.client.force_login(self.senior_approver)
        response = self.client.post(
            detail_url,
            {"form_type": "action", "action": "approve", "note": "Условия подтверждены"},
        )
        self.assertRedirects(response, detail_url)
        application.refresh_from_db()
        self.assertEqual(application.status, Application.Status.APPROVED)
        self.assertIsNotNone(application.decided_at)

    def test_internal_bank_comment_is_not_visible_to_business(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()

        self.client.force_login(self.bank_user)
        self.client.post(
            reverse("bank-application-detail", args=[application.pk]),
            {
                "form_type": "comment",
                "text": "Внутренняя заметка сотрудника",
                "audience": ApplicationComment.Audience.INTERNAL,
            },
        )

        self.client.force_login(self.business_user)
        page = self.client.get(reverse("business-application-detail", args=[application.pk]))
        self.assertNotContains(page, "Внутренняя заметка сотрудника")
