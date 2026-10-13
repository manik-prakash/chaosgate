"""RolloutWatcher: async generator yielding Rollouts paused at the chaos gate step."""
import asyncio
import logging
import sys
import time
from collections.abc import AsyncIterator, Callable

from kubernetes import client, watch
from kubernetes.client.exceptions import ApiException

from config import Config

log = logging.getLogger(__name__)

MAX_BACKOFF = 60
_END = object()


class RolloutWatcher:
    def __init__(
        self,
        cfg: Config,
        stream_fn: Callable[[str], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = asyncio.sleep,
    ):
        self.cfg = cfg
        self._stream_fn = stream_fn or self._k8s_stream
        self._clock = clock
        self._sleep = sleep
        self._gated: set[tuple[str, str | None]] = set()

    @staticmethod
    def _k8s_stream(namespace: str):
        api = client.CustomObjectsApi()
        return watch.Watch().stream(
            api.list_namespaced_custom_object,
            "argoproj.io", "v1alpha1", namespace, "rollouts",
            timeout_seconds=300,
        )

    def _is_gate(self, obj: dict) -> bool:
        status = obj.get("status") or {}
        return status.get("phase") == "Paused" and status.get("currentStepIndex") == self.cfg.chaos_gate_step_index

    def _is_duplicate(self, name: str, revision: str | None) -> bool:
        """A revision is gated once; it can be gated again only after the rollout leaves the gate step
        (abort, retry, new pause). Elapsed time is deliberately not used: a gate run can take many
        minutes and MODIFIED events keep arriving while the rollout is still paused."""
        key = (name, revision)
        if key in self._gated:
            log.debug("Ignoring repeat event for %s: this revision is already being gated", name)
            return True
        self._gated.add(key)
        return False

    def _left_gate(self, name: str) -> None:
        self._gated = {k for k in self._gated if k[0] != name}

    async def events(self) -> AsyncIterator[dict]:
        """Yield each rollout object that reaches the gate. Reconnects forever."""
        backoff = 1
        last_warned = 0.0
        while True:
            try:
                stream = iter(await asyncio.to_thread(self._stream_fn, self.cfg.namespace))
                while True:
                    ev = await asyncio.to_thread(next, stream, _END)
                    if ev is _END:
                        break
                    backoff = 1
                    obj = ev["object"]
                    name = obj["metadata"]["name"]
                    if not self._is_gate(obj):
                        self._left_gate(name)
                    elif not self._is_duplicate(name, (obj.get("status") or {}).get("currentPodHash")):
                        yield obj
                log.info("Watch stream ended; reconnecting")
            except ApiException as exc:
                if exc.status in (401, 403):
                    log.critical("Permission denied watching rollouts (%s): %s", exc.status, exc.reason)
                    sys.exit(1)
                last_warned = self._warn(f"K8s API error {exc.status}", last_warned)
            except Exception as exc:  # noqa: BLE001 - any transport failure means reconnect
                last_warned = self._warn(f"watch failed: {exc}", last_warned)
            else:
                backoff = 1
            await self._sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    def _warn(self, msg: str, last_warned: float) -> float:
        now = self._clock()
        if now - last_warned >= 60 or last_warned == 0.0:
            log.warning("%s; reconnecting with backoff", msg)
            return now
        return last_warned
