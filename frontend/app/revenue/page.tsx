"use client";

import { useCallback, useEffect, useState } from "react";
import { ActionCardView } from "@/components/ActionCardView";
import { OccupancyChart } from "@/components/charts";
import { ErrorNote, Section, Spinner, StatTile } from "@/components/ui";
import { api, type ActionCard, type ForecastPayload } from "@/lib/api";
import { inr, pct } from "@/lib/format";

export default function RevenuePage() {
  const [fc, setFc] = useState<ForecastPayload | null>(null);
  const [cards, setCards] = useState<ActionCard[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [f, a] = await Promise.all([api.forecast(30), api.actions("pending", "demand")]);
      setFc(f);
      setCards(a.cards);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(id: number, d: "approve" | "snooze" | "dismiss") {
    await api.decide(id, d);
    await load();
  }

  if (error) return <ErrorNote error={error} />;
  if (!fc) return <Spinner label="Fitting the demand model" />;

  const next7 = fc.forecast.slice(0, 7);
  const meanOcc = next7.reduce((a, r) => a + r.occupancy, 0) / Math.max(next7.length, 1);
  const peak = [...fc.forecast].sort((a, b) => b.occupancy - a.occupancy)[0];
  const trough = [...fc.forecast].sort((a, b) => a.occupancy - b.occupancy)[0];
  const upside = cards.reduce((a, c) => a + Math.max(c.impact_inr, 0), 0);

  return (
    <div className="flex flex-col gap-7">
      <Section
        title="Demand & Revenue engine"
        description="Prophet carries trend, weekly and yearly seasonality and the local-event calendar; XGBoost learns the residual from on-the-books pickup, competitor sell-outs and lead-time mix. Rates move inside floor/ceiling guardrails."
      >
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatTile label="Mean occupancy, next 7 days" value={pct(meanOcc)} accent="var(--series-1)" />
          <StatTile
            label="Peak night"
            value={peak ? pct(peak.occupancy) : "—"}
            sub={peak ? new Date(peak.date).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" }) : ""}
            accent="var(--series-1)"
          />
          <StatTile
            label="Softest night"
            value={trough ? pct(trough.occupancy) : "—"}
            sub={trough ? new Date(trough.date).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" }) : ""}
            accent="var(--series-2)"
          />
          <StatTile label="Pricing upside on the table" value={inr(upside)} sub={`${cards.length} open rate cards`} accent="var(--status-good)" />
        </div>
      </Section>

      <OccupancyChart data={fc} />

      <Section
        title="Rate recommendations"
        description="Each card states the rate, the forecast behind it, and every driver that moved the number."
      >
        {cards.length === 0 ? (
          <div className="card p-6 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            No open rate recommendations.
          </div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {cards.map((c) => (
              <ActionCardView key={c.id} card={c} onDecide={decide} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
