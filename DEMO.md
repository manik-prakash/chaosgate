# Demo script (about 12 minutes)

Timings below were measured by `scripts/e2e.py` on the kind cluster with `EXPERIMENT_DURATION_SECONDS=30` and the
images preloaded: healthy release 3.5 min, fragile deployment 3.5 min, buggy release 1.2 min. Litmus adds start-up
time per experiment, so each 30 s experiment takes about 60 s. On a cluster without preloaded images the first run
can take 4+ minutes.

## Before you walk in

1. `bash scripts/setup-demo.sh` (first time) or start Docker and `kind get clusters` should list `chaosgate`.
2. `bash scripts/open-dashboard.sh` in a spare terminal and leave it running.
3. `bash scripts/reset-demo.sh`, then check http://localhost:8080 shows *Live gate* with a **Live** badge.
4. Open Grafana in a second tab: http://localhost:3000/d/chaos-gate (admin / admin).
5. Have `kubectl get pods -w` on screen in a terminal if you want the pods visibly churning.
6. Have the design document handy to point at.

Run **one full rehearsal** the day before. Do not skip it.

## 1. The problem (1 minute)

> CI proves the code is correct. It does not prove it survives a pod dying, a CPU spike, or a slow network.
> A canary shrinks the blast radius but a bad canary on 10% of traffic still burns error budget.
> This project makes resilience a **mandatory gate**: a canary cannot reach 100% unless it survives real faults.

Show the diagram in the README.

## 2. How it fits together (2 minutes)

Point at the **Config** page and name the pieces:

- **Argo Rollouts**: canary delivery; pauses at step 1 (10% traffic).
- **LitmusChaos**: injects pod deletion, CPU saturation, HTTP latency into the canary's pods.
- **Prometheus + Sloth**: the SLO is defined as YAML; Sloth generates the recording rule
  `slo:sli_error:ratio_rate5m`. The orchestrator watches exactly that.
- **Orchestrator (the thing we built)**: sees the pause, runs chaos and SLO monitoring *together*, then promotes or aborts.

Say what is real: *Argo, Litmus, Prometheus, Sloth and Grafana are the real products running in Kubernetes; the
app is a small demo service so we can control its behaviour.*

## 3. Scenario A: healthy release, expect PROMOTED (about 3.5 min)

**Deploy & scenarios, Healthy release, Deploy.** The page jumps to *Live gate*.

Narrate while it runs:
- Stepper: gate triggered, chaos + SLO running together.
- Experiment cards go Running → Pass one at a time (sequential, fail-fast).
- The chart is the real error-budget burn rate sampled from Prometheus, staying near 0 under the red threshold.
- In the terminal, `kubectl get pods`: pods being killed and recreated by Litmus.

Result: green **PROMOTED**, the rollout continues to 100%. Click **Open report**.

## 4. Scenario B: buggy release, expect ABORTED by the SLO (about 1.5 min)

**Reset** first if the previous rollout isn't Healthy. If the page says *error budget still recovering*,
wait: see "Things that can surprise you".

**Deploy & scenarios, Buggy release.** This version answers most requests with HTTP 500.

- Chart: the burn rate climbs and **crosses the threshold**.
- The gate aborts immediately; the remaining chaos experiment is cancelled (cards show "Stopped early").
- Reason: *error budget burned 0.03 exceeding threshold 0.02*.
- Show Grafana: the error ratio for `v3-bad` spikes while `v1`/`v2` stay flat. Argo rolled the canary back.

Key message: *the chaos didn't even need to fail. The canary was caught because its error budget burned.*

## 5. Scenario C: fragile deployment, expect ABORTED by chaos (about 3.5 min)

**Reset**, then **Fragile deployment**. This scales to one replica, then deploys a new version.

- With no redundancy, the continuous availability probe fails while faults are injected.
- Decision: ABORTED, reason names the experiment that failed.
- Message: *same code, same SLO, but the deployment shape isn't resilient. Only fault injection finds that.*

## 6. Close (1 minute)

- **Reports**: every decision is an audit record.
- **Manual override**: *Rollouts* page, Promote/Abort buttons (with confirmation).
- **What I'd do next**: alert on a failed abort (known gap), per-service experiment sets, PR comment with the report.
- Mention honestly: the 5-minute SLO window lags; `pod-network-latency` needs a kernel module this laptop's
  WSL2 kernel doesn't have, so we use `pod-http-latency`.

## Likely questions

| Question | Answer |
|---|---|
| Is the chaos real? | Yes: the Litmus operator deletes real pods, saturates real CPU, injects latency. `kubectl get chaosengines` during a run. |
| Why is the canary only at 10%? | That's the blast-radius limit: the gate runs *before* more traffic shifts. |
| What if Prometheus is down? | Monitoring failure must not block delivery: a missing metric counts as 0 and the report says so. Chaos still gates. |
| What if the abort fails? | The orchestrator retries 3 times, then logs CRITICAL. There's no pager hook yet: documented gap. |
| What if the orchestrator is down? | Fail-closed: a canary stays paused at 10% and is never promoted. On restart it picks the paused rollout up again and gates it. |
| Is the dashboard secure? | No authentication: it's a demo tool reached only through `kubectl port-forward`. RBAC is least-privilege and namespaced; the pod runs non-root with a read-only filesystem. |
| Why does it wait for both? | A pass needs both signals. Either failing aborts at once and cancels the other. |
| Why not just Argo's own analysis? | You can get close natively: an `AnalysisTemplate` with a Job provider (passes if the Job exits 0) running Litmus, plus a Prometheus metric. That has fewer moving parts. This project follows the design document instead: a dedicated orchestrator that makes one decision from both signals, cancels the other check the moment one fails, and produces the report and live dashboard. The cost is one more service to run. |
| Does it work with other apps? | Yes: change `CHAOS_APP_LABEL`, the SLO, and the Rollout. |

## Things that can surprise you

- **"Error budget still recovering".** After the buggy release, the 5-minute SLO window keeps the burn rate high
  for a few minutes. Scenario A or C started then will abort on leftover errors. The dashboard disables deploys
  until it clears; `reset-demo.sh` waits for it too. Do scenario B **last**, or leave 5 minutes before the next one.
- **Port-forward died.** It does when the orchestrator pod restarts. Rerun `scripts/open-dashboard.sh` (it retries on its own).
- **Don't `kubectl apply -f k8s/rollout/` mid-demo.** It resets the image and starts an unplanned canary.
- **Everything is stuck.** `bash scripts/reset-demo.sh`. Worst case `bash scripts/teardown.sh && bash scripts/setup-demo.sh`.
- **A Litmus experiment takes long the first time** (image pull). Run one healthy scenario in rehearsal to warm up,
  and run `scripts/preload-images.sh` so nothing is downloaded during the demo.

## Suggested order for the live demo

**A (healthy)** → **C (fragile)** → **B (buggy)**: B last, so its leftover errors can't spoil another scenario.
If time is short, do **A** and **B** only (about 6 minutes).
