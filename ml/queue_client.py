"""Минимальный клиент pull-очереди для будущего ML/LLM-воркера."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class QueueClient:
    def __init__(self, base_url=None, token=None):
        self.base_url = (base_url or os.environ["DJANGO_QUEUE_URL"]).rstrip("/")
        self.token = token or os.environ["LLM_SERVICE_TOKEN"]

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
            return urlopen(request, timeout=60)
        except HTTPError:
            raise

    def claim(self, worker_id):
        response = self._request(
            "jobs/claim/",
            method="POST",
            payload={"worker_id": worker_id},
        )
        if response.status == 204:
            return None
        return json.load(response)

    def download(self, download_url, lease_token):
        request = Request(
            download_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Lease-Token": lease_token,
            },
        )
        with urlopen(request, timeout=120) as response:
            return response.read()

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
