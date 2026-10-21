from django.test import TestCase
from django.urls import reverse

from core.testing import PASSWORD, make_user

from .models import User
from .permissions import can_submit_applications


class RegistrationTests(TestCase):
    def test_registration_creates_business_user_and_logs_user_in(self):
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
        user = User.objects.get(email="maria@example.test")
        self.assertEqual(user.role, User.Role.BUSINESS)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)


class UserModelTests(TestCase):
    def test_login_ignores_email_case(self):
        make_user("Owner@Example.test")

        response = self.client.post(
            reverse("login"), {"username": "OWNER@example.TEST", "password": PASSWORD}
        )

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(email="owner@example.test").exists())

    def test_superuser_gets_bank_role_and_cannot_submit(self):
        root = User.objects.create_superuser("root@example.test", PASSWORD)

        self.assertEqual(root.role, User.Role.BANK_ADMIN)
        self.assertFalse(can_submit_applications(root))
