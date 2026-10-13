"""Thin Kubernetes helper for the dashboard: read rollout state, change image / replicas."""
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from config import Config

GROUP, VERSION, PLURAL = "argoproj.io", "v1alpha1", "rollouts"


class RolloutNotFound(KeyError):
    pass


def summarise(r: dict) -> dict:
    spec, status = r.get("spec", {}), r.get("status", {})
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])
    return {
        "name": r["metadata"]["name"],
        "namespace": r["metadata"].get("namespace"),
        "phase": status.get("phase", "Unknown"),
        "message": status.get("message", ""),
        "step": status.get("currentStepIndex"),
        "total_steps": len(spec.get("strategy", {}).get("canary", {}).get("steps", [])),
        "abort": bool(status.get("abort")),
        "replicas": spec.get("replicas"),
        "updated_replicas": status.get("updatedReplicas", 0),
        "available_replicas": status.get("availableReplicas", 0),
        "image": containers[0].get("image") if containers else None,
        "revision": status.get("currentPodHash"),
        "stable_revision": status.get("stableRS"),
    }


class Kube:
    def __init__(self, cfg: Config, api: client.CustomObjectsApi | None = None, apps: client.AppsV1Api | None = None):
        self.cfg = cfg
        self.api = api or client.CustomObjectsApi()
        self.apps = apps or client.AppsV1Api()

    def list_rollouts(self) -> list[dict]:
        items = self.api.list_namespaced_custom_object(GROUP, VERSION, self.cfg.namespace, PLURAL)["items"]
        return [summarise(r) for r in items]

    def _get(self, name: str) -> dict:
        try:
            return self.api.get_namespaced_custom_object(GROUP, VERSION, self.cfg.namespace, PLURAL, name)
        except ApiException as exc:
            if exc.status == 404:
                raise RolloutNotFound(name) from exc
            raise

    def set_image(self, name: str, image: str) -> None:
        """Change the first container's image. A JSON merge patch replaces lists wholesale, so the
        full container list (probes, ports, resources) is read, modified and sent back."""
        rollout = self._get(name)
        containers = rollout["spec"]["template"]["spec"]["containers"]
        containers[0]["image"] = image
        self.api.patch_namespaced_custom_object(
            GROUP, VERSION, self.cfg.namespace, PLURAL, name,
            body={"spec": {"template": {"spec": {"containers": containers}}}},
        )

    def set_replicas(self, name: str, replicas: int) -> None:
        self._get(name)
        self.api.patch_namespaced_custom_object(
            GROUP, VERSION, self.cfg.namespace, PLURAL, name, body={"spec": {"replicas": replicas}}
        )

    def rollback(self, name: str) -> str:
        """Point the rollout back at the image of its stable ReplicaSet (instant: no new canary)."""
        stable = self._get(name).get("status", {}).get("stableRS")
        if not stable:
            raise RolloutNotFound(f"{name} (no stable revision yet)")
        rs = self.apps.read_namespaced_replica_set(f"{name}-{stable}", self.cfg.namespace)
        image = rs.spec.template.spec.containers[0].image
        self.set_image(name, image)
        return image
