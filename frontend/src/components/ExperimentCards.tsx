import type { ExperimentRun } from "../api";

function badge(e: ExperimentRun): { text: string; cls: string } {
  if (e.status === "Cancelled") return { text: "Stopped early", cls: "pending" };
  if (e.status === "Pending") return { text: "Waiting", cls: "pending" };
  if (e.status === "Running") return { text: "Running", cls: "running" };
  return e.verdict === "Pass" ? { text: "Pass", cls: "pass" } : { text: e.verdict ?? "Done", cls: "fail" };
}

const LABEL: Record<string, string> = {
  "pod-delete": "Pod deletion",
  "pod-cpu-hog-exec": "CPU saturation",
  "pod-network-latency": "Network latency",
  "pod-http-latency": "HTTP latency",
  "pod-memory-hog-exec": "Memory pressure",
};

export function ExperimentCards({ experiments }: { experiments: ExperimentRun[] }) {
  return (
    <div className="cards">
      {experiments.map((e) => {
        const b = badge(e);
        return (
          <div key={e.name} className={`card exp exp-${b.cls}`}>
            <div className="exp-title">{LABEL[e.name] ?? e.name}</div>
            <code className="muted">{e.name}</code>
            <div className="exp-foot">
              <span className={`badge badge-${b.cls}`}>
                {b.cls === "running" && <i className="spinner" aria-hidden />}
                {b.text}
              </span>
              <span className="muted">{e.duration ? `${e.duration}s` : ""}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
