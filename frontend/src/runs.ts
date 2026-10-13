import type { Run } from "./api";

export type StreamEvent = { type: "snapshot"; runs: Run[] } | { type: "run"; run: Run };

const newestFirst = (a: Run, b: Run) => (a.started < b.started ? 1 : a.started > b.started ? -1 : 0);

/** Pure reducer for the SSE stream: a snapshot replaces everything, a run event upserts by id. */
export function applyEvent(runs: Run[], ev: StreamEvent): Run[] {
  if (ev.type === "snapshot") return [...ev.runs].sort(newestFirst);
  const rest = runs.filter((r) => r.id !== ev.run.id);
  return [ev.run, ...rest].sort(newestFirst);
}

/** The run the Live page should show: the one in progress, otherwise the most recent. */
export function pickRun(runs: Run[]): Run | undefined {
  return runs.find((r) => r.state === "RUNNING") ?? runs[0];
}

export interface ChartPoint {
  t: number;
  burn: number;
}

export function chartData(run: Run): ChartPoint[] {
  return run.samples.map((s) => ({ t: Math.round(s.t), burn: s.burn }));
}

/** Seconds left in the SLO monitoring window, never negative. */
export function secondsLeft(run: Run, nowMs: number): number {
  if (run.state !== "RUNNING") return 0;
  const elapsed = (nowMs - Date.parse(run.started)) / 1000;
  return Math.max(0, Math.round(run.window_seconds - elapsed));
}

export type Stage = "done" | "active" | "pending" | "failed";

/** Three pipeline stages for the stepper. */
export function stages(run: Run): { label: string; stage: Stage }[] {
  const finished = run.state !== "RUNNING";
  const failed = run.state === "ABORTED" || run.state === "INTERRUPTED" || run.state === "ERROR";
  return [
    { label: "Gate triggered", stage: "done" },
    { label: "Chaos + SLO monitoring", stage: finished ? (failed ? "failed" : "done") : "active" },
    {
      label: "Decision",
      stage: !finished ? "pending" : run.state === "PROMOTED" ? "done" : "failed",
    },
  ];
}
