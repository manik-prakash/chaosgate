#!/usr/bin/env python3
"""End-to-end acceptance test against a running demo cluster (stdlib only).

    bash scripts/setup-demo.sh && bash scripts/open-dashboard.sh   # in another terminal
    python scripts/e2e.py                                           # ~12 minutes

It drives the same dashboard API the UI uses and asserts, for each scenario:
  * the gate's decision and the reason it gives,
  * the Argo Rollout's final state (Healthy at 100% / aborted),
  * that a report exists for the run and shows the same decision.

Environment: API (default http://localhost:8080), RUN_TIMEOUT seconds per gate run (default 600).
Exit code 0 only if every scenario passed.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("API", "http://localhost:8080").rstrip("/")
RUN_TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "600"))
ROLLOUT = "backend"


def call(path: str, body: dict | None = None, method: str | None = None):
    data = json.dumps(body or {}).encode() if body is not None or method == "POST" else None
    req = urllib.request.Request(
        API + path, data=data, method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method or 'GET'} {path} -> {exc.code}: {exc.read().decode()[:200]}") from exc


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_for(description: str, predicate, timeout: float, every: float = 3.0):
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:  # noqa: BLE001 - transient API errors are retried
            last_err = exc
        time.sleep(every)
    raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {description}" + (f" ({last_err})" if last_err else ""))


def state() -> dict:
    return call("/api/state")


def rollout() -> dict:
    return state()["rollouts"][0]


def reset() -> None:
    """Back to 3 replicas on the stable version, healthy, with the error budget recovered."""
    log("reset: rolling back to the stable version with 3 replicas")
    call(f"/api/demo/rollouts/{ROLLOUT}/replicas", {"replicas": 3})
    call(f"/api/rollouts/{ROLLOUT}/rollback", {})
    wait_for("rollout Healthy", lambda: rollout()["phase"] == "Healthy" and not rollout()["abort"], 180)
    wait_for("no gate running", lambda: not state()["active_runs"], 60)
    wait_for("error budget recovered", lambda: (state()["slo"] or {}).get("ready", True), 600, every=5)


def other_healthy_image() -> str:
    return "demo-backend:v1" if rollout()["image"] == "demo-backend:v2" else "demo-backend:v2"


def run_scenario(name: str, start, expect: str, reason_contains: str | None, final_phase) -> list[str]:
    """Returns a list of failures (empty = pass)."""
    failures: list[str] = []
    reset()
    known = {r["id"] for r in call("/api/runs")}
    log(f"{name}: starting")
    start()

    def new_run():
        fresh = [r for r in call("/api/runs") if r["id"] not in known]
        return fresh[0] if fresh else None

    wait_for("the gate to start", new_run, 180)
    started = time.monotonic()
    run = wait_for("the gate to finish", lambda: (lambda r: r if r and r["state"] != "RUNNING" else None)(new_run()), RUN_TIMEOUT, every=4)
    log(f"{name}: {run['state']} after {time.monotonic() - started:.0f}s: {run['reason']}")

    if run["decision"] != expect:
        failures.append(f"decision was {run['decision']}, expected {expect}")
    if reason_contains and reason_contains not in (run["reason"] or ""):
        failures.append(f"reason {run['reason']!r} does not contain {reason_contains!r}")
    verdicts = {e["name"]: e["verdict"] for e in run["experiments"]}
    if expect == "PROMOTED" and any(v != "Pass" for v in verdicts.values()):
        failures.append(f"promoted but experiments were {verdicts}")

    try:
        wait_for(final_phase.__doc__, lambda: final_phase(rollout()), 120)
    except TimeoutError as exc:
        failures.append(str(exc))

    reports = {r["file"]: r for r in call("/api/reports")}
    if not run["report"] or run["report"] not in reports:
        failures.append("no report was written for this run")
    else:
        text = call(f"/reports/{run['report']}")
        if expect not in text:
            failures.append(f"report does not show {expect}")
        if reports[run["report"]]["decision"] != expect:
            failures.append(f"report listing says {reports[run['report']]['decision']}")
    return failures


def promoted_and_healthy(r: dict) -> bool:
    """rollout Healthy at 100%"""
    return r["phase"] == "Healthy" and r["step"] == r["total_steps"]


def aborted(r: dict) -> bool:
    """rollout aborted"""
    return r["abort"] and r["updated_replicas"] == 0


def main() -> int:
    try:
        s = wait_for("the dashboard API", state, 45, every=2)  # a fresh port-forward needs a moment
    except TimeoutError as exc:
        print(f"Cannot reach the dashboard API at {API}: {exc}\nRun scripts/open-dashboard.sh first.")
        return 2
    if not s["config"]["demo_mode"]:
        print("DEMO_MODE is off, so the scenarios cannot scale the app. Set DEMO_MODE=true in the ConfigMap.")
        return 2

    scenarios = [
        ("A healthy release", lambda: call(f"/api/rollouts/{ROLLOUT}/deploy", {"image": other_healthy_image()}),
         "PROMOTED", None, promoted_and_healthy),
        ("C fragile deployment (1 replica)",
         lambda: (call(f"/api/demo/rollouts/{ROLLOUT}/replicas", {"replicas": 1}),
                  call(f"/api/rollouts/{ROLLOUT}/deploy", {"image": other_healthy_image()})),
         "ABORTED", "returned Fail", aborted),
        ("B buggy release (HTTP 500s)", lambda: call(f"/api/rollouts/{ROLLOUT}/deploy", {"image": "demo-backend:v3-bad"}),
         "ABORTED", "error budget burned", aborted),
    ]

    results = {}
    for name, start, expect, reason, final in scenarios:
        try:
            results[name] = run_scenario(name, start, expect, reason, final)
        except Exception as exc:  # noqa: BLE001
            results[name] = [f"{type(exc).__name__}: {exc}"]
    try:
        reset()
    except Exception as exc:  # noqa: BLE001
        log(f"final reset failed: {exc}")

    print("\n=== results ===")
    for name, failures in results.items():
        print(f"{'PASS' if not failures else 'FAIL'}  {name}")
        for f in failures:
            print(f"      - {f}")
    return 0 if all(not f for f in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
