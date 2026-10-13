import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AppState, type ReportEntry, type Run } from "./api";
import { applyEvent } from "./runs";

/** Live run list from the server-sent-event stream; the browser reconnects automatically. */
export function useRuns() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const es = new EventSource("/api/events");
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.addEventListener("snapshot", (e) =>
      setRuns((cur) => applyEvent(cur, { type: "snapshot", runs: JSON.parse((e as MessageEvent).data) })),
    );
    es.addEventListener("run", (e) =>
      setRuns((cur) => applyEvent(cur, { type: "run", run: JSON.parse((e as MessageEvent).data) })),
    );
    return () => es.close();
  }, []);

  return { runs, live };
}

/** Poll an async loader; keeps the last good value and exposes the last error. */
export function usePolling<T>(load: () => Promise<T>, intervalMs: number) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const loadRef = useRef(load);
  loadRef.current = load;

  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(undefined);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(id);
  }, [refresh, intervalMs]);

  return { data, error, refresh };
}

export const useAppState = () => usePolling<AppState>(api.state, 3000);
export const useReports = () => usePolling<ReportEntry[]>(api.reports, 5000);

/** Current time, ticking once a second (for countdowns). */
export function useNow(): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}
