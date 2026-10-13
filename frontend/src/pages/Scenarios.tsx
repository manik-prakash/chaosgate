import { useState } from "react";
import { api, type AppState } from "../api";

interface Scenario {
  id: string;
  title: string;
  blurb: string;
  expect: "PROMOTED" | "ABORTED";
  why: string;
  run: (rollout: string, currentImage: string | null) => Promise<unknown>;
}

const SCENARIOS: Scenario[] = [
  {
    id: "healthy",
    title: "Healthy release",
    blurb: "A new version that behaves correctly under fault injection.",
    expect: "PROMOTED",
    why: "All chaos experiments pass and the error budget stays under the threshold.",
    // alternate v1 <-> v2 so the deploy is always a real change (deploying the stable image does nothing)
    run: (r, current) => api.deploy(r, current === "demo-backend:v2" ? "demo-backend:v1" : "demo-backend:v2"),
  },
  {
    id: "buggy",
    title: "Buggy release",
    blurb: "Version 3 returns HTTP 500 on most requests.",
    expect: "ABORTED",
    why: "The error budget burn rate crosses the threshold, so the gate aborts and Argo rolls back.",
    run: (r) => api.deploy(r, "demo-backend:v3-bad"),
  },
  {
    id: "fragile",
    title: "Fragile deployment",
    blurb: "A single replica, then a new version. With no redundancy, the service drops requests while faults hit it.",
    expect: "ABORTED",
    why: "The continuous availability probe fails during fault injection, so the gate aborts.",
    run: async (r, current) => {
      await api.setReplicas(r, 1);
      return api.deploy(r, current === "demo-backend:v2" ? "demo-backend:v1" : "demo-backend:v2");
    },
  },
];

export function Scenarios({ state, goTo, refresh }: { state?: AppState; goTo: (tab: string) => void; refresh: () => void }) {
  const [msg, setMsg] = useState<{ ok: boolean; text: string }>();
  const [busy, setBusy] = useState(false);
  const [image, setImage] = useState("");
  const rollout = state?.rollouts[0];
  const running = (state?.active_runs.length ?? 0) > 0;
  const needsReset = !!rollout && rollout.phase !== "Healthy";
  const slo = state?.slo;
  const budgetBusy = !!slo && !slo.ready;
  const demo = state?.config.demo_mode;

  async function act(label: string, fn: () => Promise<unknown>, thenLive = true) {
    setBusy(true);
    setMsg(undefined);
    try {
      await fn();
      setMsg({ ok: true, text: `${label}: started` });
      refresh();
      if (thenLive) setTimeout(() => goTo("live"), 600);
    } catch (e) {
      setMsg({ ok: false, text: `${label} failed: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  if (!rollout) return <p className="muted">No rollout available yet.</p>;
  const disabled = busy || running || needsReset || budgetBusy;

  return (
    <div className="stack">
      {running && <div className="notice">A gate run is in progress. Wait for it to finish before deploying again.</div>}
      {!running && needsReset && (
        <div className="notice">
          The rollout is <b>{rollout.abort ? "aborted" : rollout.phase}</b>. Reset it to the stable version before starting a new scenario.
        </div>
      )}
      {!running && !needsReset && budgetBusy && slo && (
        <div className="notice">
          The error budget is still recovering from the previous release: burn rate{" "}
          <b>{slo.burn?.toFixed(4)}</b> is above the {slo.threshold} threshold. The 5-minute window needs a few minutes
          to clear; a gate started now would abort on the leftover errors.
        </div>
      )}

      <div className="cards three">
        {SCENARIOS.map((s) => (
          <section key={s.id} className="card scenario">
            <h2>{s.title}</h2>
            <p>{s.blurb}</p>
            <p className="small">
              Expected: <span className={`pill pill-${s.expect === "PROMOTED" ? "ok" : "bad"}`}>{s.expect}</span>
            </p>
            <p className="muted small">{s.why}</p>
            <button
              className="btn primary"
              disabled={disabled || (s.id === "fragile" && !demo)}
              onClick={() => act(s.title, () => s.run(rollout.name, rollout.image))}
            >
              Deploy this scenario
            </button>
          </section>
        ))}
      </div>

      <section className="card">
        <h2>Reset</h2>
        <p className="muted">Roll back to the stable version with three replicas so the next scenario starts clean.</p>
        <button
          className="btn"
          disabled={busy || running || !demo}
          onClick={() =>
            act("Reset", async () => {
              await api.setReplicas(rollout.name, 3);
              return api.rollback(rollout.name);
            }, false)
          }
        >
          Reset to stable version
        </button>
      </section>

      <section className="card">
        <h2>Custom image</h2>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            void act("Deploy", () => api.deploy(rollout.name, image.trim()));
          }}
        >
          <input
            aria-label="Container image"
            placeholder="demo-backend:v2"
            value={image}
            onChange={(e) => setImage(e.target.value)}
          />
          <button className="btn" disabled={disabled || !image.trim()} type="submit">
            Deploy
          </button>
        </form>
      </section>

      {msg && (
        <p className={msg.ok ? "ok-text" : "bad-text"} role="status">
          {msg.text}
        </p>
      )}
    </div>
  );
}
