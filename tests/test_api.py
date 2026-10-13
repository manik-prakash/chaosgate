import dataclasses
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from api import create_app
from kube import RolloutNotFound
from runs import RunTracker

EXPS = [{"name": "pod-delete", "duration": 60}]


class FakeKube:
    def __init__(self):
        self.images, self.replicas, self.fail = {}, {}, None

    def list_rollouts(self):
        if self.fail:
            raise RuntimeError(self.fail)
        return [{"name": "web", "phase": "Paused", "step": 1, "image": "x:1"}]

    def set_image(self, name, image):
        if name != "web":
            raise RolloutNotFound(name)
        self.images[name] = image

    def set_replicas(self, name, n):
        if name != "web":
            raise RolloutNotFound(name)
        self.replicas[name] = n

    def rollback(self, name):
        if name != "web":
            raise RolloutNotFound(name)
        self.images[name] = "x:stable"
        return "x:stable"


class FakeController:
    def __init__(self):
        self.calls, self.ok = [], True

    async def promote(self, name):
        self.calls.append(("promote", name))
        return self.ok

    async def abort(self, name):
        self.calls.append(("abort", name))
        return self.ok


@pytest.fixture
async def env(cfg, tmp_path):
    cfg = dataclasses.replace(cfg, report_dir=str(tmp_path / "reports"), static_dir=str(tmp_path / "static"), demo_mode=True)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "web-100.html").write_text('<div class="decision decision-promoted">PROMOTED</div>')
    (tmp_path / "reports" / "web-200.html").write_text('<div class="decision decision-aborted">ABORTED</div>')
    (tmp_path / "reports" / "notes.txt").write_text("secret")
    (tmp_path / "static" / "assets").mkdir(parents=True)
    (tmp_path / "static" / "index.html").write_text("<html>SPA</html>")
    (tmp_path / "static" / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside")
    kube, ctl, tracker = FakeKube(), FakeController(), RunTracker()
    probe = {"value": 0.004}
    app = create_app(cfg, tracker, ctl, kube, EXPS, slo_probe=lambda: probe["value"])
    kube.probe = probe
    async with TestClient(TestServer(app)) as client:
        yield client, kube, ctl, tracker, cfg


async def test_state(env):
    client, *_ = env
    r = await client.get("/api/state")
    body = await r.json()
    assert r.status == 200
    assert body["rollouts"][0]["name"] == "web"
    assert body["config"]["error_budget_threshold"] == 0.02 and body["config"]["experiments"] == EXPS
    assert "prometheus_url" not in json.dumps(body)  # only a safe subset of config


async def test_state_kubernetes_error_is_502_json(env):
    client, kube, *_ = env
    kube.fail = "boom"
    r = await client.get("/api/state")
    assert r.status == 502 and "boom" in (await r.json())["error"]


async def test_runs_and_single_run(env):
    client, _, _, tracker, _ = env
    rid = tracker.start("web", "default", "h", EXPS, 0.02, 180)
    assert [x["id"] for x in await (await client.get("/api/runs")).json()] == [rid]
    assert (await (await client.get(f"/api/runs/{rid}")).json())["rollout"] == "web"
    assert (await client.get("/api/runs/nope")).status == 404
    state = await (await client.get("/api/state")).json()
    assert [r["id"] for r in state["active_runs"]] == [rid]


async def test_reports_list_and_serve(env):
    client, *_ = env
    listing = await (await client.get("/api/reports")).json()
    assert [(x["file"], x["decision"]) for x in listing] == [("web-200.html", "ABORTED"), ("web-100.html", "PROMOTED")]
    r = await client.get("/reports/web-100.html")
    assert r.status == 200 and "PROMOTED" in await r.text()


@pytest.mark.parametrize("path", ["/reports/notes.txt", "/reports/missing.html", "/reports/..%2fsecret.txt"])
async def test_report_traversal_and_other_files_rejected(env, path):
    client, *_ = env
    assert (await client.get(path)).status == 404


async def test_deploy(env):
    client, kube, *_ = env
    r = await client.post("/api/rollouts/web/deploy", json={"image": "demo-backend:v3-bad"})
    assert r.status == 200 and kube.images == {"web": "demo-backend:v3-bad"}
    assert (await client.post("/api/rollouts/ghost/deploy", json={"image": "a:b"})).status == 404


@pytest.mark.parametrize("image", ["", "a b", "x;rm -rf /", None, 5, "-bad"])
async def test_deploy_rejects_bad_image(env, image):
    client, kube, *_ = env
    r = await client.post("/api/rollouts/web/deploy", json={"image": image})
    assert r.status == 400 and kube.images == {}


async def test_deploy_requires_json(env):
    client, *_ = env
    assert (await client.post("/api/rollouts/web/deploy", data="nope")).status == 400


async def test_manual_promote_and_abort(env):
    client, _, ctl, *_ = env
    assert (await client.post("/api/rollouts/web/promote")).status == 200
    assert (await client.post("/api/rollouts/web/abort")).status == 200
    assert ctl.calls == [("promote", "web"), ("abort", "web")]
    ctl.ok = False
    assert (await client.post("/api/rollouts/web/abort")).status == 502


async def test_demo_replicas_validation_and_gate(env):
    client, kube, _, _, cfg = env
    assert (await client.post("/api/demo/rollouts/web/replicas", json={"replicas": 1})).status == 200
    assert kube.replicas == {"web": 1}
    for bad in (0, 6, "2", True, None):
        assert (await client.post("/api/demo/rollouts/web/replicas", json={"replicas": bad})).status == 400


async def test_demo_endpoint_disabled_outside_demo_mode(cfg, tmp_path):
    cfg = dataclasses.replace(cfg, demo_mode=False, static_dir=str(tmp_path))
    app = create_app(cfg, RunTracker(), FakeController(), FakeKube(), EXPS)
    async with TestClient(TestServer(app)) as client:
        assert (await client.post("/api/demo/rollouts/web/replicas", json={"replicas": 1})).status == 403


async def test_cross_origin_post_refused(env):
    client, kube, *_ = env
    r = await client.post("/api/rollouts/web/abort", headers={"Origin": "http://evil.example"})
    assert r.status == 403
    r = await client.post("/api/rollouts/web/promote", headers={"Origin": f"http://{client.server.host}:{client.server.port}"})
    assert r.status == 200


async def test_spa_serving(env):
    client, *_ = env
    assert "SPA" in await (await client.get("/")).text()
    assert "SPA" in await (await client.get("/rollouts")).text()  # client-side route falls back to index
    assert "console.log" in await (await client.get("/assets/app.js")).text()
    assert (await client.get("/api/nothing")).status == 404
    assert "outside" not in await (await client.get("/..%2fsecret.txt")).text()


async def test_events_stream_snapshot_then_updates(env):
    client, _, _, tracker, _ = env
    rid = tracker.start("web", "default", "h", EXPS, 0.02, 180)
    resp = await client.get("/api/events")
    assert resp.headers["Content-Type"].startswith("text/event-stream")

    async def next_event():
        name = data = None
        while True:
            line = (await resp.content.readline()).decode().strip()
            if line.startswith("event:"):
                name = line.split(": ", 1)[1]
            elif line.startswith("data:"):
                data = json.loads(line.split(": ", 1)[1])
            elif line == "" and name:
                return name, data

    name, data = await next_event()
    assert name == "snapshot" and data[0]["id"] == rid
    tracker.sample(rid, 0.01)
    name, data = await next_event()
    assert name == "run" and data["samples"][0]["burn"] == 0.01
    resp.close()


async def test_state_reports_slo_readiness(env):
    client, kube, *_ = env
    slo = (await (await client.get("/api/state")).json())["slo"]
    assert slo == {"burn": 0.004, "threshold": 0.02, "ready": True}
    kube.probe["value"] = 0.05
    slo = (await (await client.get("/api/state")).json())["slo"]
    assert slo["ready"] is False and slo["burn"] == 0.05
    kube.probe["value"] = None  # no data / Prometheus unreachable: don't block the UI
    slo = (await (await client.get("/api/state")).json())["slo"]
    assert slo["burn"] is None and slo["ready"] is True


async def test_rollback_to_stable(env):
    client, kube, *_ = env
    r = await client.post("/api/rollouts/web/rollback")
    assert r.status == 200 and (await r.json())["image"] == "x:stable"
    assert (await client.post("/api/rollouts/ghost/rollback")).status == 404


async def test_healthz_is_ok_and_needs_no_cluster(cfg, tmp_path):
    class Broken(FakeKube):
        def list_rollouts(self):
            raise RuntimeError("cluster down")

    app = create_app(dataclasses.replace(cfg, static_dir=str(tmp_path)), RunTracker(), FakeController(), Broken(), EXPS)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/healthz")
        assert r.status == 200 and (await r.text()) == "ok"
