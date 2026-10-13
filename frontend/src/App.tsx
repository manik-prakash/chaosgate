import { useEffect, useState } from "react";
import { useAppState, useReports, useRuns } from "./hooks";
import { Config } from "./pages/Config";
import { LiveGate } from "./pages/LiveGate";
import { Reports } from "./pages/Reports";
import { Rollouts } from "./pages/Rollouts";
import { Scenarios } from "./pages/Scenarios";

const TABS = [
  { id: "live", label: "Live gate" },
  { id: "rollouts", label: "Rollouts" },
  { id: "scenarios", label: "Deploy & scenarios" },
  { id: "reports", label: "Reports" },
  { id: "config", label: "Config" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const fromHash = (): TabId => {
  const id = window.location.hash.replace("#", "");
  return TABS.some((t) => t.id === id) ? (id as TabId) : "live";
};

export function App() {
  const [tab, setTab] = useState<TabId>(fromHash);
  const { runs, live } = useRuns();
  const { data: state, error, refresh } = useAppState();
  const reports = useReports();

  useEffect(() => {
    const onHash = () => setTab(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const goTo = (id: string) => {
    window.location.hash = id;
  };
  const running = runs.some((r) => r.state === "RUNNING");

  return (
    <>
      <header className="top">
        <div className="brand">
          <span className="logo" aria-hidden>✓</span>
          <div>
            <h1>Chaos Gate</h1>
            <span className="muted small">Resilience-gated canary delivery</span>
          </div>
        </div>
        <nav aria-label="Sections">
          {TABS.map((t) => (
            <a key={t.id} href={`#${t.id}`} className={t.id === tab ? "active" : ""} aria-current={t.id === tab ? "page" : undefined}>
              {t.label}
              {t.id === "live" && running && <i className="dot" aria-label="gate running" />}
            </a>
          ))}
        </nav>
        <span className={`conn ${live ? "conn-on" : "conn-off"}`} title={live ? "Receiving live updates" : "Reconnecting…"}>
          {live ? "Live" : "Offline"}
        </span>
      </header>
      <main>
        {error && <div className="notice bad-notice" role="alert">Cannot reach the orchestrator API: {error}</div>}
        {tab === "live" && <LiveGate runs={runs} goTo={goTo} />}
        {tab === "rollouts" && <Rollouts rollouts={state?.rollouts} refresh={refresh} />}
        {tab === "scenarios" && <Scenarios state={state} goTo={goTo} refresh={refresh} />}
        {tab === "reports" && <Reports reports={reports.data} />}
        {tab === "config" && <Config config={state?.config} />}
      </main>
    </>
  );
}
