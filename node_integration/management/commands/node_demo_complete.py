"""Ответить за внешний узел демо-результатом — только для разработки и демо-стенда.

Без узла пересчёт, запрошенный в карточке сотрудника, навсегда остаётся «ждёт
отправки». Команда берёт ожидающие отправки попытки и завершает их ответом из
``node_integration.demo``, учитывая подтверждённый сотрудником платёж.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from node_integration.demo import contract_result, working_capital_result
from node_integration.models import FinancialInputs, NodeJob
from node_integration.services import sha256_json


class Command(BaseCommand):
    help = "Завершить ожидающие задачи узла демо-ответом (только при DEBUG)"

    def add_arguments(self, parser):
        parser.add_argument("--application", help="Только задачи этой заявки (UUID)")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Команда только для разработки: настоящий ответ присылает узел.")
        jobs = NodeJob.objects.filter(status=NodeJob.Status.PENDING).select_related("version__application")
        if options["application"]:
            jobs = jobs.filter(version__application_id=options["application"])
        completed = 0
        for job in jobs:
            version = job.version
            inputs = FinancialInputs.objects.filter(version=version).first()
            build = contract_result if version.product_id == "contract-finance" else working_capital_result
            payload = build(
                application_id=version.application_id,
                version=version.version_number,
                inn=version.application.inn,
                amount=version.amount,
                term=version.term_months,
                payment=inputs.proposed_monthly_payment_rub if inputs else None,
            )
            job.status = NodeJob.Status.COMPLETED
            job.external_job_id = job.pk.hex
            job.result = payload
            job.result_sha256 = sha256_json(payload)
            job.completed_at = timezone.now()
            job.save()
            completed += 1
            self.stdout.write(f"Демо-ответ узла: заявка {version.application_id}, версия {version.version_number}")
        self.stdout.write(self.style.SUCCESS(f"Завершено задач: {completed}"))
