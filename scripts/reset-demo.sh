#!/usr/bin/env bash
# Return the demo to a clean, healthy state (3 replicas, stable demo-backend:v1) between scenarios.
set -euo pipefail
export MSYS_NO_PATHCONV=1

say() { printf '==> %s\n' "$*"; }

say "Removing leftover chaos objects"
kubectl delete chaosengines,chaosresults --all -n default --ignore-not-found >/dev/null

say "Rolling back to the stable version with 3 replicas"
kubectl patch rollout backend --type merge -p '{"spec":{"replicas":3}}' >/dev/null
# rollback = point the rollout back at its stable ReplicaSet's image (instant, no new canary).
# A merge patch replaces the container list, so read it, change the image, and send it all back.
"$(command -v python3 || command -v python)" - <<'PY'
import json, subprocess
def kubectl(*a):
    return subprocess.check_output(["kubectl", *a])
r = json.loads(kubectl("get", "rollout", "backend", "-o", "json"))
stable = r["status"]["stableRS"]
rs = json.loads(kubectl("get", "rs", f"backend-{stable}", "-o", "json"))
image = rs["spec"]["template"]["spec"]["containers"][0]["image"]
cs = r["spec"]["template"]["spec"]["containers"]
cs[0]["image"] = image
kubectl("patch", "rollout", "backend", "--type", "merge", "-p",
        json.dumps({"spec": {"template": {"spec": {"containers": cs}}}}))
print("stable image:", image)
PY

say "Waiting for the rollout to be Healthy"
for _ in $(seq 1 60); do
  phase=$(kubectl get rollout backend -o jsonpath='{.status.phase}' 2>/dev/null || true)
  [ "$phase" = "Healthy" ] && break
  sleep 3
done
[ "$phase" = "Healthy" ] || { echo "rollout did not become Healthy (phase: $phase)" >&2; exit 1; }

say "Waiting for the error budget to recover (5-minute SLO window; skip with SKIP_SLO_WAIT=1)"
THRESHOLD="${ERROR_BUDGET_THRESHOLD:-0.02}"
Q='slo:sli_error:ratio_rate5m%7Bsloth_service%3D%22backend-api%22%7D'
for _ in $(seq 1 120); do
  [ "${SKIP_SLO_WAIT:-0}" = "1" ] && break
  burn=$(kubectl get --raw "/api/v1/namespaces/monitoring/services/kps-prometheus:9090/proxy/api/v1/query?query=$Q" 2>/dev/null     | "$(command -v python3 || command -v python)" -c "import sys,json; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 0)" 2>/dev/null || echo 0)
  if "$(command -v python3 || command -v python)" -c "import sys; sys.exit(0 if float('$burn') <= float('$THRESHOLD') else 1)"; then
    echo "burn rate $burn is within the $THRESHOLD threshold"; echo "ready"; exit 0
  fi
  echo "burn rate $burn still above $THRESHOLD; waiting..."
  sleep 5
done
echo "ready"
