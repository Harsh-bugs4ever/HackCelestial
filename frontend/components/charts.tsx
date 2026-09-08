"use client";

import { useMemo, useState } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DeptSentiment, ForecastPayload } from "@/lib/api";
import { inr, pct, shortDate, titleCase } from "@/lib/format";

const AXIS = { fontSize: 11, fill: "var(--text-muted)" };

/** Shared tooltip shell so every chart hovers the same way. */
function TipShell({ title, rows }: { title: string; rows: [string, string, string?][] }) {
  return (
    <div
      className="rounded-md px-3 py-2 text-[12px] shadow-lg"
      style={{
        background: "var(--surface-2)",
        border: "1px solid var(--border)",
        color: "var(--text-primary)",
      }}
    >
      <div className="font-semibold mb-1">{title}</div>
      {rows.map(([label, value, color], i) => (
        <div key={i} className="flex items-center gap-2 justify-between">
          <span className="flex items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
            {color && (
              <span
                aria-hidden
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: color }}
              />
            )}
            {label}
          </span>
          <span className="tabular font-medium">{value}</span>
        </div>
      ))}
    </div>
  );
}

function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="flex items-center gap-4 flex-wrap text-[12px]" style={{ color: "var(--text-secondary)" }}>
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-0.5 w-4 rounded"
            style={{
              background: it.dashed
                ? `repeating-linear-gradient(90deg, ${it.color} 0 4px, transparent 4px 7px)`
                : it.color,
            }}
          />
          {it.label}
        </span>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------------------
 * Occupancy: 60 days actual + 30 days forecast, one axis, confidence band.
 * ----------------------------------------------------------------------- */
export function OccupancyChart({ data }: { data: ForecastPayload }) {
  const [showTable, setShowTable] = useState(false);

  const rows = useMemo(() => {
    const hist = data.history.map((h) => ({
      date: h.date,
      actual: h.occupancy,
      forecast: null as number | null,
      band: null as [number, number] | null,
    }));
    const cats = Object.values(data.by_category);
    const bandFor = (date: string): [number, number] | null => {
      if (!cats.length) return null;
      let lo = 0;
      let hi = 0;
      let n = 0;
      for (const series of cats) {
        const p = series.find((s) => s.date === date);
        if (p) {
          lo += p.lower;
          hi += p.upper;
          n += 1;
        }
      }
      return n ? [lo / n, hi / n] : null;
    };
    const fc = data.forecast.map((f) => ({
      date: f.date,
      actual: null as number | null,
      forecast: f.occupancy,
      band: bandFor(f.date),
    }));
    // join the two lines at the boundary so there is no visual gap
    if (hist.length && fc.length) fc.unshift({ ...hist[hist.length - 1], forecast: hist[hist.length - 1].actual, band: null });
    return [...hist, ...fc];
  }, [data]);

  const firstForecast = data.forecast[0]?.date;

  return (
    <figure className="card p-4">
      <figcaption className="mb-1">
        <h3 className="text-[15px] font-semibold">House occupancy — 60 days actual, 30 days forecast</h3>
        <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
          Model: {data.model.replace(/_/g, " ")}. Shaded band is the forecast interval.
        </p>
      </figcaption>

      <div className="my-2">
        <Legend
          items={[
            { label: "Actual occupancy", color: "var(--series-1)" },
            { label: "Forecast", color: "var(--series-2)", dashed: true },
          ]}
        />
      </div>

      <div style={{ height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
            <CartesianGrid vertical={false} />
            <XAxis
              dataKey="date"
              tick={AXIS}
              tickFormatter={shortDate}
              minTickGap={40}
              tickLine={false}
            />
            <YAxis
              tick={AXIS}
              tickFormatter={(v) => `${Math.round(v * 100)}%`}
              domain={[0, 1]}
              width={44}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ stroke: "var(--axis)", strokeWidth: 1 }}
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as (typeof rows)[number];
                const out: [string, string, string?][] = [];
                if (p.actual != null) out.push(["Actual", pct(p.actual, 1), "var(--series-1)"]);
                if (p.forecast != null) out.push(["Forecast", pct(p.forecast, 1), "var(--series-2)"]);
                if (p.band) out.push(["Interval", `${pct(p.band[0], 0)} – ${pct(p.band[1], 0)}`]);
                return <TipShell title={shortDate(String(label))} rows={out} />;
              }}
            />
            <Area
              dataKey={(d: (typeof rows)[number]) => d.band ?? undefined}
              stroke="none"
              fill="var(--series-2)"
              fillOpacity={0.12}
              isAnimationActive={false}
              connectNulls
            />
            {firstForecast && (
              <ReferenceLine
                x={firstForecast}
                stroke="var(--axis)"
                strokeDasharray="3 3"
                label={{ value: "today", position: "top", fontSize: 11, fill: "var(--text-muted)" }}
              />
            )}
            <Line
              dataKey="actual"
              stroke="var(--series-1)"
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
            <Line
              dataKey="forecast"
              stroke="var(--series-2)"
              strokeWidth={2}
              strokeDasharray="5 3"
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <TableToggle open={showTable} onToggle={() => setShowTable((v) => !v)} />
      {showTable && (
        <div className="mt-2 max-h-56 overflow-auto">
          <table className="w-full text-[12px] tabular">
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th className="text-left py-1">Date</th>
                <th className="text-right py-1">Occupancy</th>
                <th className="text-right py-1">Series</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                  <td className="py-1">{shortDate(r.date)}</td>
                  <td className="text-right py-1">{pct(r.actual ?? r.forecast ?? 0, 1)}</td>
                  <td className="text-right py-1" style={{ color: "var(--text-secondary)" }}>
                    {r.actual != null ? "actual" : "forecast"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </figure>
  );
}

function TableToggle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button
      className="mt-2 text-[12px] underline underline-offset-2"
      style={{ color: "var(--text-secondary)" }}
      onClick={onToggle}
      aria-expanded={open}
    >
      {open ? "Hide data table" : "View as table"}
    </button>
  );
}

/* --------------------------------------------------------------------------
 * Telemetry: small multiples, one axis each. Never two y-scales on one plot.
 * ----------------------------------------------------------------------- */
const METRICS = [
  { key: "vibration_mm_s", label: "Vibration", unit: "mm/s" },
  { key: "temperature_c", label: "Temperature", unit: "°C" },
  { key: "power_kw", label: "Power draw", unit: "kW" },
  { key: "pressure_bar", label: "Pressure", unit: "bar" },
] as const;

export function TelemetrySmallMultiples({
  readings,
}: {
  readings: { ts: string; vibration_mm_s: number; temperature_c: number; power_kw: number; pressure_bar: number }[];
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {METRICS.map((m) => {
        const values = readings.map((r) => r[m.key]);
        const latest = values[values.length - 1] ?? 0;
        const first = values[0] ?? 0;
        const rising = latest > first;
        return (
          <figure key={m.key} className="card p-3">
            <figcaption className="flex items-baseline justify-between mb-1">
              <h4 className="text-[13px] font-semibold">{m.label}</h4>
              <span className="text-[13px] tabular" style={{ color: "var(--text-primary)" }}>
                {latest.toFixed(2)}
                <span className="text-[11px]" style={{ color: "var(--text-muted)" }}> {m.unit}</span>
              </span>
            </figcaption>
            <div style={{ height: 92 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={readings} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="ts" hide />
                  <YAxis tick={{ ...AXIS, fontSize: 10 }} width={38} tickLine={false} axisLine={false} domain={["auto", "auto"]} />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const p = payload[0].payload as (typeof readings)[number];
                      return (
                        <TipShell
                          title={new Date(p.ts).toLocaleString("en-IN", {
                            day: "numeric",
                            month: "short",
                            hour: "2-digit",
                          })}
                          rows={[[m.label, `${p[m.key].toFixed(2)} ${m.unit}`, "var(--series-1)"]]}
                        />
                      );
                    }}
                  />
                  <Line
                    dataKey={m.key}
                    stroke={rising ? "var(--series-2)" : "var(--series-1)"}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </figure>
        );
      })}
    </div>
  );
}

/* --------------------------------------------------------------------------
 * Sentiment: diverging around zero, blue/red poles with a neutral midpoint.
 * ----------------------------------------------------------------------- */
export function SentimentChart({ data }: { data: Record<string, DeptSentiment> }) {
  const rows = Object.entries(data)
    .map(([dept, s]) => ({ dept: titleCase(dept), ...s }))
    .sort((a, b) => a.recent - b.recent);

  if (!rows.length) return null;

  return (
    <figure className="card p-4">
      <figcaption className="mb-2">
        <h3 className="text-[15px] font-semibold">Guest sentiment by department</h3>
        <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
          Last 10 days against a 90-day baseline. Negative bars are the departments the
          workforce engine is already reacting to.
        </p>
      </figcaption>

      <div style={{ height: Math.max(150, rows.length * 42) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 8 }}>
            <CartesianGrid horizontal={false} />
            <XAxis type="number" domain={[-1, 1]} tick={AXIS} tickLine={false} />
            <YAxis type="category" dataKey="dept" tick={AXIS} width={104} tickLine={false} axisLine={false} />
            <ReferenceLine x={0} stroke="var(--axis)" />
            <Tooltip
              cursor={{ fill: "color-mix(in oklab, var(--text-muted) 8%, transparent)" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as (typeof rows)[number];
                return (
                  <TipShell
                    title={p.dept}
                    rows={[
                      ["Recent", p.recent.toFixed(2)],
                      ["Baseline", p.baseline.toFixed(2)],
                      ["Change", `${p.delta >= 0 ? "+" : ""}${p.delta.toFixed(2)}`],
                      ["Negative share", pct(p.negative_share)],
                      ["Reviews", String(p.recent_count)],
                    ]}
                  />
                );
              }}
            />
            <Bar dataKey="recent" radius={4} barSize={18} isAnimationActive={false}>
              {rows.map((r, i) => (
                <Cell key={i} fill={r.recent >= 0 ? "var(--series-1)" : "var(--status-critical)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* direct labels double as the contrast relief */}
      <table className="w-full text-[12px] tabular mt-2">
        <thead>
          <tr style={{ color: "var(--text-muted)" }}>
            <th className="text-left py-1">Department</th>
            <th className="text-right py-1">Recent</th>
            <th className="text-right py-1">Baseline</th>
            <th className="text-right py-1">Change</th>
            <th className="text-right py-1">Reviews</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.dept} style={{ borderTop: "1px solid var(--border)" }}>
              <td className="py-1">{r.dept}</td>
              <td className="text-right py-1">{r.recent.toFixed(2)}</td>
              <td className="text-right py-1" style={{ color: "var(--text-secondary)" }}>
                {r.baseline.toFixed(2)}
              </td>
              <td
                className="text-right py-1"
                style={{ color: r.delta >= 0 ? "var(--delta-up)" : "var(--delta-down)" }}
              >
                {r.delta >= 0 ? "+" : ""}
                {r.delta.toFixed(2)}
              </td>
              <td className="text-right py-1" style={{ color: "var(--text-secondary)" }}>
                {r.recent_count}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

/* --------------------------------------------------------------------------
 * Simulator: baseline vs scenario, same axis, two bars per measure.
 * ----------------------------------------------------------------------- */
export function ScenarioChart({
  baseline,
  scenario,
}: {
  baseline: { revenue_inr: number; staff_cost_inr: number; profit_inr: number };
  scenario: { revenue_inr: number; staff_cost_inr: number; profit_inr: number };
}) {
  const rows = [
    { measure: "Room revenue", baseline: baseline.revenue_inr, scenario: scenario.revenue_inr },
    { measure: "Staff cost", baseline: baseline.staff_cost_inr, scenario: scenario.staff_cost_inr },
    { measure: "Contribution", baseline: baseline.profit_inr, scenario: scenario.profit_inr },
  ];

  return (
    <figure className="card p-4">
      <figcaption className="mb-2">
        <h3 className="text-[15px] font-semibold">Baseline vs scenario</h3>
      </figcaption>

      <div className="mb-2">
        <Legend
          items={[
            { label: "Baseline", color: "var(--series-1)" },
            { label: "Scenario", color: "var(--series-2)" },
          ]}
        />
      </div>

      <div style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: 8 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="measure" tick={AXIS} tickLine={false} />
            <YAxis
              tick={AXIS}
              width={58}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => inr(Number(v))}
            />
            <Tooltip
              cursor={{ fill: "color-mix(in oklab, var(--text-muted) 8%, transparent)" }}
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as (typeof rows)[number];
                return (
                  <TipShell
                    title={String(label)}
                    rows={[
                      ["Baseline", inr(p.baseline), "var(--series-1)"],
                      ["Scenario", inr(p.scenario), "var(--series-2)"],
                      ["Change", `${p.scenario - p.baseline >= 0 ? "+" : ""}${inr(p.scenario - p.baseline)}`],
                    ]}
                  />
                );
              }}
            />
            {/* 2px surface gap between adjacent bars */}
            <Bar dataKey="baseline" fill="var(--series-1)" radius={[4, 4, 0, 0]} barSize={34} isAnimationActive={false} />
            <Bar dataKey="scenario" fill="var(--series-2)" radius={[4, 4, 0, 0]} barSize={34} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
