"""Steady traffic against the backend Service so the SLO metric always has data."""
import os
import threading
import time
import urllib.request

URL = os.environ.get("TARGET_URL", "http://backend.default.svc.cluster.local/")
RPS = float(os.environ.get("RPS", "20"))
WORKERS = 4


def worker() -> None:
    interval = WORKERS / RPS
    while True:
        started = time.monotonic()
        try:
            urllib.request.urlopen(URL, timeout=2).read()
        except Exception:  # 5xx raises HTTPError; errors are expected from the bad version
            pass
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


for _ in range(WORKERS):
    threading.Thread(target=worker, daemon=True).start()
print(f"loadgen: {RPS} rps -> {URL}", flush=True)
while True:
    time.sleep(3600)
