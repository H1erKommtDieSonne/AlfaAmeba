import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ml.queue_client import QueueClient
from ml.worker import run_once


class Command(BaseCommand):
    help = "Обработать документы через HTTP pull-очередь локального кабинета"

    def add_arguments(self, parser):
        parser.add_argument("--queue-url", default="http://127.0.0.1:8000/api/v1/llm")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--once", action="store_true", help="Обработать одну задачу (по умолчанию)")
        group.add_argument("--watch", action="store_true", help="Ожидать и обрабатывать новые задачи")

    def handle(self, *args, **options):
        try:
            import pypdf, openpyxl, defusedxml  # noqa: F401
        except ImportError as error:
            raise CommandError("Установите зависимости: pip install -r ml/requirements.txt") from error
        client = QueueClient(options["queue_url"], settings.LLM_SERVICE_TOKEN)
        while True:
            try:
                processed = run_once(client, f"local-command-{os.getpid()}")
                if processed or not options["watch"]:
                    self.stdout.write("Задача обработана" if processed else "Очередь пуста")
            except Exception as error:
                if not options["watch"]:
                    raise CommandError("Не удалось обработать задачу. Проверьте сервер, токен и статус очереди.") from error
                self.stderr.write("Сервер недоступен или задачу не удалось обработать; повтор через 3 секунды")
                processed = False
            if not options["watch"]:
                return
            if not processed:
                time.sleep(3)
