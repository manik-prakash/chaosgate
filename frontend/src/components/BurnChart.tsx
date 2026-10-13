import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Run } from "../api";
import { chartData } from "../runs";

export function BurnChart({ run }: { run: Run }) {
  const data = chartData(run);
  const peak = Math.max(run.threshold * 1.5, ...data.map((d) => d.burn));
  return (
    <div className="chart" role="img" aria-label="Error budget burn rate over time against the threshold">
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis dataKey="t" unit="s" stroke="var(--muted)" tick={{ fontSize: 12 }} />
          <YAxis
            domain={[0, peak]}
            stroke="var(--muted)"
            tick={{ fontSize: 12 }}
            tickFormatter={(v: number) => v.toFixed(3)}
            width={52}
          />
          <Tooltip
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }}
            formatter={(v: number) => [v.toFixed(4), "burn rate"]}
            labelFormatter={(t) => `${t}s`}
          />
          <ReferenceLine
            y={run.threshold}
            stroke="var(--bad)"
            strokeDasharray="6 4"
            label={{ value: `threshold ${run.threshold}`, fill: "var(--bad)", fontSize: 12, position: "insideTopRight" }}
          />
          <Line
            type="monotone"
            dataKey="burn"
            stroke="var(--accent)"
            strokeWidth={2.5}
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
