"use client";

import { useCallback, useEffect, useState } from "react";
import { ScenarioChart } from "@/components/charts";
import { ErrorNote, Section, Spinner, StatTile } from "@/components/ui";
import { api, type Simulation } from "@/lib/api";
import { inr, pct, trend } from "@/lib/format";

export default function SimulatorPage() {
  const [rate, setRate] = useState(0);
  const [staffing, setStaffing] = useState(0);
  const [promo, setPromo] = useState(0);
  const [horizon, setHorizon] = useState(14);
  const [sim, setSim] = useState<Simulation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const run = useCallback(async () => {
    setPending(true);
    try {
      setSim(
        await api.simulate({
          rate_change_pct: rate,
          staffing_change_pct: staffing,
          promotion_discount_pct: promo,
          horizon_days: horizon,
        }),
      );
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }, [rate, staffing, promo, horizon]);

  // debounce so dragging a slider does not hammer the forecast
  useEffect(() => {
    const t = setTimeout(() => void run(), 260);
    return () => clearTimeout(t);
  }, [run]);

  if (error) return <ErrorNote error={error} />;

  return (
    <div className="flex flex-col gap-7">
      <Section
        title="Revenue-impact simulator"
        description="Move price, staffing or a promotion and see projected occupancy, revenue and guest satisfaction before committing. The elasticities are stated, so the projection can be audited rather than trusted."
      >
        <div className="card p-5 flex flex-col gap-5">
          <Slider
            label="Rate change"
            value={rate}
            min={-40}
            max={60}
            onChange={setRate}
            format={(v) => `${v > 0 ? "+" : ""}${v}%`}
          />
          <Slider
            label="Staffing change"
            value={staffing}
            min={-50}
            max={60}
            onChange={setStaffing}
            format={(v) => `${v > 0 ? "+" : ""}${v}%`}
          />
          <Slider
            label="Promotion discount"
            value={promo}
            min={0}
            max={40}
            onChange={setPromo}
            format={(v) => `${v}%`}
          />
          <Slider
            label="Horizon"
            value={horizon}
            min={1}
            max={45}
            onChange={setHorizon}
            format={(v) => `${v} days`}
          />

          <button
            onClick={() => {
              setRate(0);
              setStaffing(0);
              setPromo(0);
            }}
            className="self-start text-[13px] underline underline-offset-2"
            style={{ color: "var(--text-secondary)" }}
          >
            Reset to baseline
          </button>
        </div>
      </Section>

      {!sim ? (
        <Spinner label="Projecting the scenario" />
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3" style={{ opacity: pending ? 0.6 : 1 }}>
            <StatTile
              label="Revenue change"
              value={inr(sim.delta.revenue_inr)}
              {...trend(sim.delta.revenue_inr, 0.5)}
              sub={`over ${sim.horizon_days} days`}
              accent="var(--series-1)"
            />
            <StatTile
              label="Contribution change"
              value={inr(sim.delta.profit_inr)}
              {...trend(sim.delta.profit_inr, 0.5)}
              sub="after staff cost"
              accent="var(--series-2)"
            />
            <StatTile
              label="Occupancy change"
              value={`${sim.delta.occupancy_points >= 0 ? "+" : ""}${sim.delta.occupancy_points.toFixed(1)} pts`}
              sub={`${pct(sim.baseline.mean_occupancy)} → ${pct(sim.scenario.mean_occupancy)}`}
              accent="var(--series-3)"
            />
            <StatTile
              label="Guest satisfaction"
              value={`${sim.delta.guest_satisfaction >= 0 ? "+" : ""}${sim.delta.guest_satisfaction.toFixed(2)}`}
              {...trend(sim.delta.guest_satisfaction, 0.005)}
              sub="service-load proxy"
              accent="var(--engine-guest)"
            />
          </div>

          <ScenarioChart baseline={sim.baseline} scenario={sim.scenario} />

          <div className="card p-4">
            <h3 className="text-[14px] font-semibold mb-2">Assumptions behind this projection</h3>
            <ul className="text-[13px] flex flex-col gap-1" style={{ color: "var(--text-secondary)" }}>
              <li>
                Price elasticity <span className="tabular font-medium">{sim.assumptions.price_elasticity}</span> — a 10% rate rise
                sheds roughly 6% of demand.
              </li>
              <li>
                Promotion conversion <span className="tabular font-medium">{sim.assumptions.promotion_conversion}</span> — share of the
                discount that becomes new demand rather than discounting existing bookings.
              </li>
              <li>
                Service sensitivity <span className="tabular font-medium">{sim.assumptions.service_sensitivity}</span> — occupancy lost
                per unit of understaffing.
              </li>
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  onChange,
  format,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
  format: (v: number) => string;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-baseline justify-between text-[13px]">
        <span style={{ color: "var(--text-secondary)" }}>{label}</span>
        <span className="tabular font-semibold text-[15px]">{format(value)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
        style={{ accentColor: "var(--series-1)" }}
      />
    </label>
  );
}
