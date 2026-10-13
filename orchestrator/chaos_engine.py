"""ChaosEngine: creates LitmusChaos ChaosEngine CRs and collects ChaosResult verdicts."""
import asyncio
import logging
import sys
import time
from dataclasses import dataclass

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from config import Config

log = logging.getLogger(__name__)

GROUP, VERSION = "litmuschaos.io", "v1alpha1"

# Experiments the orchestrator knows how to run (name -> env passed to the Litmus experiment).
# The default set (design doc 5.3) is pod-delete, pod-cpu-hog-exec, pod-network-latency; choose another
# subset with the EXPERIMENTS env var. pod-network-latency needs the netem kernel module (tc); where the
# node kernel lacks it (e.g. Docker Desktop on WSL2) use pod-http-latency, which injects latency in a proxy.
CATALOG: dict[str, dict[str, str]] = {
    "pod-delete": {"CHAOS_INTERVAL": "20", "FORCE": "false"},
    "pod-cpu-hog-exec": {"CPU_CORES": "2"},
    "pod-network-latency": {"NETWORK_LATENCY": "1000", "NETWORK_INTERFACE": "eth0"},
    "pod-http-latency": {"LATENCY": "1000", "NETWORK_INTERFACE": "eth0"},  # + TARGET_SERVICE_PORT
    "pod-memory-hog-exec": {"MEMORY_CONSUMPTION": "200"},
}
TERMINAL_VERDICTS = ("Pass", "Fail", "Error", "Stopped")  # Litmus also reports Awaited while running


@dataclass
class ExperimentResult:
    name: str
    duration: int
    verdict: str  # Pass | Fail | Timeout | Error


class ChaosEngine:
    def __init__(self, cfg: Config, custom_api: client.CustomObjectsApi | None = None, experiments=None):
        self.cfg = cfg
        self.api = custom_api or client.CustomObjectsApi()
        self.experiments = experiments or self._from_catalog(cfg)
        self.results: list[ExperimentResult] = []  # filled as experiments finish (partial results on cancel)

    @staticmethod
    def _from_catalog(cfg: Config) -> list[dict]:
        out = []
        for name in (n.strip() for n in cfg.experiments.split(",") if n.strip()):
            if name not in CATALOG:
                raise ValueError(f"unknown experiment {name!r}; known: {', '.join(sorted(CATALOG))}")
            env = dict(CATALOG[name])
            if name == "pod-http-latency":
                env["TARGET_SERVICE_PORT"] = str(cfg.chaos_app_port)
            out.append({"name": name, "duration": cfg.experiment_duration_seconds, "env": env})
        return out

    def check_crds(self, api_ext: client.ApiextensionsV1Api | None = None) -> None:
        """Fatal exit if the LitmusChaos CRDs are not registered."""
        api_ext = api_ext or client.ApiextensionsV1Api()
        for plural in ("chaosengines", "chaosresults"):
            try:
                api_ext.read_custom_resource_definition(f"{plural}.{GROUP}")
            except ApiException as exc:
                log.critical("LitmusChaos CRD %s.%s not available (%s); exiting", plural, GROUP, exc.status)
                sys.exit(1)

    def _experiment_spec(self, env: list[dict]) -> dict:
        spec: dict = {"components": {"env": env}}
        if probes := self._probes():
            spec["probe"] = probes
        return spec

    def _probes(self) -> list[dict]:
        """Continuous availability probe: the experiment fails if the app stops answering during chaos."""
        if not self.cfg.chaos_probe_url:
            return []
        return [{
            "name": "app-available", "type": "httpProbe", "mode": "Continuous",
            "httpProbe/inputs": {
                "url": self.cfg.chaos_probe_url, "insecureSkipVerify": False,
                "method": {"get": {"criteria": "==", "responseCode": "200"}},
            },
            "runProperties": {"probeTimeout": "3s", "interval": "1s", "retry": 1, "stopOnFailure": False},
        }]

    def _engine_body(self, engine_name: str, exp: dict) -> dict:
        env = [{"name": "TOTAL_CHAOS_DURATION", "value": str(exp["duration"])}]
        env += [{"name": k, "value": v} for k, v in exp["env"].items()]
        return {
            "apiVersion": f"{GROUP}/{VERSION}",
            "kind": "ChaosEngine",
            "metadata": {"name": engine_name, "namespace": self.cfg.namespace},
            "spec": {
                "engineState": "active",
                "annotationCheck": "false",
                "appinfo": {
                    "appns": self.cfg.chaos_app_namespace,
                    "applabel": self.cfg.chaos_app_label,
                    "appkind": "rollout",
                },
                "chaosServiceAccount": self.cfg.chaos_service_account,
                "experiments": [{"name": exp["name"], "spec": self._experiment_spec(env)}],
            },
        }

    async def _run_one(self, exp: dict) -> tuple[ExperimentResult, str | None]:
        name = exp["name"]
        res = lambda verdict: ExperimentResult(name, exp["duration"], verdict)  # noqa: E731
        engine_name = f"chaos-gate-{name}-{int(time.time())}"
        try:
            await asyncio.to_thread(
                self.api.create_namespaced_custom_object,
                GROUP, VERSION, self.cfg.namespace, "chaosengines",
                body=self._engine_body(engine_name, exp),
            )
        except ApiException as exc:
            log.error("Failed to create ChaosEngine for %s: %s", name, exc)
            return res("Error"), f"could not create ChaosEngine for {name}: {exc.reason or exc.status}"

        result_name = f"{engine_name}-{name}"
        deadline = time.monotonic() + self.cfg.chaos_result_timeout_seconds
        try:
            outcome = await self._wait_for_verdict(name, exp, result_name, deadline)
        except asyncio.CancelledError:
            await asyncio.shield(self._delete_engine(engine_name))
            raise
        await self._delete_engine(engine_name)  # verdict recorded; don't leave engines piling up
        return outcome

    async def _delete_engine(self, engine_name: str) -> None:
        try:
            await asyncio.to_thread(
                self.api.delete_namespaced_custom_object,
                GROUP, VERSION, self.cfg.namespace, "chaosengines", engine_name,
            )
            log.info("Deleted ChaosEngine %s", engine_name)
        except ApiException as exc:
            log.warning("Could not delete ChaosEngine %s: %s", engine_name, exc.status)

    async def _wait_for_verdict(self, name, exp, result_name, deadline):
        res = lambda verdict: ExperimentResult(name, exp["duration"], verdict)  # noqa: E731
        while time.monotonic() < deadline:
            try:
                obj = await asyncio.to_thread(
                    self.api.get_namespaced_custom_object,
                    GROUP, VERSION, self.cfg.namespace, "chaosresults", result_name,
                )
                exp_status = obj.get("status", {}).get("experimentStatus", {})
                verdict = exp_status.get("verdict")
                if verdict in TERMINAL_VERDICTS:
                    log.info("%s verdict: %s", name, verdict)
                    if verdict == "Pass":
                        return res(verdict), None
                    detail = (exp_status.get("errorOutput") or {}).get("reason")
                    return res(verdict), f"{name} returned {verdict}" + (f": {detail}" if detail else "")
            except ApiException as exc:
                if exc.status != 404:  # 404 = result not created yet
                    log.warning("Error reading ChaosResult %s: %s", result_name, exc)
            await asyncio.sleep(self.cfg.chaos_result_poll_seconds)

        log.error("ChaosResult %s timed out", result_name)
        return res("Timeout"), f"{name} timed out after {self.cfg.chaos_result_timeout_seconds}s"

    async def cleanup_stale(self) -> int:
        """Delete ChaosEngines left behind by a previous orchestrator process (best effort)."""
        try:
            items = (await asyncio.to_thread(
                self.api.list_namespaced_custom_object, GROUP, VERSION, self.cfg.namespace, "chaosengines",
            ))["items"]
        except ApiException as exc:
            log.warning("Could not list ChaosEngines for cleanup: %s", exc.status)
            return 0
        stale = [i["metadata"]["name"] for i in items if i["metadata"]["name"].startswith("chaos-gate-")]
        for name in stale:
            await self._delete_engine(name)
        return len(stale)

    async def run_all(self, on_experiment=None) -> tuple[bool, list[ExperimentResult], str]:
        """Run experiments one at a time; fail fast. Returns (ok, results, reason).

        on_experiment(name, status, verdict) is called with status "Running" before each experiment
        and "Done" (plus the verdict) after it."""
        notify = on_experiment or (lambda *a: None)
        results = self.results = []
        for exp in self.experiments:
            notify(exp["name"], "Running", None)
            result, failure = await self._run_one(exp)
            results.append(result)
            notify(exp["name"], "Done", result.verdict)
            if failure:
                return False, results, failure
        return True, results, "all chaos experiments passed"
