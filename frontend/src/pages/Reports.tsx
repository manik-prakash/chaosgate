import { useState } from "react";
import type { ReportEntry } from "../api";

export function Reports({ reports }: { reports?: ReportEntry[] }) {
  const [open, setOpen] = useState<string>();
  if (!reports) return <p className="muted">Loading reports…</p>;
  if (!reports.length) return <p className="muted">No reports yet. One is written after every gate run.</p>;
  const selected = open ?? reports[0].file;
  return (
    <div className="split">
      <table className="table" aria-label="Gate reports">
        <thead>
          <tr>
            <th>Time (UTC)</th>
            <th>Report</th>
            <th>Decision</th>
          </tr>
        </thead>
        <tbody>
          {reports.map((r) => (
            <tr key={r.file} className={r.file === selected ? "selected" : ""} onClick={() => setOpen(r.file)}>
              <td>{new Date(r.timestamp * 1000).toISOString().replace("T", " ").slice(0, 19)}</td>
              <td>
                <button className="link" onClick={() => setOpen(r.file)}>{r.file}</button>
              </td>
              <td>
                <span className={`pill pill-${r.decision === "PROMOTED" ? "ok" : r.decision === "ABORTED" ? "bad" : "warn"}`}>
                  {r.decision}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="viewer">
        <div className="viewer-head">
          <span className="muted small">{selected}</span>
          <a href={`/reports/${selected}`} target="_blank" rel="noreferrer">Open in new tab ↗</a>
        </div>
        <iframe title="Report" src={`/reports/${selected}`} />
      </div>
    </div>
  );
}
