from decimal import Decimal

from django.core.management.base import BaseCommand

from organizations.models import Organization, OrganizationMembership
from products.models import Product
from users.models import User


PRODUCTS = (
    {
        "code": "investment", "name": "Инвестиционный кредит",
        "description": "Демо: оборудование, ремонт и расширение бизнеса.",
        "min_amount": Decimal("100000.00"), "max_amount": Decimal("30000000.00"),
        "min_term_months": 6, "max_term_months": 60, "badge": "Демо-условия",
    },
    {
        "code": "refinancing", "name": "Рефинансирование",
        "description": "Демо: пересмотр действующих кредитов без просрочки.",
        "min_amount": Decimal("100000.00"), "max_amount": Decimal("30000000.00"),
        "min_term_months": 6, "max_term_months": 60, "badge": "Демо-условия",
    },
    {
        "code": "working-capital",
        "name": "Оборотный кредит",
        "description": "На закупку товара, сырья и текущие расходы бизнеса.",
        "min_amount": Decimal("100000.00"),
        "max_amount": Decimal("30000000.00"),
        "min_term_months": 3,
        "max_term_months": 36,
        "badge": "Основной сценарий",
    },
    {
        "code": "contract-finance",
        "name": "Контрактный кредит",
        "description": "На исполнение подтверждённого коммерческого или госконтракта.",
        "min_amount": Decimal("300000.00"),
        "max_amount": Decimal("50000000.00"),
        "min_term_months": 3,
        "max_term_months": 24,
        "badge": "Демо-гипотеза",
    },
)


class Command(BaseCommand):
    help = "Создаёт или обновляет синтетический каталог продуктов"

    def handle(self, *args, **options):
        for item in PRODUCTS:
            product, created = Product.objects.update_or_create(
                code=item["code"], defaults={**item, "active": True}
            )
            self.stdout.write(f"{'Создан' if created else 'Обновлён'}: {product.name}")
        business_user = self.create_demo_user(
            email="business@example.test",
            password="DemoPass123!",
            first_name="Анна",
            last_name="Волкова",
            role=User.Role.BUSINESS,
        )
        organization, _ = Organization.objects.get_or_create(
            inn="7707083893",
            defaults={
                "name": "ООО Север",
                "verification_status": Organization.VerificationStatus.VERIFIED,
            },
        )
        if organization.verification_status != Organization.VerificationStatus.VERIFIED:
            organization.verification_status = Organization.VerificationStatus.VERIFIED
            organization.save(update_fields=("verification_status", "updated_at"))
        OrganizationMembership.objects.update_or_create(
            organization=organization,
            user=business_user,
            defaults={"role": OrganizationMembership.Role.OWNER, "is_active": True},
        )
        self.create_demo_user(
            email="bank@example.test",
            password="DemoPass123!",
            first_name="Илья",
            last_name="Соколов",
            role=User.Role.BANK_REVIEWER,
        )
        self.create_demo_user(
            email="senior@example.test",
            password="DemoPass123!",
            first_name="Сергей",
            last_name="Петров",
            role=User.Role.SENIOR_APPROVER,
        )
        self.create_demo_user(
            email="admin@example.test",
            password="DemoPass123!",
            first_name="Ольга",
            last_name="Миронова",
            role=User.Role.BANK_ADMIN,
        )
        self.stdout.write(self.style.SUCCESS("Демо-каталог готов"))

    def create_demo_user(self, *, email, password, first_name, last_name, role):
        user, _ = User.objects.get_or_create(email=email)
        user.first_name = first_name
        user.last_name = last_name
        user.role = role
        user.set_password(password)
        user.save()
        self.stdout.write(f"Демо-пользователь готов: {email}")
        return user

