"""Entry point and ChaosGatedOrchestrator: wires watcher, chaos, SLO monitor, controller, reports."""
import asyncio
import logging
import signal
from pathlib import Path

from aiohttp import web
from kubernetes import config as k8s_config

from api import create_app
from chaos_engine import ChaosEngine, ExperimentResult
from config import Config
from kube import Kube
from report_generator import GateReport, ReportGenerator
from rollout_controller import RolloutController
from runs import RunTracker
from slo_monitor import SLOMonitor
from watcher import RolloutWatcher

log = logging.getLogger("orchestrator")


def _failed(task: asyncio.Task) -> bool:
    """A finished chaos/SLO task counts as failed if it raised or its first result element is False."""
    if task.cancelled():
        return False
    if task.exception() is not None:
        return True
    return task.result()[0] is False


class ChaosGatedOrchestrator:
    def __init__(self, cfg, watcher, chaos_factory, slo_factory, controller, reporter, tracker=None):
        self.cfg = cfg
        self.watcher = watcher
        self.chaos_factory = chaos_factory
        self.slo_factory = slo_factory
        self.controller = controller
        self.reporter = reporter
        self.tracker = tracker or RunTracker()
        self._semaphore = asyncio.Semaphore(cfg.max_concurrent_gates)
        self._locks: dict[str, asyncio.Lock] = {}

    async def _run_both(self, chaos, slo, run_id):
        """Run chaos and SLO monitoring concurrently; as soon as one fails, cancel the other."""
        chaos_t = asyncio.create_task(
            chaos.run_all(
                on_experiment=lambda name, status, verdict: self.tracker.experiment(run_id, name, status, verdict)
            )
        )
        chaos_done = {"v": False}
        chaos_t.add_done_callback(lambda _: chaos_done.update(v=True))
        slo_t = asyncio.create_task(
            slo.monitor(
                on_sample=lambda burn: self.tracker.sample(run_id, burn),
                keep_going=lambda: not chaos_done["v"],  # watch the budget for as long as faults are injected
            )
        )
        pending = {chaos_t, slo_t}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if any(_failed(t) for t in done):
                break
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return chaos_t, slo_t

    async def run_gate(self, rollout: dict) -> str:
        """GATE_TRIGGERED -> RUNNING -> DECIDING -> PROMOTING/ABORTING. Always writes a report."""
        name = rollout["metadata"]["name"]
        namespace = rollout["metadata"].get("namespace", self.cfg.namespace)
        revision = (rollout.get("status") or {}).get("currentPodHash")
        log.info("Chaos gate triggered for rollout %s", name)
        chaos, slo = self.chaos_factory(), self.slo_factory()
        run_id = self.tracker.start(
            name, namespace, revision, getattr(chaos, "experiments", []),
            self.cfg.error_budget_threshold, self.cfg.chaos_duration_seconds,
        )
        try:
            return await self._execute(rollout, chaos, slo, run_id)
        except Exception as exc:
            # the rollout is left paused (fail-closed); make that visible instead of a run stuck as RUNNING
            self.tracker.fail(run_id, f"internal error: {exc!r}. The rollout was left paused; decide manually")
            raise

    async def _execute(self, rollout: dict, chaos, slo, run_id: str) -> str:
        name = rollout["metadata"]["name"]
        namespace = rollout["metadata"].get("namespace", self.cfg.namespace)
        chaos_t, slo_t = await self._run_both(chaos, slo, run_id)

        experiments, reasons = list(getattr(chaos, "results", [])), []
        chaos_ok = budget_ok = True
        if chaos_t.cancelled():
            chaos_ok = True  # stopped early because the SLO budget failed; not a chaos verdict
        elif chaos_t.exception() is not None:
            log.error("Chaos run crashed: %r", chaos_t.exception())
            chaos_ok, reasons = False, [f"chaos run crashed: {chaos_t.exception()!r}"]
        else:
            chaos_ok, experiments, chaos_reason = chaos_t.result()
            if not chaos_ok:
                reasons.append(chaos_reason)

        max_burn = getattr(slo, "max_burn", 0.0)
        if slo_t.cancelled():
            budget_ok = max_burn <= self.cfg.error_budget_threshold  # stopped early by a chaos failure
        elif slo_t.exception() is not None:
            log.error("SLO monitor crashed: %r", slo_t.exception())
            budget_ok = False
            reasons.append(f"SLO monitor crashed: {slo_t.exception()!r}")
        else:
            budget_ok, max_burn = slo_t.result()
            if not budget_ok:
                first = getattr(slo, "first_burn", None)
                if first is not None and first > self.cfg.error_budget_threshold:
                    reasons.append(
                        f"error budget was already burning before fault injection began ({first:.4f} > "
                        f"{self.cfg.error_budget_threshold}); probably leftover errors from a previous release. "
                        "Wait for the 5-minute SLO window to recover, then retry"
                    )
                else:
                    reasons.append(
                        f"error budget burned {max_burn:.4f} exceeding threshold {self.cfg.error_budget_threshold}"
                    )

        # experiments that never ran still belong in the audit trail
        ran = {e.name for e in experiments}
        for planned in getattr(chaos, "experiments", []):
            if planned["name"] not in ran:
                verdict = "Cancelled" if chaos_t.cancelled() else "Skipped"
                experiments.append(ExperimentResult(planned["name"], planned["duration"], verdict))

        promote = chaos_ok and budget_ok
        decision = "PROMOTED" if promote else "ABORTED"
        no_slo_data = bool(getattr(slo, "polls", 0) and slo.no_data_polls == slo.polls)
        if not promote:
            reason = "; ".join(reasons)
        elif no_slo_data:
            reason = "all chaos experiments passed; no SLO data was available, so the error budget was not verified"
        else:
            reason = "all chaos experiments passed and error budget stayed within threshold"

        patched = await (self.controller.promote(name) if promote else self.controller.abort(name))
        if not patched:
            reason += f" (WARNING: {'promote' if promote else 'abort'} patch failed; manual intervention required)"

        report_name = None
        try:  # the rollout has already been promoted/aborted: a report failure must not lose that fact
            report_path = self.reporter.generate(
                GateReport(
                    rollout=name, namespace=namespace, decision=decision, reason=reason,
                    experiments=experiments, slo_service=self.cfg.slo_service_name,
                    max_burn=max_burn, threshold=self.cfg.error_budget_threshold, slo_ok=budget_ok,
                    slo_no_data=no_slo_data,
                )
            )
            report_name = getattr(report_path, "name", None)
        except Exception:  # noqa: BLE001
            log.exception("Could not write the report for %s (decision %s stands)", name, decision)
        self.tracker.finish(run_id, decision, reason, report_name)
        log.info("Rollout %s: %s (%s)", name, decision, reason)
        return decision

    async def handle(self, rollout: dict) -> None:
        """One gate at a time per rollout name; at most max_concurrent_gates overall."""
        name = rollout["metadata"]["name"]
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock, self._semaphore:
            try:
                await self.run_gate(rollout)
            except Exception:  # noqa: BLE001 - one bad gate must not kill the watcher loop
                log.exception("Gate run for %s failed unexpectedly", name)

    async def run(self) -> None:
        tasks: set[asyncio.Task] = set()
        async for rollout in self.watcher.events():
            task = asyncio.create_task(self.handle(rollout))
            tasks.add(task)
            task.add_done_callback(tasks.discard)


def build(cfg: Config, tracker: RunTracker | None = None) -> ChaosGatedOrchestrator:
    return ChaosGatedOrchestrator(
        cfg,
        watcher=RolloutWatcher(cfg),
        chaos_factory=lambda: ChaosEngine(cfg),
        slo_factory=lambda: SLOMonitor(cfg),
        controller=RolloutController(cfg),
        reporter=ReportGenerator(cfg),
        tracker=tracker,
    )


async def amain() -> None:
    cfg = Config.from_env()
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    ChaosEngine(cfg).check_crds()
    if stale := await ChaosEngine(cfg).cleanup_stale():
        log.warning("Removed %d stale ChaosEngine(s) left by a previous orchestrator run", stale)
    RolloutController(cfg).check_crd()

    tracker = RunTracker(Path(cfg.report_dir) / "runs.json")
    orch = build(cfg, tracker)
    runner = None
    if cfg.http_port >= 0:
        experiments = [{"name": e["name"], "duration": e["duration"]} for e in ChaosEngine(cfg).experiments]
        app = create_app(cfg, tracker, orch.controller, Kube(cfg), experiments, slo_probe=SLOMonitor(cfg).fetch)
        runner = web.AppRunner(app, access_log=None)  # probes would flood the log
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", cfg.http_port).start()
        log.info("Dashboard and API listening on :%d", cfg.http_port)
    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, main_task.cancel)
        except NotImplementedError:  # Windows
            pass
    log.info("Orchestrator started: namespace=%s gate step=%d", cfg.namespace, cfg.chaos_gate_step_index)
    try:
        await orch.run()
    finally:
        if runner:
            await runner.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(amain())
    except asyncio.CancelledError:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
