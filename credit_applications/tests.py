from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Application, Product, UserProfile


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
        cls.bank_user = get_user_model().objects.create_user(
            username="bank@example.test", email="bank@example.test", password="StrongPass123!"
        )
        UserProfile.objects.create(user=cls.bank_user, role=UserProfile.Role.BANK_EMPLOYEE)
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
        self.assertContains(admin_page, "Обзор системы")
        self.assertContains(admin_page, "Администрирование")
        self.assertNotContains(admin_page, "Новая заявка")

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

