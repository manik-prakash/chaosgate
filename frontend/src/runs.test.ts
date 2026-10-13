import { describe, expect, it } from "vitest";
import type { Run } from "./api";
import { applyEvent, chartData, pickRun, secondsLeft, stages } from "./runs";

const run = (over: Partial<Run> = {}): Run => ({
  id: "r1", rollout: "backend", namespace: "default", revision: "h", state: "RUNNING",
  started: "2026-01-01T00:00:00Z", finished: null, experiments: [], samples: [],
  threshold: 0.02, window_seconds: 100, decision: null, reason: null, report: null, ...over,
});

describe("applyEvent", () => {
  it("snapshot replaces the list, newest first", () => {
    const out = applyEvent([run({ id: "old" })], {
      type: "snapshot",
      runs: [run({ id: "a", started: "2026-01-01T00:00:00Z" }), run({ id: "b", started: "2026-01-02T00:00:00Z" })],
    });
    expect(out.map((r) => r.id)).toEqual(["b", "a"]);
  });

  it("run event upserts by id without duplicating", () => {
    const first = applyEvent([], { type: "run", run: run() });
    const updated = applyEvent(first, { type: "run", run: run({ state: "PROMOTED" }) });
    expect(updated).toHaveLength(1);
    expect(updated[0].state).toBe("PROMOTED");
  });

  it("a new run is placed first", () => {
    const out = applyEvent([run({ id: "a" })], { type: "run", run: run({ id: "b", started: "2026-01-02T00:00:00Z" }) });
    expect(out[0].id).toBe("b");
  });
});

describe("pickRun", () => {
  it("prefers the running run over a newer finished one", () => {
    const runs = [run({ id: "done", state: "ABORTED" }), run({ id: "live" })];
    expect(pickRun(runs)?.id).toBe("live");
  });
  it("falls back to the first run, or undefined", () => {
    expect(pickRun([run({ id: "x", state: "PROMOTED" })])?.id).toBe("x");
    expect(pickRun([])).toBeUndefined();
  });
});

describe("secondsLeft", () => {
  it("counts down from the window and never goes negative", () => {
    const r = run();
    expect(secondsLeft(r, Date.parse("2026-01-01T00:00:30Z"))).toBe(70);
    expect(secondsLeft(r, Date.parse("2026-01-01T00:05:00Z"))).toBe(0);
  });
  it("is zero for a finished run", () => {
    expect(secondsLeft(run({ state: "PROMOTED" }), Date.parse("2026-01-01T00:00:10Z"))).toBe(0);
  });
});

describe("stages", () => {
  it("shows monitoring active while running", () => {
    expect(stages(run()).map((s) => s.stage)).toEqual(["done", "active", "pending"]);
  });
  it("marks aborted runs as failed", () => {
    expect(stages(run({ state: "ABORTED" })).map((s) => s.stage)).toEqual(["done", "failed", "failed"]);
  });
  it("treats an internal error as a failed run", () => {
    expect(stages(run({ state: "ERROR" })).map((s) => s.stage)).toEqual(["done", "failed", "failed"]);
  });
  it("marks promoted runs as done", () => {
    expect(stages(run({ state: "PROMOTED" })).map((s) => s.stage)).toEqual(["done", "done", "done"]);
  });
});

describe("chartData", () => {
  it("maps samples to rounded seconds", () => {
    const r = run({ samples: [{ t: 4.6, burn: 0.01 }, { t: 9.9, burn: 0.03 }] });
    expect(chartData(r)).toEqual([{ t: 5, burn: 0.01 }, { t: 10, burn: 0.03 }]);
  });
});
