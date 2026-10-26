from django.test import TestCase
from django.urls import reverse


class AuthPageRenderTests(TestCase):
    def test_login_renders_email_password_and_csrf_without_demo_note(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/login.html")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, "README")
        self.assertNotContains(response, "Демо-доступы")
        self.assertNotContains(response, "ИНН")

    def test_login_keeps_next_and_shows_invalid_credentials_error(self):
        response = self.client.get(reverse("login"), {"next": "/applications/new/"})
        self.assertContains(response, 'value="/applications/new/"')

        response = self.client.post(
            reverse("login"),
            {"username": "nobody@example.test", "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, "Email или пароль указаны неверно.")

    def test_register_uses_generic_business_steps_and_password_grid(self):
        response = self.client.get(reverse("register"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/register.html")
        self.assertContains(response, "Заполните сведения о бизнесе")
        self.assertContains(response, "Подайте заявку")
        self.assertContains(response, "form-grid form-grid--passwords")
        self.assertNotContains(response, "ИНН")
        self.assertNotContains(response, "Подтвердите компанию")
        self.assertNotContains(response, "ИНН исключено")

    def test_register_shows_field_errors_for_password_mismatch(self):
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Мария",
                "last_name": "Орлова",
                "email": "maria@example.test",
                "password1": "UniquePass123!",
                "password2": "DifferentPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "не совпадают")
        self.assertContains(response, 'role="alert"')
