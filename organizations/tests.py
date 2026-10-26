from django.urls import reverse

from applications.models import Application
from core.testing import CreditTestCase, make_user

from .models import Organization, OrganizationAccessRequest, OrganizationMembership


class BankAdminDashboardTests(CreditTestCase):
    def test_bank_admin_has_separate_dashboard(self):
        self.client.force_login(self.bank_admin)

        dashboard = self.client.get(reverse("dashboard"))
        self.assertRedirects(
            dashboard, reverse("bank-admin-dashboard"), fetch_redirect_response=False
        )
        admin_page = self.client.get(reverse("bank-admin-dashboard"))
        self.assertContains(admin_page, "Управление доступом")
        self.assertContains(admin_page, "Администрирование")
        self.assertNotContains(admin_page, "Подтвердить")
        self.assertNotContains(admin_page, "Запросы на доступ")
        self.assertNotContains(admin_page, "ИНН сам по себе")
        self.assertNotContains(admin_page, "Новая заявка")
        self.assertNotContains(admin_page, "Последние заявки")

        queue = self.client.get(reverse("bank-queue"))
        self.assertEqual(queue.status_code, 403)

        self.client.force_login(self.bank_user)
        forbidden = self.client.get(reverse("bank-admin-dashboard"))
        self.assertEqual(forbidden.status_code, 403)


class SubmissionWithoutApprovalTests(CreditTestCase):
    def test_public_access_status_redirects_to_business_cabinet(self):
        self.client.force_login(self.business_user)

        response = self.client.get(reverse("organization-access-status"))

        self.assertRedirects(
            response, reverse("business-cabinet"), fetch_redirect_response=False
        )

    def test_outsider_submits_without_membership_or_access_request(self):
        outsider = make_user("outsider@example.test")
        self.client.force_login(outsider)
        payload = self.valid_payload()
        payload["organization_name"] = "ООО Другое название"

        response = self.client.post(reverse("business-cabinet"), payload)

        self.assertRedirects(
            response,
            reverse("application-success", args=[Application.objects.get(created_by=outsider).pk]),
            fetch_redirect_response=False,
        )
        application = Application.objects.get(created_by=outsider)
        self.assertEqual(application.organization_name, "ООО Другое название")
        self.assertFalse(
            OrganizationMembership.objects.filter(
                organization=self.organization, user=outsider
            ).exists()
        )
        self.assertFalse(
            OrganizationAccessRequest.objects.filter(user=outsider).exists()
        )
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.name, "ООО Север")
        self.assertEqual(Organization.objects.count(), 1)

    def test_pending_existing_organization_does_not_block_submission(self):
        self.organization.verification_status = Organization.VerificationStatus.PENDING
        self.organization.save(update_fields=("verification_status", "updated_at"))
        outsider = make_user("pending@example.test")
        self.client.force_login(outsider)
        payload = self.valid_payload()
        payload["organization_name"] = "ООО Новое имя"

        response = self.client.post(reverse("business-cabinet"), payload)

        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertTrue(Application.objects.filter(created_by=outsider).exists())
        self.organization.refresh_from_db()
        self.assertEqual(
            self.organization.verification_status, Organization.VerificationStatus.PENDING
        )
        self.assertEqual(self.organization.name, "ООО Север")

    def test_hidden_review_endpoint_does_not_grant_membership(self):
        outsider = make_user("legacy@example.test")
        access_request = OrganizationAccessRequest.objects.create(
            organization=self.organization, user=outsider
        )
        self.client.force_login(self.bank_admin)

        response = self.client.post(
            reverse("review-access-request", args=[access_request.pk]),
            {"action": "approve", "note": "Старый запрос"},
        )

        self.assertRedirects(
            response, reverse("bank-admin-dashboard"), fetch_redirect_response=False
        )
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, OrganizationAccessRequest.Status.PENDING)
        self.assertFalse(
            OrganizationMembership.objects.filter(
                organization=self.organization, user=outsider
            ).exists()
        )
