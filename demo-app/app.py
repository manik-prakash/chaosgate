"""Tiny demo backend for the chaos-gated pipeline.

GET /         -> 200, or 500 for ERROR_RATE of requests (this is the "bug" in v3-bad)
GET /healthz  -> always 200 (probes; not counted in metrics)
GET /metrics  -> Prometheus text format: http_requests_total{service,code,version}
"""
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = os.environ.get("VERSION", "dev")
ERROR_RATE = float(os.environ.get("ERROR_RATE", "0"))
STARTUP_DELAY = float(os.environ.get("STARTUP_DELAY", "0"))
SERVICE = "backend"

_lock = threading.Lock()
_counts: dict[str, int] = {}


def _count(code: int) -> None:
    with _lock:
        _counts[str(code)] = _counts.get(str(code), 0) + 1


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str = "text/plain") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            return self._send(200, "ok")
        if self.path == "/metrics":
            with _lock:
                lines = [
                    "# HELP http_requests_total Requests served, by status code.",
                    "# TYPE http_requests_total counter",
                ]
                lines += [
                    f'http_requests_total{{service="{SERVICE}",code="{c}",version="{VERSION}"}} {n}'
                    for c, n in sorted(_counts.items())
                ]
                # always expose the series so rate() works from the first scrape
                for c in ("200", "500"):
                    if c not in _counts:
                        lines.append(f'http_requests_total{{service="{SERVICE}",code="{c}",version="{VERSION}"}} 0')
            return self._send(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")
        code = 500 if random.random() < ERROR_RATE else 200
        _count(code)
        self._send(code, f"backend {VERSION} {'error' if code == 500 else 'ok'}\n")

    def log_message(self, *args):  # keep container logs quiet
        pass


if __name__ == "__main__":
    time.sleep(STARTUP_DELAY)
    print(f"backend {VERSION} listening on :8080 (error rate {ERROR_RATE})", flush=True)
    ThreadingHTTPServer(("", 8080), Handler).serve_forever()
