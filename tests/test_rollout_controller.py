import logging

from kubernetes.client.exceptions import ApiException

from rollout_controller import RolloutController


def ctl(cfg, api):
    return RolloutController(cfg, custom_api=api, retry_delay=0)


async def test_promote_patches_status_and_spec(cfg, custom_api):
    assert await ctl(cfg, custom_api).promote("web") is True
    status = custom_api.patch_namespaced_custom_object_status.call_args
    assert status.args[3:5] == ("rollouts", "web") or status.args[-2:] == ("rollouts", "web")
    assert status.kwargs["body"] == {"status": {"pauseConditions": None}}
    spec = custom_api.patch_namespaced_custom_object.call_args
    assert spec.kwargs["body"] == {"spec": {"paused": False}}


async def test_abort_patches_status_abort_true(cfg, custom_api):
    assert await ctl(cfg, custom_api).abort("web") is True
    call = custom_api.patch_namespaced_custom_object_status.call_args
    assert call.kwargs["body"] == {"status": {"abort": True}}
    custom_api.patch_namespaced_custom_object.assert_not_called()


async def test_retries_transient_errors_then_succeeds(cfg, custom_api):
    custom_api.patch_namespaced_custom_object_status.side_effect = [
        ApiException(status=500),
        ApiException(status=503),
        {"ok": True},
    ]
    assert await ctl(cfg, custom_api).abort("web") is True
    assert custom_api.patch_namespaced_custom_object_status.call_count == 3


async def test_all_retries_fail_logs_critical(cfg, custom_api, caplog):
    custom_api.patch_namespaced_custom_object_status.side_effect = ApiException(status=500)
    with caplog.at_level(logging.CRITICAL):
        assert await ctl(cfg, custom_api).abort("web") is False
    assert custom_api.patch_namespaced_custom_object_status.call_count == 3
    assert any(r.levelno == logging.CRITICAL and "web" in r.getMessage() for r in caplog.records)


async def test_not_found_does_not_retry(cfg, custom_api, caplog):
    custom_api.patch_namespaced_custom_object_status.side_effect = ApiException(status=404)
    assert await ctl(cfg, custom_api).abort("ghost") is False
    assert custom_api.patch_namespaced_custom_object_status.call_count == 1
