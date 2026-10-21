"""Общая обвязка сквозных тестов: заявка проходит через несколько приложений сразу."""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from organizations.models import Organization, OrganizationMembership
from products.models import Product
from users.models import User

PASSWORD = "StrongPass123!"


def make_user(email, role=User.Role.BUSINESS):
    return User.objects.create_user(email=email, password=PASSWORD, role=role)


class CreditTestCase(TestCase):
    """Продукт, проверенная организация с владельцем и по сотруднику на каждую роль банка."""

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
        cls.business_user = make_user("owner@example.test")
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
        cls.bank_user = make_user("bank@example.test", User.Role.BANK_REVIEWER)
        cls.senior_approver = make_user("senior@example.test", User.Role.SENIOR_APPROVER)
        cls.bank_admin = make_user("admin@example.test", User.Role.BANK_ADMIN)

    def valid_payload(self):
        return {
            "organization_name": "ООО Север",
            "inn": "7707083893",
            "product": self.product.pk,
            "amount": "1500000.00",
            "term_months": "12",
            "purpose": "Закупка сезонной партии товара на три месяца",
            "documents": [
                SimpleUploadedFile(
                    "account-statement.pdf",
                    b"%PDF-1.4 synthetic statement",
                    content_type="application/pdf",
                )
            ],
        }
