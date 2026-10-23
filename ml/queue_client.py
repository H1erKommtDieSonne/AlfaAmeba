"""Минимальный клиент pull-очереди для будущего ML/LLM-воркера."""

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class QueueClient:
    def __init__(self, base_url=None, token=None):
        self.base_url = (base_url or os.environ["DJANGO_QUEUE_URL"]).rstrip("/")
        self.token = token or os.environ["LLM_SERVICE_TOKEN"]
        # Local queue traffic must not be sent through an OS HTTP proxy.
        handlers = [NoRedirects]
        if urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
            handlers.append(ProxyHandler({}))
        self.opener = build_opener(*handlers)

    def _request(self, path, *, method="GET", payload=None, lease_token=None):
        headers = {"Authorization": f"Bearer {self.token}"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if lease_token:
            headers["X-Lease-Token"] = lease_token
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            return self.opener.open(request, timeout=60)
        except HTTPError:
            raise

    def claim(self, worker_id):
        with self._request(
            "jobs/claim/",
            method="POST",
            payload={"worker_id": worker_id},
        ) as response:
            if response.status == 204:
                return None
            return json.load(response)

    def download(self, download_url, lease_token):
        target, base = urlsplit(download_url), urlsplit(self.base_url)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc) or not target.path.startswith(base.path.rstrip("/") + "/jobs/"):
            raise ValueError("Download URL outside queue origin")
        request = Request(
            download_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Lease-Token": lease_token,
            },
        )
        with self.opener.open(request, timeout=60) as response:
            content = response.read(20 * 1024 * 1024 + 1)
            if len(content) > 20 * 1024 * 1024:
                raise ValueError("Document exceeds download limit")
            return content

    def renew(self, job_id, lease_token):
        with self._request(f"jobs/{job_id}/renew/", method="POST", payload={"lease_token": lease_token}) as response:
            return json.load(response)

    def complete(self, job_id, lease_token, documents, summary=None):
        with self._request(
            f"jobs/{job_id}/complete/",
            method="POST",
            payload={
                "lease_token": lease_token,
                "documents": documents,
                "summary": summary or {},
            },
        ) as response:
            return json.load(response)

    def fail(self, job_id, lease_token, error, *, retryable=True):
        with self._request(
            f"jobs/{job_id}/fail/",
            method="POST",
            payload={
                "lease_token": lease_token,
                "error": error,
                "retryable": retryable,
            },
        ) as response:
            return json.load(response)
