"""RolloutController: promote / abort an Argo Rollout by patching it."""
import asyncio
import logging

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from config import Config

log = logging.getLogger(__name__)

GROUP, VERSION, PLURAL = "argoproj.io", "v1alpha1", "rollouts"
ATTEMPTS = 3


class RolloutController:
    def __init__(self, cfg: Config, custom_api: client.CustomObjectsApi | None = None, retry_delay: float = 2):
        self.cfg = cfg
        self.api = custom_api or client.CustomObjectsApi()
        self.retry_delay = retry_delay

    def check_crd(self, api_ext: client.ApiextensionsV1Api | None = None) -> None:
        """Fatal exit if Argo Rollouts is not installed; logs the controller version if discoverable."""
        import sys

        api_ext = api_ext or client.ApiextensionsV1Api()
        try:
            api_ext.read_custom_resource_definition(f"{PLURAL}.{GROUP}")
        except ApiException as exc:
            log.critical("Argo Rollouts CRD not available (%s); exiting", exc.status)
            sys.exit(1)
        log.info("Argo Rollouts CRD present; using status.abort for aborts")

    async def _patch(self, desc: str, name: str, ops) -> bool:
        """Run `ops` (a list of zero-arg callables doing patches) with retries."""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                for op in ops:
                    resp = await asyncio.to_thread(op)
                    log.info("%s %s OK (attempt %d)", desc, name, attempt)
                    log.debug("%s %s response: %s", desc, name, resp)
                return True
            except ApiException as exc:
                log.error("%s %s failed (attempt %d): %s %s", desc, name, attempt, exc.status, exc.body)
                if exc.status == 404:
                    log.error("Rollout %s not found; nothing to do", name)
                    return False
                if attempt < ATTEMPTS:
                    await asyncio.sleep(self.retry_delay)
        log.critical(
            "%s of rollout %s FAILED after %d attempts; manual intervention required%s",
            desc, name, ATTEMPTS,
            " (a bad canary may still be live)" if desc == "abort" else "",
        )
        return False

    async def promote(self, name: str) -> bool:
        ns = self.cfg.namespace
        ops = [
            lambda: self.api.patch_namespaced_custom_object_status(
                GROUP, VERSION, ns, PLURAL, name, body={"status": {"pauseConditions": None}}
            ),
            lambda: self.api.patch_namespaced_custom_object(
                GROUP, VERSION, ns, PLURAL, name, body={"spec": {"paused": False}}
            ),
        ]
        return await self._patch("promote", name, ops)

    async def abort(self, name: str) -> bool:
        ns = self.cfg.namespace
        ops = [
            lambda: self.api.patch_namespaced_custom_object_status(
                GROUP, VERSION, ns, PLURAL, name, body={"status": {"abort": True}}
            )
        ]
        return await self._patch("abort", name, ops)
