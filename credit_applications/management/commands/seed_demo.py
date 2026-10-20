from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from credit_applications.models import Product, UserProfile


PRODUCTS = (
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
        self.create_demo_user(
            email="business@example.test",
            password="DemoPass123!",
            first_name="Анна",
            last_name="Волкова",
            role=UserProfile.Role.BUSINESS,
        )
        self.create_demo_user(
            email="bank@example.test",
            password="DemoPass123!",
            first_name="Илья",
            last_name="Соколов",
            role=UserProfile.Role.BANK_EMPLOYEE,
        )
        self.create_demo_user(
            email="admin@example.test",
            password="DemoPass123!",
            first_name="Ольга",
            last_name="Миронова",
            role=UserProfile.Role.BANK_ADMIN,
        )
        self.stdout.write(self.style.SUCCESS("Демо-каталог готов"))

    def create_demo_user(self, *, email, password, first_name, last_name, role):
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(
            username=email,
            defaults={"email": email, "first_name": first_name, "last_name": last_name},
        )
        user.email = email
        user.first_name = first_name
        user.last_name = last_name
        user.set_password(password)
        user.save()
        UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        self.stdout.write(f"Демо-пользователь готов: {email}")

