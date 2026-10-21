from django.urls import reverse

from applications.models import Application
from core.testing import CreditTestCase, make_user

from .models import OrganizationAccessRequest, OrganizationMembership


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
        self.assertNotContains(admin_page, "Новая заявка")
        self.assertNotContains(admin_page, "Последние заявки")

        queue = self.client.get(reverse("bank-queue"))
        self.assertEqual(queue.status_code, 403)

        self.client.force_login(self.bank_user)
        forbidden = self.client.get(reverse("bank-admin-dashboard"))
        self.assertEqual(forbidden.status_code, 403)


class AccessRequestTests(CreditTestCase):
    def test_unrelated_business_user_gets_pending_request_not_membership(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()

        outsider = make_user("outsider@example.test")
        self.client.force_login(outsider)

        response = self.client.post(reverse("business-cabinet"), self.valid_payload())
        self.assertRedirects(response, reverse("organization-access-status"))
        self.assertEqual(Application.objects.count(), 1)
        self.assertFalse(
            OrganizationMembership.objects.filter(organization=self.organization, user=outsider).exists()
        )
        self.assertTrue(
            OrganizationAccessRequest.objects.filter(
                organization=self.organization,
                user=outsider,
                status=OrganizationAccessRequest.Status.PENDING,
            ).exists()
        )

        detail = self.client.get(reverse("business-application-detail", args=[application.pk]))
        self.assertEqual(detail.status_code, 403)

    def test_first_claimant_cannot_submit_until_admin_approves_access(self):
        newcomer = make_user("newcomer@example.test")
        payload = self.valid_payload()
        payload["organization_name"] = "ООО Новая компания"
        payload["inn"] = "7801234567"
        self.client.force_login(newcomer)

        response = self.client.post(reverse("business-cabinet"), payload)
        self.assertRedirects(response, reverse("organization-access-status"))
        self.assertFalse(Application.objects.filter(created_by=newcomer).exists())
        access_request = OrganizationAccessRequest.objects.get(user=newcomer)
        self.assertEqual(access_request.status, OrganizationAccessRequest.Status.PENDING)
        self.assertFalse(OrganizationMembership.objects.filter(user=newcomer).exists())

        self.client.force_login(self.bank_admin)
        self.client.post(
            reverse("review-access-request", args=[access_request.pk]),
            {"action": "approve", "note": "Полномочия проверены вручную"},
        )
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, OrganizationAccessRequest.Status.APPROVED)
        self.assertTrue(
            OrganizationMembership.objects.filter(
                user=newcomer,
                organization=access_request.organization,
                role=OrganizationMembership.Role.OWNER,
            ).exists()
        )
