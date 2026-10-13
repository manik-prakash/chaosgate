import type { Run } from "../api";
import { BurnChart } from "../components/BurnChart";
import { ExperimentCards } from "../components/ExperimentCards";
import { Stepper } from "../components/Stepper";
import { useNow } from "../hooks";
import { pickRun, secondsLeft } from "../runs";

function Banner({ run }: { run: Run }) {
  if (run.state === "RUNNING")
    return (
      <div className="banner banner-running">
        <strong>Gate running</strong>
        <span>
          Injecting faults on <b>{run.rollout}</b> while watching the error budget…
        </span>
      </div>
    );
  const ok = run.state === "PROMOTED";
  return (
    <div className={`banner ${ok ? "banner-ok" : "banner-bad"}`}>
      <strong>{run.decision ?? run.state}</strong>
      <span>{run.reason}</span>
      {run.report && (
        <a href={`/reports/${run.report}`} target="_blank" rel="noreferrer">
          Open report ↗
        </a>
      )}
    </div>
  );
}

export function LiveGate({ runs, goTo }: { runs: Run[]; goTo: (tab: string) => void }) {
  const now = useNow();
  const run = pickRun(runs);

  if (!run)
    return (
      <div className="empty card">
        <h2>No gate runs yet</h2>
        <p className="muted">A gate starts when a canary rollout pauses at the chaos step.</p>
        <button className="btn primary" onClick={() => goTo("scenarios")}>
          Start a deployment →
        </button>
      </div>
    );

  const peak = run.samples.reduce((m, s) => Math.max(m, s.burn), 0);
  const left = secondsLeft(run, now);
  return (
    <div className="stack">
      <Banner run={run} />
      <Stepper run={run} />
      <section>
        <h2>
          Chaos experiments <span className="muted">run one after another</span>
        </h2>
        <ExperimentCards experiments={run.experiments} />
      </section>
      <section className="card">
        <div className="section-head">
          <h2>Error budget burn rate</h2>
          <div className="stats">
            <span>
              peak <b className={peak > run.threshold ? "bad" : ""}>{peak.toFixed(4)}</b>
            </span>
            <span>
              threshold <b>{run.threshold}</b>
            </span>
            {run.state === "RUNNING" && (
              <span>
                window ends in <b>{left}s</b>
              </span>
            )}
          </div>
        </div>
        {run.samples.length ? (
          <BurnChart run={run} />
        ) : (
          <p className="muted">Waiting for the first Prometheus sample…</p>
        )}
      </section>
      <p className="muted small">
        Run {run.id} · revision {run.revision ?? "?"} · started {new Date(run.started).toLocaleTimeString()}
      </p>
    </div>
  );
}
