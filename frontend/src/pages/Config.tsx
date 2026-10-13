import type { GateConfig } from "../api";

export function Config({ config }: { config?: GateConfig }) {
  if (!config) return <p className="muted">Loading configuration…</p>;
  const rows: [string, React.ReactNode][] = [
    ["Namespace", config.namespace],
    ["Gate triggers at rollout step", config.gate_step_index],
    ["Target pods", <code key="l">{config.chaos_app_label}</code>],
    ["SLO service", <code key="s">{config.slo_service}</code>],
    ["Error budget threshold", config.error_budget_threshold],
    ["SLO monitoring window", `at least ${config.monitoring_window_seconds}s (continues while chaos runs), polled every ${config.poll_interval_seconds}s`],
    ["Max concurrent gates", config.max_concurrent_gates],
  ];
  return (
    <div className="stack">
      <section className="card">
        <h2>Gate settings</h2>
        <dl className="kv">
          {rows.map(([k, v]) => (
            <div className="kv-row" key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="card">
        <h2>Chaos experiments</h2>
        <p className="muted small">Run in this order; the first failure aborts the rollout.</p>
        <ol className="plain">
          {config.experiments.map((e) => (
            <li key={e.name}>
              <code>{e.name}</code> <span className="muted">{e.duration}s</span>
            </li>
          ))}
        </ol>
      </section>
      {config.grafana_url && (
        <section className="card">
          <h2>Dashboards</h2>
          <a href={config.grafana_url} target="_blank" rel="noreferrer">Open Grafana ↗</a>
        </section>
      )}
    </div>
  );
}
