from django.urls import reverse

from core.testing import CreditTestCase


class SectionTests(CreditTestCase):
    def test_section_follows_url_name(self):
        self.client.force_login(self.business_user)
        self.assertEqual(
            self.client.get(reverse("business-applications")).context["section"],
            "business-applications",
        )
        self.assertEqual(self.client.get(reverse("business-cabinet")).context["section"], "business")

        self.client.force_login(self.bank_user)
        self.assertEqual(self.client.get(reverse("bank-queue")).context["section"], "bank")
