import time

from django.core.management.base import BaseCommand, CommandError

from node_integration.outbound import OutboundConfigError, load_config, run_once


class Command(BaseCommand):
    help = "Отправить PENDING-задачи проверки документов внешнему узлу"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--once", action="store_true", help="Отправить одну задачу (по умолчанию)")
        group.add_argument("--watch", action="store_true", help="Ожидать и отправлять новые задачи")

    def handle(self, *args, **options):
        try:
            config = load_config()
        except OutboundConfigError as error:
            raise CommandError(f"Отправка узлу не настроена: {error}") from error

        if not options["watch"]:
            job = run_once(config)
            if job is None:
                self.stdout.write("Нет задач для отправки")
            else:
                self.stdout.write(f"Задача {job.pk}: {job.get_status_display()}")
            return

        while True:
            try:
                job = run_once(config)
            except Exception as error:
                self.stderr.write(f"Не удалось отправить задачу: {error}")
                job = None
            if job is None:
                time.sleep(config.poll_seconds)
            else:
                self.stdout.write(f"Задача {job.pk}: {job.get_status_display()}")
