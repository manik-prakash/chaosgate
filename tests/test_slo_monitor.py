import requests

from slo_monitor import SLOMonitor


async def test_clean_run_returns_true_and_max(cfg, fake_prom):
    fake_prom([0.001, 0.005, 0.002])
    ok, max_burn = await SLOMonitor(cfg).monitor()
    assert ok is True
    assert max_burn == 0.005


async def test_breach_returns_immediately(cfg, fake_prom):
    import dataclasses

    cfg = dataclasses.replace(cfg, chaos_duration_seconds=30)
    fp = fake_prom([0.001, 0.034, 0.0])
    ok, max_burn = await SLOMonitor(cfg).monitor()
    assert ok is False
    assert max_burn == 0.034
    assert fp.calls == 2  # stopped at the breach, did not run the full window


async def test_value_equal_to_threshold_is_not_a_breach(cfg, fake_prom):
    fake_prom([cfg.error_budget_threshold])
    ok, _ = await SLOMonitor(cfg).monitor()
    assert ok is True


async def test_unreachable_prometheus_continues_as_zero(cfg, fake_prom):
    fake_prom([requests.ConnectionError("down")])
    mon = SLOMonitor(cfg)
    ok, max_burn = await mon.monitor()
    assert ok is True
    assert max_burn == 0.0
    assert mon.no_data_polls == mon.polls > 0


async def test_empty_result_treated_as_zero(cfg, fake_prom):
    fake_prom([None])
    mon = SLOMonitor(cfg)
    ok, max_burn = await mon.monitor()
    assert (ok, max_burn) == (True, 0.0)
    assert mon.no_data_polls == mon.polls


async def test_query_uses_service_name(cfg, monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"], seen["params"] = url, params
        raise requests.ConnectionError()

    monkeypatch.setattr("slo_monitor.requests.get", fake_get)
    await SLOMonitor(cfg).monitor()
    assert seen["url"].endswith("/api/v1/query")
    assert seen["params"]["query"] == 'slo:sli_error:ratio_rate5m{sloth_service="backend-api"}'


async def test_on_sample_called_each_poll(cfg, fake_prom):
    fake_prom([0.001, 0.002])
    seen = []
    mon = SLOMonitor(cfg)
    await mon.monitor(on_sample=seen.append)
    assert seen[:2] == [0.001, 0.002] and len(seen) == mon.polls
    assert mon.max_burn == 0.002


def test_fetch_returns_value_or_none(cfg, fake_prom):
    fake_prom([0.0123])
    assert SLOMonitor(cfg).fetch() == 0.0123


def test_fetch_none_when_no_data_or_unreachable(cfg, fake_prom):
    fake_prom([None])
    assert SLOMonitor(cfg).fetch() is None
    fake_prom([requests.ConnectionError("down")])
    assert SLOMonitor(cfg).fetch() is None


async def test_keeps_monitoring_past_the_window_while_chaos_is_still_running(cfg, fake_prom):
    """A real chaos run outlasts the configured window; the budget must stay watched until it ends."""
    fake_prom([0.001])
    mon = SLOMonitor(cfg)  # window is 1s with 10ms polls
    chaos_running = {"v": True}
    seen = []

    def on_sample(burn):
        seen.append(burn)
        if len(seen) == 150:  # well past the 1s window (~100 polls)
            chaos_running["v"] = False

    ok, _ = await mon.monitor(on_sample=on_sample, keep_going=lambda: chaos_running["v"])
    assert ok is True and len(seen) >= 150


async def test_breach_after_the_window_is_still_caught_while_chaos_runs(cfg, fake_prom):
    fake_prom([0.001] * 120 + [0.05])
    ok, burn = await SLOMonitor(cfg).monitor(keep_going=lambda: True)
    assert ok is False and burn == 0.05


async def test_first_sample_is_recorded(cfg, fake_prom):
    fake_prom([0.04])
    mon = SLOMonitor(cfg)
    await mon.monitor()
    assert mon.first_burn == 0.04
