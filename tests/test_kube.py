from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from kube import Kube, RolloutNotFound, summarise


def rollout(stable="abc", image="demo:v3"):
    return {
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {"replicas": 3, "template": {"spec": {"containers": [
            {"name": "c", "image": image, "ports": [{"containerPort": 80}], "livenessProbe": {"x": 1}}]}},
            "strategy": {"canary": {"steps": [{}, {}, {}]}}},
        "status": {"phase": "Degraded", "stableRS": stable, "currentStepIndex": 0, "abort": True},
    }


def make(cfg, ro):
    custom, apps = MagicMock(), MagicMock()
    custom.get_namespaced_custom_object.return_value = ro
    return Kube(cfg, api=custom, apps=apps), custom, apps


def test_summarise(cfg):
    s = summarise(rollout())
    assert s["name"] == "web" and s["phase"] == "Degraded" and s["abort"] is True
    assert s["total_steps"] == 3 and s["image"] == "demo:v3" and s["stable_revision"] == "abc"


def test_set_image_sends_the_whole_container_list(cfg):
    kube, custom, _ = make(cfg, rollout())
    kube.set_image("web", "demo:v9")
    body = custom.patch_namespaced_custom_object.call_args.kwargs["body"]
    c = body["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == "demo:v9" and c["livenessProbe"] == {"x": 1} and c["ports"]  # nothing dropped


def test_rollback_uses_the_stable_replicaset_image(cfg):
    kube, custom, apps = make(cfg, rollout(stable="abc"))
    apps.read_namespaced_replica_set.return_value = NS(
        spec=NS(template=NS(spec=NS(containers=[NS(image="demo:v2")]))))
    assert kube.rollback("web") == "demo:v2"
    assert apps.read_namespaced_replica_set.call_args.args[0] == "web-abc"
    body = custom.patch_namespaced_custom_object.call_args.kwargs["body"]
    assert body["spec"]["template"]["spec"]["containers"][0]["image"] == "demo:v2"


def test_missing_rollout_raises_not_found(cfg):
    kube, custom, _ = make(cfg, rollout())
    custom.get_namespaced_custom_object.side_effect = ApiException(status=404)
    with pytest.raises(RolloutNotFound):
        kube.set_image("ghost", "x:1")
