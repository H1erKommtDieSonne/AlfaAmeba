from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import (
    Application,
    ApplicationComment,
    ApplicationEvent,
    Organization,
    OrganizationAccessRequest,
    OrganizationMembership,
    Product,
    UserProfile,
)


class VerticalSliceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(
            code="working-capital",
            name="Оборотный кредит",
            description="На текущие расходы",
            min_amount=Decimal("100000.00"),
            max_amount=Decimal("30000000.00"),
            min_term_months=3,
            max_term_months=36,
            badge="Основной сценарий",
        )
        cls.business_user = get_user_model().objects.create_user(
            username="owner@example.test", email="owner@example.test", password="StrongPass123!"
        )
        UserProfile.objects.create(user=cls.business_user, role=UserProfile.Role.BUSINESS)
        cls.organization = Organization.objects.create(
            name="ООО Север",
            inn="7707083893",
            verification_status=Organization.VerificationStatus.VERIFIED,
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.business_user,
            role=OrganizationMembership.Role.OWNER,
        )
        cls.bank_user = get_user_model().objects.create_user(
            username="bank@example.test", email="bank@example.test", password="StrongPass123!"
        )
        UserProfile.objects.create(user=cls.bank_user, role=UserProfile.Role.BANK_REVIEWER)
        cls.senior_approver = get_user_model().objects.create_user(
            username="senior@example.test",
            email="senior@example.test",
            password="StrongPass123!",
        )
        UserProfile.objects.create(
            user=cls.senior_approver,
            role=UserProfile.Role.SENIOR_APPROVER,
        )
        cls.bank_admin = get_user_model().objects.create_user(
            username="admin@example.test", email="admin@example.test", password="StrongPass123!"
        )
        UserProfile.objects.create(user=cls.bank_admin, role=UserProfile.Role.BANK_ADMIN)

    def valid_payload(self):
        return {
            "organization_name": "ООО Север",
            "inn": "7707083893",
            "product": self.product.pk,
            "amount": "1500000.00",
            "term_months": "12",
            "purpose": "Закупка сезонной партии товара на три месяца",
        }

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("business-cabinet"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('business-cabinet')}")

    def test_registration_creates_business_profile_and_logs_user_in(self):
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Мария",
                "last_name": "Орлова",
                "email": "maria@example.test",
                "password1": "UniquePass123!",
                "password2": "UniquePass123!",
            },
        )

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        user = get_user_model().objects.get(username="maria@example.test")
        self.assertEqual(user.profile.role, UserProfile.Role.BUSINESS)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_submission_creates_snapshot_visible_to_bank(self):
        self.client.force_login(self.business_user)
        response = self.client.post(reverse("business-cabinet"), self.valid_payload())
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

        self.client.force_login(self.bank_user)
        queue = self.client.get(reverse("bank-queue"))
        self.assertContains(queue, "ООО Север")
        self.assertContains(queue, "1\u00a0500\u00a0000")

        version.purpose = "Попытка изменить уже отправленную версию"
        with self.assertRaises(ValidationError):
            version.save()

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

    def test_product_limits_are_validated_on_the_server(self):
        self.client.force_login(self.business_user)
        payload = self.valid_payload()
        payload["amount"] = "1000.00"

        response = self.client.post(reverse("business-cabinet"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Для этого продукта сумма от")
        self.assertFalse(Application.objects.exists())

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

    def test_unrelated_business_user_gets_pending_request_not_membership(self):
        self.client.force_login(self.business_user)
        self.client.post(reverse("business-cabinet"), self.valid_payload())
        application = Application.objects.get()

        outsider = get_user_model().objects.create_user(
            username="outsider@example.test",
            email="outsider@example.test",
            password="StrongPass123!",
        )
        UserProfile.objects.create(user=outsider, role=UserProfile.Role.BUSINESS)
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
        newcomer = get_user_model().objects.create_user(
            username="newcomer@example.test",
            email="newcomer@example.test",
            password="StrongPass123!",
        )
        UserProfile.objects.create(user=newcomer, role=UserProfile.Role.BUSINESS)
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

