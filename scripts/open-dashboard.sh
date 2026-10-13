#!/usr/bin/env bash
# Port-forward the dashboard (8080) and Grafana (3000) and keep them alive.
# kubectl port-forward dies whenever the orchestrator pod restarts, so this re-establishes it.
#   Dashboard: http://localhost:8080        Grafana: http://localhost:3000/d/chaos-gate  (admin / admin)
set -uo pipefail
export MSYS_NO_PATHCONV=1

forward() {  # forward <label> <kubectl port-forward args...>
  local label="$1"; shift
  while true; do
    kubectl port-forward "$@" >/dev/null 2>&1
    echo "[$label] port-forward ended, retrying in 2s..." >&2
    sleep 2
  done
}

forward dashboard svc/chaos-gate-dashboard 8080:8080 &
forward grafana -n monitoring svc/kps-grafana 3000:80 &
trap 'kill $(jobs -p) 2>/dev/null' EXIT INT TERM

echo "Dashboard: http://localhost:8080"
echo "Grafana:   http://localhost:3000/d/chaos-gate   (admin / admin)"
echo "Ctrl+C to stop."
wait
