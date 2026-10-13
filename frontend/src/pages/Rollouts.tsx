import { useState } from "react";
import { api, type Rollout } from "../api";

function phaseClass(r: Rollout): string {
  if (r.abort || r.phase === "Degraded") return "bad";
  if (r.phase === "Healthy") return "ok";
  return "warn";
}

function ManualControl({ rollout, onDone }: { rollout: Rollout; onDone: () => void }) {
  const [confirm, setConfirm] = useState<"promote" | "abort" | null>(null);
  const [msg, setMsg] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function run(kind: "promote" | "abort") {
    setBusy(true);
    try {
      await api[kind](rollout.name);
      setMsg(`${kind === "promote" ? "Promote" : "Abort"} sent`);
      onDone();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  return (
    <div className="manual">
      <p className="muted small">
        Manual override: the orchestrator decides automatically; use these only to intervene.
      </p>
      <div className="row">
        {(["promote", "abort"] as const).map((kind) =>
          confirm === kind ? (
            <button key={kind} className={`btn ${kind === "abort" ? "danger" : "primary"}`} disabled={busy} onClick={() => run(kind)}>
              Confirm {kind}?
            </button>
          ) : (
            <button key={kind} className="btn" disabled={busy} onClick={() => setConfirm(kind)}>
              {kind === "promote" ? "Promote now" : "Abort rollout"}
            </button>
          ),
        )}
        {confirm && (
          <button className="btn ghost" onClick={() => setConfirm(null)}>
            Cancel
          </button>
        )}
      </div>
      {msg && <p className="small" role="status">{msg}</p>}
    </div>
  );
}

export function Rollouts({ rollouts, refresh }: { rollouts?: Rollout[]; refresh: () => void }) {
  if (!rollouts) return <p className="muted">Loading rollouts…</p>;
  if (!rollouts.length) return <p className="muted">No rollouts found in this namespace.</p>;
  return (
    <div className="stack">
      {rollouts.map((r) => (
        <section key={r.name} className="card">
          <div className="section-head">
            <h2>{r.name}</h2>
            <span className={`pill pill-${phaseClass(r)}`}>{r.abort ? "Aborted" : r.phase}</span>
          </div>
          <dl className="kv">
            <dt>Image</dt>
            <dd><code>{r.image}</code></dd>
            <dt>Canary step</dt>
            <dd>{r.step ?? "–"} of {r.total_steps}</dd>
            <dt>Pods</dt>
            <dd>
              {r.available_replicas} available / {r.replicas} desired · {r.updated_replicas} on new revision
            </dd>
            {r.message && (
              <>
                <dt>Status</dt>
                <dd>{r.message}</dd>
              </>
            )}
          </dl>
          <ManualControl rollout={r} onDone={refresh} />
        </section>
      ))}
    </div>
  );
}
