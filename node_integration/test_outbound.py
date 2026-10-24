import hashlib
import json
import os
import threading
import time
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import SpooledTemporaryFile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from applications.models import Application, ApplicationVersion
from core.testing import CreditTestCase
from documents.models import ApplicationDocument

from .models import NodeJob
from .outbound import (
    MAX_DOCUMENT_BYTES,
    MAX_RESPONSE_BYTES,
    STALE_SENDING_MARGIN_SECONDS,
    InvalidNodeResponse,
    NodeResponse,
    OutboundConfigError,
    claim_next_job,
    expire_stale_sending,
    load_config,
    parse_success_response,
    prepare_request,
    run_once,
    send_job,
)
from .services import complete_node_job

API_KEY = "test-node-api-key"
CALLBACK_TOKEN = "test-node-callback-token"
SUCCESS_JOB_ID = "a" * 32


class _FakeNodeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []
        self.response_status = 202
        self.response_body = b'{"job_id":"' + SUCCESS_JOB_ID.encode() + b'","status":"queued","files_count":1}'
        self.delay = 0.0


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {key: value for key, value in self.headers.items()},
                "body": body,
            }
        )
        if self.server.delay:
            time.sleep(self.server.delay)
        try:
            self.send_response(self.server.response_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.server.response_body)))
            self.end_headers()
            self.wfile.write(self.server.response_body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def log_message(self, *args, **kwargs):
        pass


class OutboundTestBase(CreditTestCase):
    def setUp(self):
        self.server = _FakeNodeServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _settings(self, **overrides):
        values = {
            "DOCUMENT_NODE_ENABLED": True,
            "DOCUMENT_NODE_URL": self.base_url,
            "DOCUMENT_NODE_API_KEY": API_KEY,
            "DOCUMENT_NODE_CALLBACK_TOKEN": CALLBACK_TOKEN,
            "DOCUMENT_NODE_TIMEOUT_SECONDS": 2,
            "DOCUMENT_NODE_POLL_SECONDS": 1,
        }
        values.update(overrides)
        return self.settings(**values)

    def _make_version(self, number=1):
        application = Application.objects.create(
            organization=self.organization,
            organization_name=self.organization.name,
            inn=self.organization.inn,
            created_by=self.business_user,
            status=Application.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        return ApplicationVersion.objects.create(
            application=application,
            version_number=number,
            product=self.product,
            amount="1500000.00",
            term_months=12,
            purpose="Закупка сезонной партии товара на три месяца",
            submitted_at=timezone.now(),
            snapshot={"source": "test"},
        )

    def _add_document(
        self,
        version,
        name="statement.pdf",
        content=b"%PDF-1.4 synthetic statement",
        *,
        size=None,
        sha256=None,
        content_type="application/pdf",
    ):
        upload = SimpleUploadedFile(name, content, content_type=content_type)
        return ApplicationDocument.objects.create(
            version=version,
            file=upload,
            original_name=name,
            requirement_code="statement",
            content_type=content_type,
            size=len(content) if size is None else size,
            sha256=hashlib.sha256(content).hexdigest() if sha256 is None else sha256,
        )

    def _make_job(self, *, documents=1, status=NodeJob.Status.PENDING, number=1, **kwargs):
        version = self._make_version(number=number)
        for index in range(documents):
            self._add_document(
                version,
                name=f"document-{index}.pdf",
                content=f"%PDF-1.4 synthetic {index}".encode(),
            )
        return NodeJob.objects.create(version=version, status=status, **kwargs)

    def _set_response(self, status=202, payload=None, *, body=None, delay=0.0):
        self.server.response_status = status
        if body is not None:
            self.server.response_body = body
        elif payload is not None:
            self.server.response_body = json.dumps(payload).encode()
        self.server.delay = delay

    def _success_payload(self, files_count=1, job_id=SUCCESS_JOB_ID):
        return {"job_id": job_id, "status": "queued", "files_count": files_count}

    def _multipart_parts(self, request):
        content_type = request["headers"]["Content-Type"]
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + content_type.encode()
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + request["body"]
        )
        return list(message.iter_parts())


class OutboundSuccessTests(OutboundTestBase):
    def test_success_sends_real_multipart_and_marks_waiting(self):
        job = self._make_job(documents=2)
        self._add_document(
            job.version,
            name="retained estimate.csv",
            content=b"item,amount\nworks,100\n",
            content_type="text/csv",
        )
        # Третий документ делает набор полным, включая унаследованную версию.
        self._set_response(payload=self._success_payload(files_count=3))

        with self._settings():
            sent = run_once()

        self.assertEqual(sent.pk, job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.WAITING)
        self.assertEqual(job.external_job_id, SUCCESS_JOB_ID)
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.claimed_at)
        self.assertIsNone(job.completed_at)
        self.assertEqual(job.last_error, "")
        self.assertEqual(job.version.documents.count(), 3)

        self.assertEqual(len(self.server.requests), 1)
        request = self.server.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/jobs")
        self.assertEqual(request["headers"]["X-API-Key"], API_KEY)
        self.assertNotIn(CALLBACK_TOKEN, request["body"].decode("utf-8", "ignore"))
        self.assertTrue(request["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))

        parts = self._multipart_parts(request)
        names = [part.get_param("name", header="content-disposition") for part in parts]
        self.assertEqual(
            names, ["system_prompt", "application_context", "files", "files", "files"]
        )
        files = [part for part in parts if part.get_param("name", header="content-disposition") == "files"]
        # Порядок файлов не является частью контракта узла: на Windows created_at
        # может совпасть, и порядок по UUID-тай-брейку становится произвольным.
        # Проверяем полноту и связь «имя → содержимое» без учёта порядка, с кратностью.
        self.assertCountEqual(
            [(part.get_filename(), part.get_payload(decode=True)) for part in files],
            [
                ("document-0.pdf", b"%PDF-1.4 synthetic 0"),
                ("document-1.pdf", b"%PDF-1.4 synthetic 1"),
                ("retained estimate.csv", b"item,amount\nworks,100\n"),
            ],
        )
        prompt = parts[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("Не принимай кредитных решений", prompt)
        self.assertIn(str(job.version.application_id), prompt)
        self.assertIn(str(job.version.pk), prompt)
        self.assertEqual(
            parts[1].get_param("name", header="content-disposition"),
            "application_context",
        )
        context = json.loads(parts[1].get_payload(decode=True).decode("utf-8"))
        self.assertEqual(context["schema_version"], 2)
        self.assertEqual(context["application_id"], str(job.version.application_id))
        # Сумма и срок — с самой версии, тип — по продукту (контракт v2).
        self.assertEqual(context["loan_amount_rub"], 1500000.0)
        self.assertEqual(context["term_months"], 12)
        self.assertEqual(context["loan_type"], "working_capital")

    def test_success_is_order_independent_when_created_at_ties(self):
        # Регрессия: одинаковый created_at у документов версии (типично для
        # Windows, точность ~15 мс) делает порядок по UUID произвольным.
        # Отправка обязана остаться полной; проверяем пары без учёта порядка.
        job = self._make_job(documents=1)
        self._add_document(
            job.version, name="a-first.csv", content=b"a,1\n", content_type="text/csv"
        )
        self._add_document(
            job.version, name="b-second.csv", content=b"b,2\n", content_type="text/csv"
        )
        ApplicationDocument.objects.filter(version=job.version).update(
            created_at=timezone.now()
        )
        self._set_response(payload=self._success_payload(files_count=3))

        with self._settings():
            run_once()

        parts = self._multipart_parts(self.server.requests[0])
        names = [part.get_param("name", header="content-disposition") for part in parts]
        self.assertEqual(
            names, ["system_prompt", "application_context", "files", "files", "files"]
        )
        files = [part for part in parts if part.get_param("name", header="content-disposition") == "files"]
        self.assertCountEqual(
            [(part.get_filename(), part.get_payload(decode=True)) for part in files],
            [
                ("document-0.pdf", b"%PDF-1.4 synthetic 0"),
                ("a-first.csv", b"a,1\n"),
                ("b-second.csv", b"b,2\n"),
            ],
        )

    def test_configured_base_path_is_preserved(self):
        self._make_job()
        self._set_response(payload=self._success_payload())
        with self._settings(DOCUMENT_NODE_URL=f"{self.base_url}/api/v1"):
            job = run_once()
        self.assertEqual(job.status, NodeJob.Status.WAITING)
        self.assertEqual(self.server.requests[0]["path"], "/api/v1/jobs")

    def test_success_is_atomic_and_does_not_touch_other_fields(self):
        job = self._make_job()
        original_version_id = job.version_id
        self._set_response(payload=self._success_payload())
        with self._settings():
            run_once()
        job.refresh_from_db()
        self.assertEqual(job.version_id, original_version_id)
        self.assertEqual(job.status, NodeJob.Status.WAITING)
        self.assertIsNone(job.result)
        self.assertIsNone(job.completed_at)

    def test_direct_http_client_ignores_proxy_environment(self):
        self._make_job()
        self._set_response(payload=self._success_payload())
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "ALL_PROXY": "http://127.0.0.1:1",
            },
        ):
            with self._settings():
                job = run_once()
        self.assertEqual(job.status, NodeJob.Status.WAITING)
        self.assertEqual(len(self.server.requests), 1)

    def test_external_id_clash_is_uncertain_and_preserves_existing_job(self):
        other = self._make_job(number=1)
        other.external_job_id = "b" * 32
        other.status = NodeJob.Status.WAITING
        other.save(update_fields=("external_job_id", "status"))
        job = self._make_job(number=1)
        self._set_response(payload=self._success_payload(job_id="b" * 32))

        with self._settings():
            run_once()

        job.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.UNCERTAIN)
        self.assertIsNone(job.external_job_id)
        self.assertIn("идентификатор", job.last_error.lower())
        self.assertEqual(other.status, NodeJob.Status.WAITING)
        self.assertEqual(other.external_job_id, "b" * 32)

    def test_completed_callback_during_network_is_not_clobbered(self):
        external_id = "c" * 32
        job = self._make_job(
            status=NodeJob.Status.SENDING,
            external_job_id=external_id,
            attempts=1,
            claimed_at=timezone.now(),
        )
        payload = {
            "job_id": external_id,
            "status": "completed",
            "result": {"summary": "callback already completed"},
            "files": [],
            "verification_status": "needs_review",
        }

        def fake_post(config, prepared):
            complete_node_job(payload)
            return NodeResponse(
                status=202,
                body=json.dumps(self._success_payload(job_id=external_id)).encode(),
            )

        with self._settings():
            config = load_config()
        with patch("node_integration.outbound.post_job", side_effect=fake_post):
            send_job(job, config)

        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.COMPLETED)
        self.assertEqual(job.result["result"]["summary"], "callback already completed")
        self.assertEqual(job.external_job_id, external_id)
        self.assertIsNotNone(job.completed_at)

    def test_prepare_request_uses_spooled_multipart(self):
        job = self._make_job()
        self._add_document(
            job.version,
            name="large.pdf",
            content=b"x" * (1024 * 1024 + 100),
        )
        prepared = prepare_request(job)
        try:
            self.assertIsInstance(prepared.body, SpooledTemporaryFile)
            self.assertTrue(prepared.body._rolled)
            self.assertEqual(prepared.files_count, 2)
            self.assertGreater(prepared.size, 1024 * 1024)
            self.assertIn("извлечение", prepared.system_prompt)
        finally:
            prepared.body.close()


class OutboundErrorTests(OutboundTestBase):
    def test_known_4xx_marks_failed_without_retry(self):
        for status in (400, 401, 403, 404, 409, 413, 415, 422, 429):
            with self.subTest(status=status):
                job = self._make_job()
                self._set_response(status=status, body=b'{"error":"rejected"}')
                with self._settings():
                    run_once()
                job.refresh_from_db()
                self.assertEqual(job.status, NodeJob.Status.FAILED)
                self.assertEqual(job.attempts, 1)
                self.assertIn(f"HTTP {status}", job.last_error)

                request_count = len(self.server.requests)
                with self._settings():
                    self.assertIsNone(run_once())
                self.assertEqual(len(self.server.requests), request_count)
                job.refresh_from_db()
                self.assertEqual(job.status, NodeJob.Status.FAILED)

    def test_5xx_redirect_and_unexpected_2xx_are_uncertain(self):
        for status in (500, 503, 302, 200, 204):
            with self.subTest(status=status):
                job = self._make_job()
                self._set_response(status=status, payload=self._success_payload())
                with self._settings():
                    run_once()
                job.refresh_from_db()
                self.assertEqual(job.status, NodeJob.Status.UNCERTAIN)
                self.assertIsNone(job.external_job_id)
                self.assertNotEqual(job.status, NodeJob.Status.PENDING)

    def test_network_timeout_is_uncertain_and_not_retried(self):
        job = self._make_job()
        self._set_response(payload=self._success_payload(), delay=1.5)
        with self._settings(DOCUMENT_NODE_TIMEOUT_SECONDS=1):
            run_once()
        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.UNCERTAIN)
        self.assertIn("timeout", job.last_error.lower())
        self.assertEqual(job.attempts, 1)
        request_count = len(self.server.requests)
        with self._settings(DOCUMENT_NODE_TIMEOUT_SECONDS=1):
            self.assertIsNone(run_once())
        self.assertEqual(len(self.server.requests), request_count)

    def test_malformed_202_responses_are_uncertain(self):
        bodies = [
            b"not json",
            b'{"job_id":"' + b"A" * 32 + b'","status":"queued","files_count":1}',
            b'{"job_id":"' + b"a" * 32 + b'","status":"waiting","files_count":1}',
            b'{"job_id":"' + b"a" * 32 + b'","status":"queued","files_count":2}',
            b'{"job_id":"' + b"a" * 32 + b'","status":"queued","files_count":true}',
            b'{"job_id":"' + b"a" * 32 + b'","job_id":"' + b"b" * 32
            + b'","status":"queued","files_count":1}',
            b"[]",
            b'{"job_id":"' + b"a" * 32 + b'","status":"queued","files_count":1,"x":NaN}',
            b"x" * (MAX_RESPONSE_BYTES + 1),
        ]
        for body in bodies:
            with self.subTest(body=body[:40]):
                job = self._make_job()
                self._set_response(status=202, body=body)
                with self._settings():
                    run_once()
                job.refresh_from_db()
                self.assertEqual(job.status, NodeJob.Status.UNCERTAIN)
                self.assertIsNone(job.external_job_id)
                self.assertTrue(job.last_error)

    def test_parse_success_response_rejects_duplicate_keys(self):
        response = NodeResponse(
            status=202,
            body=(
                b'{"job_id":"' + b"a" * 32 + b'","status":"queued","files_count":1,'
                b'"files_count":1}'
            ),
        )
        with self.assertRaises(InvalidNodeResponse):
            parse_success_response(response, 1)

    def test_invalid_documents_fail_before_any_network_request(self):
        def no_documents(version):
            return None

        def six_documents(version):
            for index in range(6):
                self._add_document(version, name=f"d{index}.pdf", content=b"x")

        def bad_extension(version):
            self._add_document(version, name="malware.exe", content=b"x")

        def empty_file(version):
            self._add_document(version, name="empty.pdf", content=b"")

        def oversized(version):
            self._add_document(
                version, name="big.pdf", content=b"x" * (MAX_DOCUMENT_BYTES + 1)
            )

        def size_mismatch(version):
            self._add_document(version, name="size.pdf", content=b"x", size=2)

        def sha_mismatch(version):
            self._add_document(version, name="sha.pdf", content=b"x", sha256="0" * 64)

        def missing_file(version):
            ApplicationDocument.objects.create(
                version=version,
                file="applications/does-not-exist.pdf",
                original_name="missing.pdf",
                requirement_code="statement",
                content_type="application/pdf",
                size=10,
                sha256="0" * 64,
            )

        def retained_is_also_validated(version):
            self._add_document(version, name="good.pdf", content=b"good")
            self._add_document(version, name="retained.pdf", content=b"bad", sha256="0" * 64)

        cases = [
            no_documents,
            six_documents,
            bad_extension,
            empty_file,
            oversized,
            size_mismatch,
            sha_mismatch,
            missing_file,
            retained_is_also_validated,
        ]
        for builder in cases:
            with self.subTest(case=builder.__name__):
                version = self._make_version()
                builder(version)
                job = NodeJob.objects.create(version=version)
                document_count = version.documents.count()
                request_count = len(self.server.requests)
                with self._settings():
                    run_once()
                job.refresh_from_db()
                self.assertEqual(job.status, NodeJob.Status.FAILED)
                self.assertEqual(job.attempts, 1)
                self.assertIsNone(job.external_job_id)
                self.assertTrue(job.last_error)
                self.assertEqual(len(self.server.requests), request_count)
                self.assertEqual(version.documents.count(), document_count)


class OutboundQueueTests(OutboundTestBase):
    def test_claim_is_compare_and_swap_and_does_not_call_network(self):
        first = self._make_job()
        second = self._make_job()
        NodeJob.objects.filter(pk=second.pk).update(
            available_at=timezone.now() + timedelta(hours=1)
        )
        with self._settings():
            claimed = claim_next_job()
        self.assertEqual(claimed.pk, first.pk)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, NodeJob.Status.SENDING)
        self.assertEqual(first.attempts, 1)
        self.assertIsNotNone(first.claimed_at)
        self.assertEqual(second.status, NodeJob.Status.PENDING)
        self.assertEqual(self.server.requests, [])
        with self._settings():
            self.assertIsNone(claim_next_job())

    def test_stale_sending_becomes_uncertain_and_recent_stays(self):
        old = self._make_job(
            status=NodeJob.Status.SENDING,
            attempts=1,
            claimed_at=timezone.now()
            - timedelta(seconds=2 + STALE_SENDING_MARGIN_SECONDS + 1),
        )
        recent = self._make_job(
            status=NodeJob.Status.SENDING,
            attempts=1,
            claimed_at=timezone.now(),
        )
        pending = self._make_job()
        with self._settings(DOCUMENT_NODE_TIMEOUT_SECONDS=2):
            changed = expire_stale_sending()
        self.assertEqual(changed, 1)
        old.refresh_from_db()
        recent.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(old.status, NodeJob.Status.UNCERTAIN)
        self.assertIn("неизвестен", old.last_error)
        self.assertEqual(recent.status, NodeJob.Status.SENDING)
        self.assertEqual(pending.status, NodeJob.Status.PENDING)

        request_count = len(self.server.requests)
        with self._settings(DOCUMENT_NODE_TIMEOUT_SECONDS=2):
            expire_stale_sending()
        self.assertEqual(len(self.server.requests), request_count)
        old.refresh_from_db()
        self.assertEqual(old.status, NodeJob.Status.UNCERTAIN)

    def test_command_once_sends_only_one_job(self):
        first = self._make_job()
        second = self._make_job()
        self._set_response(payload=self._success_payload())
        with self._settings():
            call_command("send_node_jobs", "--once")
        self.assertEqual(len(self.server.requests), 1)
        statuses = set(
            NodeJob.objects.filter(pk__in=(first.pk, second.pk)).values_list(
                "status", flat=True
            )
        )
        self.assertEqual(statuses, {NodeJob.Status.WAITING, NodeJob.Status.PENDING})

    def test_command_once_without_jobs_does_not_send(self):
        with self._settings():
            call_command("send_node_jobs", "--once")
        self.assertEqual(self.server.requests, [])

    def test_invalid_config_leaves_job_pending(self):
        job = self._make_job()
        cases = [
            {"DOCUMENT_NODE_ENABLED": False},
            {"DOCUMENT_NODE_URL": "ftp://127.0.0.1:1"},
            {"DOCUMENT_NODE_URL": "http://user:password@127.0.0.1:1"},
            {"DOCUMENT_NODE_URL": f"{self.base_url}/?query=1"},
            {"DOCUMENT_NODE_URL": f"{self.base_url}/#fragment"},
            {"DOCUMENT_NODE_URL": "http:///jobs"},
            {"DOCUMENT_NODE_API_KEY": ""},
            {"DOCUMENT_NODE_API_KEY": "   "},
            {"DOCUMENT_NODE_CALLBACK_TOKEN": ""},
            {"DOCUMENT_NODE_CALLBACK_TOKEN": "  "},
            {"DOCUMENT_NODE_TIMEOUT_SECONDS": 0},
            {"DOCUMENT_NODE_TIMEOUT_SECONDS": -1},
            {"DOCUMENT_NODE_POLL_SECONDS": 0},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self._settings(**overrides):
                    with self.assertRaises(OutboundConfigError):
                        load_config()
                    with self.assertRaises(CommandError):
                        call_command("send_node_jobs", "--once")
                job.refresh_from_db()
                self.assertEqual(job.status, NodeJob.Status.PENDING)
                self.assertEqual(job.attempts, 0)
                self.assertEqual(self.server.requests, [])

    @override_settings(DOCUMENT_NODE_ENABLED=False)
    def test_disabled_by_default_does_not_claim(self):
        job = self._make_job()
        with self.assertRaises(OutboundConfigError):
            load_config()
        job.refresh_from_db()
        self.assertEqual(job.status, NodeJob.Status.PENDING)
        self.assertEqual(job.attempts, 0)
