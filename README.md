# Chaos-Gated Canary Delivery Pipeline

A quality gate for Kubernetes canary releases. Normal CI checks that code is *correct*; this checks that it is *resilient*. When an Argo Rollouts canary pauses at the gate step, a Python orchestrator

1. injects real infrastructure faults with **LitmusChaos** (pod deletion, CPU saturation, HTTP latency),
2. while watching the **error-budget burn rate** of the service in **Prometheus** (SLO defined with **Sloth**),
3. **promotes** the canary if it survived, or **aborts** it (automatic rollback) if a fault broke it or the budget burned too fast,
4. and records every decision in an HTML report, a live **React dashboard**, and a **Grafana** dashboard.

```
new image ─► Argo Rollouts canary (10%) ─► pauses at step 1
                                              │
                      RolloutWatcher ◄────────┘
                           │
          ┌────────────────┴────────────────┐
   ChaosEngine (Litmus)               SLOMonitor (Prometheus)
   pod-delete → cpu-hog → http-latency      polls slo:sli_error:ratio_rate5m
          └──────── either fails ⇒ the other is cancelled ────────┘
                           │
              all OK ? PROMOTE (→100%) : ABORT (rollback)
                           │
              RunTracker ─► dashboard (live) · HTML report · /reports
```

Design document: the v1.0 spec this project implements. Deviations are listed under [Design decisions](#design-decisions).

## Quick start (demo cluster on kind)

Needs Docker (running), `kind`, `kubectl`, `helm`, Git Bash/WSL/Linux/macOS. About 8 GB for the Docker VM.

```bash
bash scripts/setup-demo.sh        # builds images, creates the cluster, installs everything (~10 min first time)
bash scripts/preload-images.sh    # optional: load third-party images so the demo works offline
bash scripts/open-dashboard.sh    # dashboard :8080, Grafana :3000 (keeps port-forwards alive)
```

Open http://localhost:8080. Between scenarios run `bash scripts/reset-demo.sh` (or use *Reset* in the UI).
Presenting it? See [DEMO.md](DEMO.md).

## What is in the repo

| Path | Purpose |
|---|---|
| `orchestrator/` | the Python service. `main.py` wires it up; `watcher`, `chaos_engine`, `slo_monitor`, `rollout_controller`, `report_generator` are the design-doc components; `runs.py` tracks live runs, `api.py` + `kube.py` serve the dashboard; `config.py` is the only env reader |
| `frontend/` | React + Vite + TypeScript dashboard, built into the orchestrator image |
| `demo-app/` | the demo backend (`/metrics`, tunable error rate) and a load generator |
| `k8s/` | orchestrator Deployment/RBAC/PVC, the sample Rollout, Prometheus stack values, Grafana dashboard, Litmus RBAC, reference ChaosEngines |
| `slo/` | Sloth SLO definition and the rules generated from it |
| `scripts/` | `setup-demo`, `reset-demo`, `preload-images`, `open-dashboard`, `teardown`, `gen-slo`, and `e2e.py` (automated acceptance test) |
| `tests/` | unit tests, no cluster needed |

## The dashboard

| Page | What it shows / does |
|---|---|
| Live gate | pipeline stepper, one card per chaos experiment, burn-rate chart against the threshold, countdown, and the PROMOTED/ABORTED decision with its reason |
| Rollouts | phase, canary step, pods on the new revision; manual Promote / Abort with a confirm step |
| Deploy & scenarios | one-click **Healthy**, **Buggy (HTTP 500s)** and **Fragile (1 replica)** releases, reset, custom image. Waits while the previous release's errors are still in the 5-minute SLO window |
| Reports | history of every run with an inline viewer |
| Config | thresholds, experiment list, Grafana link |

No authentication: it is a demo tool. Reach it with `kubectl port-forward` only; state-changing requests also refuse cross-origin browsers.

## Configuration

All environment variables, set in the ConfigMap in `k8s/orchestrator/deployment.yaml`:

| Variable | Default | Meaning |
|---|---|---|
| `NAMESPACE`, `CHAOS_APP_NAMESPACE` | `default` | where rollouts / target pods live |
| `CHAOS_GATE_STEP_INDEX` | `1` | rollout step that is the gate (`pause: {}`) |
| `PROMETHEUS_URL` | `http://prometheus-operated:9090` | Prometheus |
| `SLO_SERVICE_NAME` | `backend-api` | the `sloth_service` label |
| `ERROR_BUDGET_THRESHOLD` | `0.02` | abort above this burn rate |
| `CHAOS_DURATION_SECONDS` | `180` | minimum SLO monitoring window. Monitoring continues past it while chaos is still running |
| `POLL_INTERVAL_SECONDS` | `10` | Prometheus poll interval |
| `EXPERIMENTS` | `pod-delete,pod-cpu-hog-exec,pod-network-latency` | run in this order. Catalog: those plus `pod-http-latency`, `pod-memory-hog-exec` |
| `EXPERIMENT_DURATION_SECONDS` | `60` | per experiment |
| `CHAOS_PROBE_URL` | empty | if set, every experiment fails when this URL stops returning 200 during chaos |
| `CHAOS_APP_LABEL`, `CHAOS_APP_PORT`, `CHAOS_SERVICE_ACCOUNT` | `app=backend`, `8080`, `litmus-admin` | chaos target |
| `CHAOS_RESULT_TIMEOUT_SECONDS` | `300` | a silent experiment counts as failed |
| `REPORT_DIR`, `GRAFANA_DASHBOARD_URL` | `/reports`, empty | reports volume, link in reports |
| `MAX_CONCURRENT_GATES` | `3` | gates running at once (same rollout: one at a time) |
| `HTTP_PORT`, `DEMO_MODE` | `8080`, `false` | dashboard port; `DEMO_MODE` enables the replica-scaling endpoint |

## Design decisions

Choices made where the design doc left room, plus deviations found by running it on a real cluster.

- **Concurrency (OQ1):** different rollouts gate concurrently up to `MAX_CONCURRENT_GATES`; one gate at a time per rollout.
- **Missing SLO data (OQ4):** counts as 0.0 so monitoring can never block delivery, and the report says no data was observed.
- **Alerting (OQ5):** CRITICAL log lines only. **Known gap:** if an abort patch fails 3 times, a bad canary can stay live and nothing pages anyone.
- **Deferred:** per-rollout experiment sets (OQ2) and GitHub PR comments (OQ3).
- **Abort / promote** patch the Rollout `/status` subresource (`status.abort: true`; clearing `status.pauseConditions` plus `spec.paused: false`).
- **Monitoring window (deviation):** the doc fixes the SLO window at `CHAOS_DURATION_SECONDS`. On a real cluster the chaos run takes longer than the experiments' configured durations (Litmus start-up per experiment): a measured run lasted 240 s while the 180 s window had already ended, leaving the last experiment unmonitored. The window is now a minimum; sampling continues until the chaos run ends.
- **Early cancel (deviation):** when chaos fails or the budget breaches, the other task is cancelled at once and any running ChaosEngine is deleted, instead of waiting for the full window. A pass still waits for both.
- **Dedupe (deviation):** the doc's "same rollout within 5 minutes" rule broke on a real cluster twice. A name-only key dropped the gate for a new revision, and a time-based window re-triggered a run that took longer than 5 minutes. A revision is now gated once, keyed on rollout + `status.currentPodHash`, until the rollout leaves the gate step.
- **Litmus verdicts (deviation):** real Litmus also returns `Error` and `Stopped`, not only `Pass`/`Fail`; both are treated as failures at once, with Litmus's own error text in the reason.
- **Experiment name (deviation):** the doc says `cpu-hog-exec`; Litmus calls it `pod-cpu-hog-exec`. Target `appkind` is `rollout`, since the app is an Argo Rollout.
- **Engine cleanup:** each ChaosEngine is deleted after its verdict is read, and engines left behind by a crashed or restarted orchestrator are removed at startup (otherwise the old chaos would overlap the re-gated run).
- **Fail-closed:** while the orchestrator is down, a paused canary just stays paused. On restart it gates every rollout still paused at the gate step.

## Why a separate orchestrator? (alternatives)

Argo Rollouts can express a similar gate natively: an `AnalysisTemplate` whose Job provider runs a Litmus experiment (the metric passes if the Job exits 0), next to a Prometheus metric, as inline or background analysis. That route needs no custom service. This project implements the design document's orchestrator instead, which buys one decision built from both signals with early cancellation, a persisted audit report, and a live dashboard, at the cost of running (and testing) one more component.

## Security notes

- No authentication on the dashboard, API or Grafana (anonymous viewer, `admin`/`admin`): this is a demo. Reach it only through `kubectl port-forward`. State-changing requests refuse cross-origin browsers, which does not stop a determined local attacker.
- RBAC is namespaced and minimal (`k8s/orchestrator/rbac.yaml`); the only cluster-wide permission is reading three CRDs at startup. The Litmus service account is a namespaced Role too, so chaos can only touch the demo namespace.
- The orchestrator pod runs as non-root with a read-only root filesystem, no capabilities and the default seccomp profile.

## Things worth knowing

- **`pod-network-latency` needs the `netem` kernel module.** Docker Desktop's WSL2 kernel doesn't have it (`CONFIG_NET_SCH_NETEM is not set`), so the experiment fails with "Specified qdisc kind is unknown". The demo uses `pod-http-latency` (latency injected by a proxy) instead; on a normal Linux kernel you can use the doc's default.
- **The 5-minute SLO window lags.** After a bad canary is rolled back, `slo:sli_error:ratio_rate5m` stays above the threshold for a few minutes, and a gate started in that time aborts on the leftover errors (correct, but confusing). The dashboard and `reset-demo.sh` wait for it to recover.
- **Don't re-apply `k8s/rollout/rollout.yaml` mid-demo.** It resets the image to `demo-backend:v1`, which starts a new canary.
- **Fragile scenario:** with one replica the availability probe fails during fault injection. In the runs I did this failed at `pod-http-latency`; `pod-delete` itself passed, because the replacement pod comes up before the old one finishes terminating.
- The sample SLO queries assume `http_requests_total{service="backend"}`, which `demo-app` exports.

## Development

```bash
pip install -r orchestrator/requirements.txt pytest pytest-asyncio
python -m pytest                         # watcher, chaos, SLO, controller, reports, tracker, API

cd frontend && npm install
npm test                                 # reducer / chart helpers
npm run dev                              # proxies /api to kubectl port-forward svc/chaos-gate-dashboard 18080:8080
```

Regenerate the SLO rules with the real Sloth CLI: `bash scripts/gen-slo.sh`.

Automated acceptance test against a running demo cluster (about 12 minutes; deploys the three scenarios and asserts decisions, reasons, rollout state and reports):
```bash
bash scripts/open-dashboard.sh   # in another terminal
python scripts/e2e.py
```

## Verification

`python scripts/e2e.py` against the demo cluster (rebuilt from scratch with `scripts/setup-demo.sh`, 3 m 48 s first time, under 2 min on re-run) passed all three scenarios: healthy release **PROMOTED** (209 s), fragile single-replica deployment **ABORTED** by the availability probe (209 s), buggy release **ABORTED** on error-budget burn (72 s). Each also checks the final Rollout state and that the report agrees. The fragile scenario aborted at `pod-http-latency` in every run so far (3 of 3).

## Tested on

kind v0.30 (Kubernetes 1.34) · Argo Rollouts v1.10.1 · LitmusChaos 3.31 · kube-prometheus-stack 92.3 (Prometheus 3.15) · Sloth 0.11 · Docker Desktop on WSL2.
