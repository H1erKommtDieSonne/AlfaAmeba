"""Pull worker. Parsers run in short-lived subprocesses with a time limit."""

import argparse
import hashlib
import logging
import multiprocessing
import os
import socket
import sys
import time

from .analysis import analyze, review_result, summarize
from .queue_client import QueueClient

logger = logging.getLogger(__name__)


def _parse_child(connection, document, content, application):
    try:
        from .extraction import ExtractionIssue, extract
        try:
            result = analyze(document, extract(content, document["name"]), application)
        except ExtractionIssue as error:
            result = review_result(document["document_id"], error.code, str(error))
        except Exception:
            result = review_result(document["document_id"], "UNREADABLE", "Файл повреждён или не удалось прочитать его структуру. Нужна другая копия или ручная проверка.")
        connection.send(result)
    finally:
        connection.close()


def process_document(document, content, application, timeout=30):
    if len(content) != document["size"] or hashlib.sha256(content).hexdigest() != document["sha256"]:
        return review_result(document["document_id"], "INTEGRITY", "Размер или контрольная сумма файла не совпали. Требуется повторная проверка загрузки.")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_parse_child, args=(sender, document, content, application))
    process.start()
    sender.close()
    try:
        if receiver.poll(timeout):
            try:
                return receiver.recv()
            except EOFError:
                return review_result(document["document_id"], "PARSER_FAILED", "Обработчик не смог завершить разбор. Нужна ручная проверка.")
        return review_result(document["document_id"], "TIMEOUT", "Разбор превысил лимит времени. Нужна ручная проверка.")
    finally:
        if process.is_alive():
            process.terminate()
        process.join()
        receiver.close()


def run_once(client, worker_id, processor=process_document):
    job = client.claim(worker_id)
    if job is None:
        return False
    try:
        results = []
        for document in job["documents"]:
            client.renew(job["job_id"], job["lease_token"])
            content = client.download(document["download_url"], job["lease_token"])
            results.append(processor(document, content, job.get("application") or {}))
        client.renew(job["job_id"], job["lease_token"])
        client.complete(job["job_id"], job["lease_token"], results,
                        summarize(results, job["documents"], job.get("application") or {}))
    except Exception:
        # Do not log extracted text or propagate credentials from network errors.
        logger.warning("Document job failed: %s", job["job_id"])
        try:
            client.fail(job["job_id"], job["lease_token"], "Сбой получения или сохранения результатов обработки", retryable=True)
        except Exception:
            logger.warning("Could not release job; lease recovery will handle it")
        raise
    return True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Backwards compatibility: explicit --stage keeps the local demo/stub worker CLI.
    if any(arg == "--stage" or arg.startswith("--stage=") for arg in argv):
        from .stub_worker import main as stub_main

        return stub_main(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    # Fail fast for a misconfigured environment, rather than marking every file unreadable.
    import pypdf, openpyxl, defusedxml  # noqa: F401
    client = QueueClient()
    logging.basicConfig(level=logging.INFO)
    worker_id = f"local-text-{socket.gethostname()}-{os.getpid()}"
    while True:
        try:
            processed = run_once(client, worker_id)
        except Exception:
            if args.once:
                raise SystemExit(1)
            processed = False
        if args.once:
            return
        if not processed:
            time.sleep(3)


# Local demo/stub worker API retained for existing tests and callers.
# The real extraction pipeline remains process_document/run_once above.
from .stub_worker import (  # noqa: E402,F401
    ConfigError,
    Worker,
    WorkerConfig,
    load_config,
)


if __name__ == "__main__":
    main()
