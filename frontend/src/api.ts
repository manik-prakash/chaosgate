export type RunState = "RUNNING" | "PROMOTED" | "ABORTED" | "INTERRUPTED" | "ERROR";

export interface ExperimentRun {
  name: string;
  duration: number;
  status: "Pending" | "Running" | "Done" | "Cancelled";
  verdict: string | null;
}

export interface Sample {
  t: number;
  burn: number;
}

export interface Run {
  id: string;
  rollout: string;
  namespace: string;
  revision: string | null;
  state: RunState;
  started: string;
  finished: string | null;
  experiments: ExperimentRun[];
  samples: Sample[];
  threshold: number;
  window_seconds: number;
  decision: "PROMOTED" | "ABORTED" | null;
  reason: string | null;
  report: string | null;
}

export interface Rollout {
  name: string;
  phase: string;
  message: string;
  step: number | null;
  total_steps: number;
  abort: boolean;
  replicas: number | null;
  updated_replicas: number;
  available_replicas: number;
  image: string | null;
  revision: string | null;
  stable_revision: string | null;
}

export interface GateConfig {
  namespace: string;
  gate_step_index: number;
  slo_service: string;
  error_budget_threshold: number;
  monitoring_window_seconds: number;
  poll_interval_seconds: number;
  max_concurrent_gates: number;
  chaos_app_label: string;
  experiments: { name: string; duration: number }[];
  grafana_url: string;
  demo_mode: boolean;
}

export interface SloStatus {
  burn: number | null;
  threshold: number;
  ready: boolean;
}

export interface AppState {
  rollouts: Rollout[];
  slo: SloStatus | null;
  active_runs: Run[];
  config: GateConfig;
}

export interface ReportEntry {
  file: string;
  timestamp: number;
  decision: "PROMOTED" | "ABORTED" | "UNKNOWN";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error ?? `${res.status} ${res.statusText}`);
  return body as T;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

export const api = {
  state: () => request<AppState>("/api/state"),
  reports: () => request<ReportEntry[]>("/api/reports"),
  deploy: (rollout: string, image: string) => post(`/api/rollouts/${rollout}/deploy`, { image }),
  promote: (rollout: string) => post(`/api/rollouts/${rollout}/promote`),
  abort: (rollout: string) => post(`/api/rollouts/${rollout}/abort`),
  rollback: (rollout: string) => post<{ image: string }>(`/api/rollouts/${rollout}/rollback`),
  setReplicas: (rollout: string, replicas: number) =>
    post(`/api/demo/rollouts/${rollout}/replicas`, { replicas }),
};
