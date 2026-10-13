import asyncio

from chaos_engine import ExperimentResult
from main import ChaosGatedOrchestrator


class FakeChaos:
    def __init__(self, ok=True, delay=0, boom=False):
        self.ok, self.delay, self.boom = ok, delay, boom

    async def run_all(self, **kw):
        await asyncio.sleep(self.delay)
        if self.boom:
            raise RuntimeError("kaboom")
        res = [ExperimentResult("pod-delete", 60, "Pass" if self.ok else "Fail")]
        return self.ok, res, ("all chaos experiments passed" if self.ok else "pod-delete returned Fail")


class FakeSLO:
    def __init__(self, ok=True, burn=0.001, delay=0):
        self.ok, self.burn, self.delay = ok, burn, delay
        self.polls, self.no_data_polls = 1, 0

    async def monitor(self, **kw):
        await asyncio.sleep(self.delay)
        return self.ok, self.burn


class FakeController:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    async def promote(self, name):
        self.calls.append(("promote", name))
        return self.ok

    async def abort(self, name):
        self.calls.append(("abort", name))
        return self.ok


class FakeReporter:
    def __init__(self):
        self.reports = []

    def generate(self, report):
        self.reports.append(report)
        return "x.html"


def rollout(name="web"):
    return {"metadata": {"name": name, "namespace": "default"}}


def build(cfg, chaos, slo, ctl=None, rep=None):
    ctl, rep = ctl or FakeController(), rep or FakeReporter()
    orch = ChaosGatedOrchestrator(cfg, watcher=None, chaos_factory=lambda: chaos,
                                  slo_factory=lambda: slo, controller=ctl, reporter=rep)
    return orch, ctl, rep


async def test_both_pass_promotes(cfg):
    orch, ctl, rep = build(cfg, FakeChaos(), FakeSLO())
    assert await orch.run_gate(rollout()) == "PROMOTED"
    assert ctl.calls == [("promote", "web")]
    assert rep.reports[0].decision == "PROMOTED"


async def test_chaos_failure_aborts(cfg):
    orch, ctl, rep = build(cfg, FakeChaos(ok=False), FakeSLO())
    assert await orch.run_gate(rollout()) == "ABORTED"
    assert ctl.calls == [("abort", "web")]
    assert "pod-delete returned Fail" in rep.reports[0].reason


async def test_budget_breach_aborts_with_reason(cfg):
    orch, ctl, rep = build(cfg, FakeChaos(), FakeSLO(ok=False, burn=0.034))
    assert await orch.run_gate(rollout()) == "ABORTED"
    assert "0.034" in rep.reports[0].reason and "0.02" in rep.reports[0].reason


async def test_unexpected_exception_aborts_and_still_reports(cfg):
    orch, ctl, rep = build(cfg, FakeChaos(boom=True), FakeSLO())
    assert await orch.run_gate(rollout()) == "ABORTED"
    assert ctl.calls == [("abort", "web")]
    assert len(rep.reports) == 1


async def test_runs_chaos_and_slo_concurrently(cfg):
    orch, _, _ = build(cfg, FakeChaos(delay=0.2), FakeSLO(delay=0.2))
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await orch.run_gate(rollout())
    assert loop.time() - t0 < 0.35


async def test_patch_failure_noted_in_report(cfg):
    orch, _, rep = build(cfg, FakeChaos(), FakeSLO(), ctl=FakeController(ok=False))
    await orch.run_gate(rollout())
    assert "manual" in rep.reports[0].reason.lower()


async def test_same_rollout_runs_are_serialised(cfg):
    active, peak = 0, 0

    class Chaos(FakeChaos):
        async def run_all(self, **kw):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return await super().run_all()

    orch, _, _ = build(cfg, Chaos(), FakeSLO())
    await asyncio.gather(orch.handle(rollout("web")), orch.handle(rollout("web")))
    assert peak == 1


async def test_global_concurrency_is_capped(cfg):
    import dataclasses

    cfg = dataclasses.replace(cfg, max_concurrent_gates=2)
    active, peak = 0, 0

    class Chaos(FakeChaos):
        async def run_all(self, **kw):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return await super().run_all()

    orch, _, _ = build(cfg, Chaos(), FakeSLO())
    await asyncio.gather(*(orch.handle(rollout(f"r{i}")) for i in range(5)))
    assert peak == 2


async def test_chaos_failure_cancels_slo_monitor_early(cfg):
    orch, ctl, rep = build(cfg, FakeChaos(ok=False), FakeSLO(delay=5))
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await orch.run_gate(rollout()) == "ABORTED"
    assert loop.time() - t0 < 1  # did not wait for the 5s SLO monitor
    assert ctl.calls == [("abort", "web")]


async def test_slo_breach_cancels_chaos_early(cfg):
    orch, ctl, rep = build(cfg, FakeChaos(delay=5), FakeSLO(ok=False, burn=0.05))
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await orch.run_gate(rollout()) == "ABORTED"
    assert loop.time() - t0 < 1
    assert "0.05" in rep.reports[0].reason


async def test_success_waits_for_both_tasks(cfg):
    orch, ctl, _ = build(cfg, FakeChaos(delay=0), FakeSLO(delay=0.2))
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await orch.run_gate(rollout()) == "PROMOTED"
    assert loop.time() - t0 >= 0.2  # chaos passed first, still waited for the SLO window


async def test_tracker_records_the_run(cfg):
    from runs import RunTracker

    tracker = RunTracker()
    orch = ChaosGatedOrchestrator(cfg, watcher=None, chaos_factory=lambda: FakeChaos(),
                                  slo_factory=lambda: FakeSLO(), controller=FakeController(),
                                  reporter=FakeReporter(), tracker=tracker)
    await orch.run_gate(rollout())
    run = tracker.all()[0]
    assert run["rollout"] == "web" and run["state"] == "PROMOTED"


async def test_reason_burn_is_rounded(cfg):
    orch, _, rep = build(cfg, FakeChaos(), FakeSLO(ok=False, burn=0.05157298420848146))
    await orch.run_gate(rollout())
    assert "0.0516" in rep.reports[0].reason and "0.0515729" not in rep.reports[0].reason


async def test_budget_already_burning_before_chaos_is_called_out(cfg):
    slo = FakeSLO(ok=False, burn=0.05)
    slo.first_burn = 0.05  # the very first sample was already over the threshold
    orch, _, rep = build(cfg, FakeChaos(delay=5), slo)
    await orch.run_gate(rollout())
    reason = rep.reports[0].reason
    assert "already" in reason and "previous release" in reason


async def test_experiments_that_never_ran_appear_in_the_report_as_cancelled(cfg):
    class Planned(FakeChaos):
        experiments = [{"name": "pod-delete", "duration": 30}, {"name": "pod-cpu-hog-exec", "duration": 30}]
        results = []

    orch, _, rep = build(cfg, Planned(delay=5), FakeSLO(ok=False, burn=0.05))
    await orch.run_gate(rollout())
    assert [(e.name, e.verdict) for e in rep.reports[0].experiments] == [
        ("pod-delete", "Cancelled"), ("pod-cpu-hog-exec", "Cancelled"),
    ]


async def test_slo_monitor_is_told_to_keep_going_until_chaos_finishes(cfg):
    seen = {}

    class Slo(FakeSLO):
        async def monitor(self, **kw):
            seen["keep_going"] = kw["keep_going"]
            seen["during"] = kw["keep_going"]()
            await asyncio.sleep(0.15)
            seen["after"] = kw["keep_going"]()
            return True, 0.0

    orch, _, _ = build(cfg, FakeChaos(delay=0.05), Slo())
    await orch.run_gate(rollout())
    assert seen["during"] is True and seen["after"] is False


async def test_report_failure_does_not_lose_the_decision(cfg):
    class BrokenReporter(FakeReporter):
        def generate(self, report):
            raise OSError("volume is full")

    from runs import RunTracker

    tracker = RunTracker()
    ctl = FakeController()
    orch = ChaosGatedOrchestrator(cfg, watcher=None, chaos_factory=lambda: FakeChaos(),
                                  slo_factory=lambda: FakeSLO(), controller=ctl,
                                  reporter=BrokenReporter(), tracker=tracker)
    assert await orch.run_gate(rollout()) == "PROMOTED"
    run = tracker.all()[0]
    assert ctl.calls == [("promote", "web")]
    assert run["state"] == "PROMOTED" and run["report"] is None  # finished, not stuck as RUNNING


async def test_unexpected_error_marks_the_run_as_error(cfg):
    from runs import RunTracker

    class ExplodingController(FakeController):
        async def promote(self, name):
            raise RuntimeError("api exploded")

    tracker = RunTracker()
    orch = ChaosGatedOrchestrator(cfg, watcher=None, chaos_factory=lambda: FakeChaos(),
                                  slo_factory=lambda: FakeSLO(), controller=ExplodingController(),
                                  reporter=FakeReporter(), tracker=tracker)
    await orch.handle(rollout())  # handle() logs and swallows, the run must still be closed out
    run = tracker.all()[0]
    assert run["state"] == "ERROR" and "api exploded" in run["reason"]


async def test_promotion_without_slo_data_says_the_budget_was_not_verified(cfg):
    slo = FakeSLO()
    slo.polls, slo.no_data_polls = 4, 4
    orch, _, rep = build(cfg, FakeChaos(), slo)
    assert await orch.run_gate(rollout()) == "PROMOTED"
    assert "not verified" in rep.reports[0].reason
