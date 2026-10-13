#!/usr/bin/env bash
# Build and install the whole demo on a local kind cluster. Safe to re-run.
#   Needs: docker (running), kind, kubectl, helm. Run from Git Bash / WSL / Linux / macOS.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1   # keep Git Bash from rewriting /paths in docker/kubectl args

CLUSTER="${CLUSTER:-chaosgate}"
ARGO_ROLLOUTS_VERSION="${ARGO_ROLLOUTS_VERSION:-v1.10.1}"
LITMUS_VERSION="${LITMUS_VERSION:-3.31.1}"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing required tool: $1" >&2; exit 1; }; }
for t in docker kind kubectl helm; do need "$t"; done
docker info >/dev/null 2>&1 || { echo "docker is not running" >&2; exit 1; }

say "kind cluster '$CLUSTER'"
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER"
kubectl config use-context "kind-$CLUSTER" >/dev/null

say "Building images"
# name error-rate startup-delay: v3-bad answers 60% of requests with HTTP 500
for spec in "v1 0 0" "v2 0 0" "v3-bad 0.6 0"; do
  set -- $spec
  docker build -q -t "demo-backend:$1" --build-arg "VERSION=$1" --build-arg "ERROR_RATE=$2"     --build-arg "STARTUP_DELAY=$3" demo-app >/dev/null
  echo "built demo-backend:$1"
done
docker build -q -f docker/Dockerfile -t chaos-gate-orchestrator:latest . >/dev/null && echo "built chaos-gate-orchestrator:latest"

say "Loading images into the cluster"
for img in demo-backend:v1 demo-backend:v2 demo-backend:v3-bad chaos-gate-orchestrator:latest; do
  kind load docker-image "$img" --name "$CLUSTER" >/dev/null
done

say "Argo Rollouts $ARGO_ROLLOUTS_VERSION"
kubectl get ns argo-rollouts >/dev/null 2>&1 || kubectl create namespace argo-rollouts
# server-side apply: the Rollout CRD is too large for client-side apply's annotation
kubectl apply --server-side --force-conflicts -n argo-rollouts \
  -f "https://github.com/argoproj/argo-rollouts/releases/download/$ARGO_ROLLOUTS_VERSION/install.yaml" >/dev/null
kubectl wait -n argo-rollouts --for=condition=available deploy/argo-rollouts --timeout=180s

say "Prometheus + Grafana (kube-prometheus-stack)"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add litmuschaos https://litmuschaos.github.io/litmus-helm/ >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
  -f k8s/monitoring/kube-prometheus-stack-values.yaml --wait --timeout 8m >/dev/null

say "LitmusChaos $LITMUS_VERSION (operator + experiments)"
helm upgrade --install litmus-core litmuschaos/litmus-core -n litmus --create-namespace \
  --set operator.image.repository=litmuschaos/chaos-operator --set runner.image.repository=litmuschaos/chaos-runner \
  --set operator.image.pullPolicy=IfNotPresent --wait --timeout 4m >/dev/null
helm upgrade --install k8s-chaos litmuschaos/kubernetes-chaos -n default \
  --set image.litmusGO.repository=litmuschaos/go-runner --set image.litmusLIBImage.repository=litmuschaos/go-runner \
  --set image.litmus.repository=litmuschaos/ansible-runner --set image.litmusGO.pullPolicy=IfNotPresent >/dev/null
kubectl apply -f k8s/chaos/litmus-rbac.yaml >/dev/null

say "SLO rules, scrape config, Grafana dashboard"
kubectl apply -f slo/generated/prometheus-rules.yaml -f k8s/monitoring/backend-podmonitor.yaml \
  -f k8s/monitoring/grafana-dashboard.yaml >/dev/null

say "Demo app, load generator, orchestrator"
# Create the Rollout only if it is missing: re-applying the manifest would reset its image to demo-backend:v1
# and start an unplanned canary whenever the demo had moved on to another version.
kubectl apply -f k8s/rollout/service.yaml -f k8s/rollout/loadgen.yaml >/dev/null
kubectl get rollout backend >/dev/null 2>&1 || kubectl apply -f k8s/rollout/rollout.yaml >/dev/null
# earlier versions of this project used cluster-wide roles; remove them if they are still around
kubectl delete clusterrole,clusterrolebinding chaos-gate-orchestrator litmus-admin --ignore-not-found >/dev/null
kubectl apply -f k8s/orchestrator/ >/dev/null
kubectl rollout restart deploy/chaos-gate-orchestrator >/dev/null
kubectl rollout status deploy/loadgen --timeout=120s
kubectl rollout status deploy/chaos-gate-orchestrator --timeout=180s

say "Preloading the Litmus runner images (so the first chaos run is not slowed by image pulls)"
bash "$(dirname "$0")/preload-images.sh" >/dev/null

cat <<EOF

Ready. Open the dashboard:
  kubectl port-forward svc/chaos-gate-dashboard 8080:8080    ->  http://localhost:8080
  kubectl port-forward -n monitoring svc/kps-grafana 3000:80 ->  http://localhost:3000/d/chaos-gate  (admin / admin)
Run scripts/reset-demo.sh between scenarios.
EOF
