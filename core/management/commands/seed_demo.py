from decimal import Decimal

import hashlib
import uuid

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from applications.models import Application, ApplicationEvent, ApplicationVersion
from documents.models import ApplicationDocument
from node_integration.demo import (
    CONTRACT_DOCUMENTS,
    WORKING_DOCUMENTS,
    contract_result,
    demo_pdf,
    working_capital_result,
)
from node_integration.models import NodeJob
from node_integration.services import enqueue_version, sha256_json
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


# Две заявки для карточки сотрудника: комплект собран и комплект неполный.
# Идентификаторы постоянные, чтобы повторный запуск обновлял их, а не плодил новые.
DEMO_APPLICATIONS = (
    {
        "id": uuid.UUID("5e1f0a21-7b2f-4c8e-9a61-5f0b2d4c7e01"),
        "title": "оборотный кредит, комплект собран",
        "product": "working-capital",
        "amount": Decimal("15000000.00"),
        "term": 24,
        "purpose": "Закупка товара под сезон и пополнение оборотных средств",
        "needs": {"need": "working", "gap_kind": "", "debt_kind": "", "collateral": False},
        "build": working_capital_result,
        "documents": WORKING_DOCUMENTS,
    },
    {
        "id": uuid.UUID("7c42b9d3-7b2f-4c8e-9a61-5f0b2d4c7e02"),
        "title": "контрактный кредит, нужны документы",
        "product": "contract-finance",
        "amount": Decimal("8000000.00"),
        "term": 12,
        "purpose": "Исполнение контракта на поставку оборудования",
        "needs": {"need": "contract", "gap_kind": "", "debt_kind": "", "collateral": True},
        "build": contract_result,
        "documents": CONTRACT_DOCUMENTS,
    },
)


class Command(BaseCommand):
    help = "Создаёт или обновляет синтетический каталог продуктов, демо-пользователей и демо-заявки"

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
        bank_user = self.create_demo_user(
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
        for demo in DEMO_APPLICATIONS:
            self.create_demo_application(demo, organization=organization, client=business_user, reviewer=bank_user)
        self.stdout.write(self.style.SUCCESS("Демо-каталог готов"))

    @transaction.atomic
    def create_demo_application(self, demo, *, organization, client, reviewer):
        """Заявка в работе у кредитного специалиста с уже полученным ответом узла (схема v2).

        Ответ узла пересобирается при каждом запуске, а сама заявка и её версия создаются
        один раз: отправленную версию менять нельзя (ApplicationVersion.save).
        """
        submitted_at = timezone.now()
        application, created = Application.objects.get_or_create(
            pk=demo["id"],
            defaults={
                "organization": organization,
                "organization_name": organization.name,
                "inn": organization.inn,
                "created_by": client,
                "assigned_to": reviewer,
                "status": Application.Status.IN_REVIEW,
                "submitted_at": submitted_at,
            },
        )
        if created:
            product = Product.objects.get(code=demo["product"])
            ApplicationVersion.objects.create(
                application=application,
                version_number=1,
                product=product,
                amount=demo["amount"],
                term_months=demo["term"],
                purpose=demo["purpose"],
                submitted_at=submitted_at,
                snapshot={"needs": demo["needs"], "product_code": product.code, "inn": organization.inn,
                          "organization_name": organization.name, "amount": str(demo["amount"]),
                          "term_months": demo["term"], "purpose": demo["purpose"], "version": 1,
                          "submitted_at": submitted_at.isoformat()},
            )
            ApplicationEvent.objects.create(
                application=application, actor=client, event_type=ApplicationEvent.Type.SUBMITTED,
                to_status=Application.Status.SUBMITTED, message="Первая версия заявки отправлена в банк (демо)",
            )
            ApplicationEvent.objects.create(
                application=application, actor=reviewer, event_type=ApplicationEvent.Type.ASSIGNED,
                from_status=Application.Status.SUBMITTED, to_status=Application.Status.IN_REVIEW,
            )
        version = application.versions.get(version_number=1)
        if not version.documents.exists():
            self.attach_demo_documents(version, demo["documents"])
        payload = demo["build"](application_id=application.pk, inn=organization.inn, amount=demo["amount"], term=demo["term"])
        # Ответ кладём в первую попытку; пересчёты, запрошенные на стенде, остаются как были.
        job = enqueue_version(version)
        job.status = NodeJob.Status.COMPLETED
        job.external_job_id = application.pk.hex
        job.result = payload
        job.result_sha256 = sha256_json(payload)
        job.completed_at = timezone.now()
        job.save()
        self.stdout.write(f"Демо-заявка {'создана' if created else 'обновлена'}: {demo['title']}")

    def attach_demo_documents(self, version, documents):
        """Синтетические PDF с теми же именами, что в демо-ответе узла: список документов и форма сходятся."""
        for filename, requirement_code, lines in documents:
            content = demo_pdf(*lines)
            ApplicationDocument.objects.create(
                version=version, file=ContentFile(content, name=filename), original_name=filename,
                requirement_code=requirement_code, content_type="application/pdf",
                size=len(content), sha256=hashlib.sha256(content).hexdigest(),
            )

    def create_demo_user(self, *, email, password, first_name, last_name, role):
        user, _ = User.objects.get_or_create(email=email)
        user.first_name = first_name
        user.last_name = last_name
        user.role = role
        user.set_password(password)
        user.save()
        self.stdout.write(f"Демо-пользователь готов: {email}")
        return user

