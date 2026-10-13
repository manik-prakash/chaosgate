import type { Run } from "../api";
import { stages } from "../runs";

const ICON = { done: "✓", failed: "✕", active: "●", pending: "○" } as const;

export function Stepper({ run }: { run: Run }) {
  return (
    <ol className="stepper" aria-label="Gate progress">
      {stages(run).map((s) => (
        <li key={s.label} className={`step step-${s.stage}`}>
          <span className="step-icon" aria-hidden>
            {ICON[s.stage]}
          </span>
          <span>{s.label}</span>
        </li>
      ))}
    </ol>
  );
}
