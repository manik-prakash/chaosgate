"""HTTP API + dashboard hosting (aiohttp). JSON under /api, reports under /reports, SPA at /."""
import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

from config import Config
from kube import RolloutNotFound
from reports import list_reports, read_report

log = logging.getLogger(__name__)

IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}$")


def _json(data, status=200) -> web.Response:
    return web.json_response(data, status=status)


def _err(message: str, status: int) -> web.Response:
    return _json({"error": message}, status)


@web.middleware
async def csrf_guard(request: web.Request, handler):
    """No auth in this demo, so at least refuse cross-site state changes."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return _err("cross-origin request refused", 403)
    return await handler(request)


def create_app(cfg: Config, tracker, controller, kube, experiments: list[dict], slo_probe=None) -> web.Application:
    app = web.Application(middlewares=[csrf_guard])
    report_dir = Path(cfg.report_dir)
    static = Path(cfg.static_dir) if cfg.static_dir else Path(__file__).parent / "static"

    async def call_kube(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except RolloutNotFound as exc:
            raise web.HTTPNotFound(text=json.dumps({"error": f"rollout {exc} not found"}),
                                   content_type="application/json") from exc
        except Exception as exc:  # noqa: BLE001 - surface cluster errors to the UI, not a stack trace
            log.exception("Kubernetes call failed")
            raise web.HTTPBadGateway(text=json.dumps({"error": f"kubernetes error: {exc}"}),
                                     content_type="application/json") from exc

    async def healthz(_):
        """Liveness/readiness: the process is up and the event loop answers. Deliberately no cluster calls."""
        return web.Response(text="ok", content_type="text/plain")

    # ---- read ----------------------------------------------------------
    async def slo_status() -> dict | None:
        if slo_probe is None:
            return None
        try:
            burn = await asyncio.to_thread(slo_probe)
        except Exception:  # noqa: BLE001 - a monitoring hiccup must not break the dashboard
            burn = None
        return {
            "burn": burn,
            "threshold": cfg.error_budget_threshold,
            "ready": burn is None or burn <= cfg.error_budget_threshold,
        }

    async def state(_):
        return _json({
            "rollouts": await call_kube(kube.list_rollouts),
            "slo": await slo_status(),
            "active_runs": [r for r in tracker.all() if r["state"] == "RUNNING"],
            "config": {
                "namespace": cfg.namespace,
                "gate_step_index": cfg.chaos_gate_step_index,
                "slo_service": cfg.slo_service_name,
                "error_budget_threshold": cfg.error_budget_threshold,
                "monitoring_window_seconds": cfg.chaos_duration_seconds,
                "poll_interval_seconds": cfg.poll_interval_seconds,
                "max_concurrent_gates": cfg.max_concurrent_gates,
                "chaos_app_label": cfg.chaos_app_label,
                "experiments": experiments,
                "grafana_url": cfg.grafana_dashboard_url,
                "demo_mode": cfg.demo_mode,
            },
        })

    async def runs(_):
        return _json(tracker.all())

    async def run_one(request):
        run = tracker.get(request.match_info["id"])
        return _json(run) if run else _err("run not found", 404)

    async def events(request):
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })
        await resp.prepare(request)
        q = tracker.subscribe()
        try:
            await resp.write(f"event: snapshot\ndata: {json.dumps(tracker.all())}\n\n".encode())
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
                    continue
                await resp.write(f"event: run\ndata: {json.dumps(ev['run'])}\n\n".encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            tracker.unsubscribe(q)
        return resp

    async def reports(_):
        return _json(list_reports(report_dir))

    async def report(request):
        body = read_report(report_dir, request.match_info["name"])
        if body is None:
            return web.Response(status=404, text="not found", content_type="text/plain")
        return web.Response(text=body, content_type="text/html")

    # ---- control ------------------------------------------------------------
    async def read_json(request) -> dict:
        try:
            data = await request.json()
        except ValueError as exc:
            raise web.HTTPBadRequest(text=json.dumps({"error": "body must be JSON"}),
                                     content_type="application/json") from exc
        return data if isinstance(data, dict) else {}

    async def deploy(request):
        name = request.match_info["name"]
        image = (await read_json(request)).get("image", "")
        if not isinstance(image, str) or not IMAGE.match(image):
            return _err("invalid image reference", 400)
        await call_kube(kube.set_image, name, image)
        log.info("Dashboard: deploy %s -> %s", name, image)
        return _json({"ok": True, "rollout": name, "image": image})

    async def promote(request):
        name = request.match_info["name"]
        ok = await controller.promote(name)
        log.info("Dashboard: manual promote %s -> %s", name, ok)
        return _json({"ok": ok}, 200 if ok else 502)

    async def abort(request):
        name = request.match_info["name"]
        ok = await controller.abort(name)
        log.info("Dashboard: manual abort %s -> %s", name, ok)
        return _json({"ok": ok}, 200 if ok else 502)

    async def rollback(request):
        name = request.match_info["name"]
        image = await call_kube(kube.rollback, name)
        log.info("Dashboard: rollback %s -> %s", name, image)
        return _json({"ok": True, "rollout": name, "image": image})

    async def demo_replicas(request):
        if not cfg.demo_mode:
            return _err("demo mode is disabled", 403)
        name = request.match_info["name"]
        n = (await read_json(request)).get("replicas")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 5:
            return _err("replicas must be an integer between 1 and 5", 400)
        await call_kube(kube.set_replicas, name, n)
        return _json({"ok": True, "rollout": name, "replicas": n})

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/runs", runs)
    app.router.add_get("/api/runs/{id}", run_one)
    app.router.add_get("/api/events", events)
    app.router.add_get("/api/reports", reports)
    app.router.add_get("/reports/{name}", report)
    app.router.add_post("/api/rollouts/{name}/deploy", deploy)
    app.router.add_post("/api/rollouts/{name}/promote", promote)
    app.router.add_post("/api/rollouts/{name}/abort", abort)
    app.router.add_post("/api/rollouts/{name}/rollback", rollback)
    app.router.add_post("/api/demo/rollouts/{name}/replicas", demo_replicas)

    # ---- single-page app ------------------------------------------------------
    index = static / "index.html"

    async def spa(request):
        tail = request.match_info["tail"]
        if tail.startswith("api/"):
            return _err("not found", 404)
        if index.is_file():
            candidate = (static / tail).resolve()
            if tail and candidate.is_file() and static.resolve() in candidate.parents:
                return web.FileResponse(candidate)
            return web.FileResponse(index)
        return web.Response(text="Dashboard not built. API is available under /api.", content_type="text/plain")

    app.router.add_get("/{tail:.*}", spa)
    return app
