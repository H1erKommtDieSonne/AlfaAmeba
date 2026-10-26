import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from .worker import main as run_worker

worker = Thread(target=run_worker, name="document-worker", daemon=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        payload = json.dumps(
            {"status": "ok" if worker.is_alive() else "unavailable",
             "service": "local-document-analysis", "worker_alive": worker.is_alive(), "model_loaded": False}
        ).encode()
        self.send_response(200 if worker.is_alive() else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    worker.start()
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
