#!/usr/bin/env bash
# Pull every third-party image the demo needs and load it into the kind cluster, so the demo works
# without internet (conference/classroom Wi-Fi). Run once while online, after scripts/setup-demo.sh.
# Docker keeps the pulled images, so a later `kind create cluster` + this script works fully offline.
set -euo pipefail
export MSYS_NO_PATHCONV=1
CLUSTER="${CLUSTER:-chaosgate}"

IMAGES=(
  quay.io/argoproj/argo-rollouts:v1.10.1
  quay.io/prometheus/prometheus:v3.15.0-distroless
  quay.io/prometheus-operator/prometheus-operator:v0.94.1
  quay.io/prometheus-operator/prometheus-config-reloader:v0.94.1
  docker.io/grafana/grafana:13.2.4-distroless
  quay.io/kiwigrid/k8s-sidecar:2.14.1
  litmuschaos/chaos-operator:3.31.0
  litmuschaos/chaos-runner:3.31.0
  litmuschaos/go-runner:3.31.0
)

for img in "${IMAGES[@]}"; do
  echo "==> $img"
  docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img" >/dev/null
  kind load docker-image "$img" --name "$CLUSTER" >/dev/null
done
echo "done: ${#IMAGES[@]} images loaded into kind-$CLUSTER"
echo "Tip: if you upgrade a chart in k8s/monitoring/ or the Litmus/Argo versions, update this list:"
echo "  kubectl get pods -A -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{\"\\n\"}{end}{end}' | sort -u"
