import pytest
from kubernetes.client.exceptions import ApiException

from watcher import RolloutWatcher


def event(name, phase="Paused", step=1, typ="MODIFIED", pod_hash="h1"):
    return {
        "type": typ,
        "object": {
            "metadata": {"name": name},
            "status": {"phase": phase, "currentStepIndex": step, "currentPodHash": pod_hash},
        },
    }


class Script:
    """stream_fn that plays one scripted stream per call: a list of events, or an Exception to raise."""

    def __init__(self, *streams):
        self.streams = list(streams)
        self.calls = 0

    def __call__(self, namespace):
        self.calls += 1
        s = self.streams.pop(0)
        if isinstance(s, Exception):
            raise s
        return iter(s)


class Done(Exception):
    pass


def make(cfg, script, clock=None, sleeps=None):
    sleeps = sleeps if sleeps is not None else []

    async def fake_sleep(d):
        sleeps.append(d)

    return RolloutWatcher(cfg, stream_fn=script, clock=clock or (lambda: 0.0), sleep=fake_sleep), sleeps


async def collect(w, n):
    out = []
    async for obj in w.events():
        out.append(obj)
        if len(out) == n:
            break
    return out


async def test_filters_on_phase_and_step(cfg):
    script = Script([event("a", phase="Progressing"), event("b", step=0), event("c"), event("d", step=2)])
    w, _ = make(cfg, script)
    out = await collect(w, 1)
    assert [o["metadata"]["name"] for o in out] == ["c"]


async def test_same_revision_gated_once_while_paused(cfg):
    script = Script([event("web"), event("web"), event("web"), event("other")])
    w, _ = make(cfg, script)
    out = await collect(w, 2)
    assert [o["metadata"]["name"] for o in out] == ["web", "other"]


async def test_long_runs_are_not_regated_by_elapsed_time(cfg):
    """A gate run can take far longer than 5 minutes; a later MODIFIED event must not re-trigger it."""
    t = [0.0]
    script = Script([event("web"), event("web"), event("other")])
    w, _ = make(cfg, script, clock=lambda: t[0])
    gen = w.events()
    first = await gen.__anext__()
    t[0] = 3600.0
    second = await gen.__anext__()
    assert first["metadata"]["name"] == "web" and second["metadata"]["name"] == "other"


async def test_regated_after_rollout_leaves_the_gate(cfg):
    """abort/retry: Paused -> Degraded/Progressing -> Paused again for the same revision gates again."""
    script = Script([event("web"), event("web", phase="Degraded", step=0), event("web")])
    w, _ = make(cfg, script)
    out = await collect(w, 2)
    assert [o["metadata"]["name"] for o in out] == ["web", "web"]


async def test_reconnects_with_exponential_backoff(cfg):
    err = ConnectionError("down")
    script = Script(err, err, err, [event("web")])
    w, sleeps = make(cfg, script)
    out = await collect(w, 1)
    assert out[0]["metadata"]["name"] == "web"
    assert script.calls == 4
    assert sleeps == [1, 2, 4]


async def test_backoff_capped_at_60(cfg):
    script = Script(*[ConnectionError()] * 8, [event("web")])
    w, sleeps = make(cfg, script)
    await collect(w, 1)
    assert max(sleeps) == 60


async def test_reconnects_when_stream_ends(cfg):
    script = Script([], [event("web")])
    w, _ = make(cfg, script)
    out = await collect(w, 1)
    assert script.calls == 2 and out


async def test_forbidden_is_fatal(cfg):
    script = Script(ApiException(status=403, reason="Forbidden"))
    w, _ = make(cfg, script)
    with pytest.raises(SystemExit) as e:
        await collect(w, 1)
    assert e.value.code == 1


async def test_new_revision_of_same_rollout_is_not_deduped(cfg):
    script = Script([event("web", pod_hash="h1"), event("web", pod_hash="h1"), event("web", pod_hash="h2")])
    w, _ = make(cfg, script)
    out = await collect(w, 2)
    assert [o["status"]["currentPodHash"] for o in out] == ["h1", "h2"]
