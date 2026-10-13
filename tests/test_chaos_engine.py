from kubernetes.client.exceptions import ApiException

from chaos_engine import ChaosEngine


def result_obj(verdict):
    return {"status": {"experimentStatus": {"verdict": verdict}}}


def make_engine(cfg, custom_api, verdicts):
    """verdicts: {experiment-name: verdict or None (never completes)}"""

    def get_result(group, version, ns, plural, name):
        assert plural == "chaosresults"
        for exp, v in verdicts.items():
            if name.endswith(f"-{exp}"):
                if v is None:
                    return {"status": {"experimentStatus": {"verdict": "Awaited"}}}
                return result_obj(v)
        raise ApiException(status=404)

    custom_api.get_namespaced_custom_object.side_effect = get_result
    return ChaosEngine(cfg, custom_api=custom_api)


def created_names(custom_api):
    return [c.kwargs["body"]["metadata"]["name"] for c in custom_api.create_namespaced_custom_object.call_args_list]


async def test_all_pass_runs_sequentially_in_order(cfg, custom_api):
    eng = make_engine(cfg, custom_api, {"pod-delete": "Pass", "pod-cpu-hog-exec": "Pass", "pod-network-latency": "Pass"})
    ok, results, reason = await eng.run_all()
    assert ok is True
    assert [r.name for r in results] == ["pod-delete", "pod-cpu-hog-exec", "pod-network-latency"]
    assert all(r.verdict == "Pass" for r in results)
    names = created_names(custom_api)
    assert [n.rsplit("-", 1)[0] for n in names] == [
        "chaos-gate-pod-delete", "chaos-gate-pod-cpu-hog-exec", "chaos-gate-pod-network-latency",
    ]
    assert names[0].startswith("chaos-gate-pod-delete-")


async def test_fail_fast_skips_remaining(cfg, custom_api):
    eng = make_engine(cfg, custom_api, {"pod-delete": "Fail", "pod-cpu-hog-exec": "Pass", "pod-network-latency": "Pass"})
    ok, results, reason = await eng.run_all()
    assert ok is False
    assert len(results) == 1 and results[0].verdict == "Fail"
    assert custom_api.create_namespaced_custom_object.call_count == 1
    assert "pod-delete returned Fail" in reason


async def test_timeout_is_fail(cfg, custom_api):
    eng = make_engine(cfg, custom_api, {"pod-delete": None})
    ok, results, reason = await eng.run_all()
    assert ok is False
    assert results[0].verdict == "Timeout"
    assert "timed out" in reason


async def test_create_error_aborts_immediately(cfg, custom_api):
    custom_api.create_namespaced_custom_object.side_effect = ApiException(status=500, reason="boom")
    ok, results, reason = await ChaosEngine(cfg, custom_api=custom_api).run_all()
    assert ok is False
    assert results[0].verdict == "Error"
    assert "pod-delete" in reason
    assert custom_api.create_namespaced_custom_object.call_count == 1


async def test_engine_body_targets_configured_app(cfg, custom_api):
    eng = make_engine(cfg, custom_api, {"pod-delete": "Fail"})
    await eng.run_all()
    body = custom_api.create_namespaced_custom_object.call_args.kwargs["body"]
    spec = body["spec"]
    assert spec["appinfo"]["applabel"] == cfg.chaos_app_label
    assert spec["chaosServiceAccount"] == cfg.chaos_service_account
    assert body["kind"] == "ChaosEngine"


async def test_check_crds_fatal_when_missing(cfg, custom_api):
    import pytest

    api_ext = type("X", (), {})()

    def read(name):
        raise ApiException(status=404)

    api_ext.read_custom_resource_definition = read
    with pytest.raises(SystemExit) as e:
        ChaosEngine(cfg, custom_api=custom_api).check_crds(api_ext)
    assert e.value.code == 1


async def test_on_experiment_callback_sequence(cfg, custom_api):
    eng = make_engine(cfg, custom_api, {"pod-delete": "Pass", "pod-cpu-hog-exec": "Fail"})
    events = []
    await eng.run_all(on_experiment=lambda n, s, v: events.append((n, s, v)))
    assert events == [
        ("pod-delete", "Running", None), ("pod-delete", "Done", "Pass"),
        ("pod-cpu-hog-exec", "Running", None), ("pod-cpu-hog-exec", "Done", "Fail"),
    ]
    assert [r.name for r in eng.results] == ["pod-delete", "pod-cpu-hog-exec"]


async def test_cancel_deletes_running_engine(cfg, custom_api):
    import asyncio
    import dataclasses

    cfg = dataclasses.replace(cfg, chaos_result_timeout_seconds=30)
    eng = make_engine(cfg, custom_api, {"pod-delete": None})
    task = asyncio.create_task(eng.run_all())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    args = custom_api.delete_namespaced_custom_object.call_args.args
    assert args[3] == "chaosengines" and args[4].startswith("chaos-gate-pod-delete-")


async def test_experiment_duration_comes_from_config(cfg, custom_api):
    import dataclasses

    eng = ChaosEngine(dataclasses.replace(cfg, experiment_duration_seconds=30), custom_api=custom_api)
    assert {e["duration"] for e in eng.experiments} == {30}
    env = eng._engine_body("x", eng.experiments[0])["spec"]["experiments"][0]["spec"]["components"]["env"]
    assert {"name": "TOTAL_CHAOS_DURATION", "value": "30"} in env


async def test_error_verdict_is_terminal_with_litmus_reason(cfg, custom_api):
    import dataclasses

    cfg = dataclasses.replace(cfg, chaos_result_timeout_seconds=30)  # must NOT wait for this
    def get_result(group, version, ns, plural, name):
        return {"status": {"experimentStatus": {
            "verdict": "Error",
            "errorOutput": {"errorCode": "HELPER_ERROR", "reason": "failed to create tc rules"},
        }}}
    custom_api.get_namespaced_custom_object.side_effect = get_result
    import time
    t0 = time.monotonic()
    ok, results, reason = await ChaosEngine(cfg, custom_api=custom_api).run_all()
    assert time.monotonic() - t0 < 5
    assert ok is False and results[0].verdict == "Error"
    assert "pod-delete returned Error" in reason and "failed to create tc rules" in reason


async def test_stopped_verdict_is_failure(cfg, custom_api):
    eng = make_engine(cfg, custom_api, {"pod-delete": "Stopped"})
    ok, results, reason = await eng.run_all()
    assert ok is False and results[0].verdict == "Stopped"


def test_experiments_selected_from_catalog(cfg, custom_api):
    import dataclasses

    c = dataclasses.replace(cfg, experiments="pod-delete, pod-http-latency", chaos_app_port=9000)
    eng = ChaosEngine(c, custom_api=custom_api)
    assert [e["name"] for e in eng.experiments] == ["pod-delete", "pod-http-latency"]
    env = eng._engine_body("x", eng.experiments[1])["spec"]["experiments"][0]["spec"]["components"]["env"]
    assert {"name": "TARGET_SERVICE_PORT", "value": "9000"} in env


def test_unknown_experiment_rejected_at_startup(cfg, custom_api):
    import dataclasses

    import pytest

    with pytest.raises(ValueError, match="nope"):
        ChaosEngine(dataclasses.replace(cfg, experiments="pod-delete,nope"), custom_api=custom_api)


def test_default_experiments_are_the_design_doc_set(cfg, custom_api):
    assert [e["name"] for e in ChaosEngine(cfg, custom_api=custom_api).experiments] == [
        "pod-delete", "pod-cpu-hog-exec", "pod-network-latency",
    ]


async def test_engine_deleted_after_verdict_is_read(cfg, custom_api):
    eng = make_engine(cfg, custom_api, {"pod-delete": "Pass", "pod-cpu-hog-exec": "Fail"})
    await eng.run_all()
    deleted = [c.args[4] for c in custom_api.delete_namespaced_custom_object.call_args_list]
    assert len(deleted) == 2 and deleted[0].startswith("chaos-gate-pod-delete-")
    assert deleted[1].startswith("chaos-gate-pod-cpu-hog-exec-")


async def test_cleanup_failure_does_not_change_the_verdict(cfg, custom_api):
    eng = make_engine(cfg, custom_api, {"pod-delete": "Pass", "pod-cpu-hog-exec": "Pass", "pod-network-latency": "Pass"})
    custom_api.delete_namespaced_custom_object.side_effect = ApiException(status=500)
    ok, results, _ = await eng.run_all()
    assert ok is True and len(results) == 3


def test_no_probe_by_default(cfg, custom_api):
    eng = ChaosEngine(cfg, custom_api=custom_api)
    spec = eng._engine_body("x", eng.experiments[0])["spec"]["experiments"][0]["spec"]
    assert "probe" not in spec


def test_availability_probe_added_when_url_configured(cfg, custom_api):
    import dataclasses

    c = dataclasses.replace(cfg, chaos_probe_url="http://backend.default.svc/healthz")
    eng = ChaosEngine(c, custom_api=custom_api)
    probe = eng._engine_body("x", eng.experiments[0])["spec"]["experiments"][0]["spec"]["probe"][0]
    assert probe["type"] == "httpProbe" and probe["mode"] == "Continuous"
    assert probe["httpProbe/inputs"]["url"] == "http://backend.default.svc/healthz"
    assert probe["httpProbe/inputs"]["method"]["get"]["responseCode"] == "200"
    assert probe["runProperties"]["stopOnFailure"] is False


async def test_cleanup_stale_deletes_only_our_engines(cfg, custom_api):
    custom_api.list_namespaced_custom_object.return_value = {"items": [
        {"metadata": {"name": "chaos-gate-pod-delete-1"}},
        {"metadata": {"name": "someone-elses-engine"}},
        {"metadata": {"name": "chaos-gate-pod-cpu-hog-exec-2"}},
    ]}
    eng = ChaosEngine(cfg, custom_api=custom_api)
    n = await eng.cleanup_stale()
    deleted = sorted(c.args[4] for c in custom_api.delete_namespaced_custom_object.call_args_list)
    assert n == 2 and deleted == ["chaos-gate-pod-cpu-hog-exec-2", "chaos-gate-pod-delete-1"]


async def test_cleanup_stale_never_raises(cfg, custom_api):
    custom_api.list_namespaced_custom_object.side_effect = ApiException(status=500)
    assert await ChaosEngine(cfg, custom_api=custom_api).cleanup_stale() == 0
