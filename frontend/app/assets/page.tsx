"use client";

import { useCallback, useEffect, useState } from "react";
import { ActionCardView } from "@/components/ActionCardView";
import { TelemetrySmallMultiples } from "@/components/charts";
import { ErrorNote, Section, Spinner, StatusPill } from "@/components/ui";
import { api, type ActionCard, type AssetHealth } from "@/lib/api";
import { pct } from "@/lib/format";

type Telemetry = Awaited<ReturnType<typeof api.telemetry>>;

export default function AssetsPage() {
  const [assets, setAssets] = useState<AssetHealth[]>([]);
  const [orders, setOrders] = useState<
    { id: number; asset_id: string; scheduled_for: string; window_reason: string; status: string }[]
  >([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tele, setTele] = useState<Telemetry | null>(null);
  const [cards, setCards] = useState<ActionCard[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [a, c] = await Promise.all([api.assets(), api.actions("pending", "maintenance")]);
      setAssets(a.assets);
      setOrders(a.work_orders);
      setCards(c.cards);
      setSelected((s) => s ?? a.assets[0]?.id ?? null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setTele(null);
    api
      .telemetry(selected)
      .then((t) => !cancelled && setTele(t))
      .catch(() => !cancelled && setTele(null));
    return () => {
      cancelled = true;
    };
  }, [selected]);

  async function decide(id: number, d: "approve" | "snooze" | "dismiss") {
    await api.decide(id, d);
    await load();
  }

  if (error) return <ErrorNote error={error} />;
  if (!assets.length) return <Spinner label="Scoring asset telemetry" />;

  return (
    <div className="flex flex-col gap-7">
      <Section
        title="Predictive maintenance"
        description="Isolation Forest flags anomalous sensor states without labels; LightGBM and the trend model turn the drift into a failure probability. Service windows are taken from the demand forecast, not from a calendar."
      >
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,420px)_1fr] gap-4">
          <div className="card overflow-hidden">
            <table className="w-full text-[13px]">
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="text-left font-medium px-3 py-2">Asset</th>
                  <th className="text-right font-medium px-3 py-2">Risk</th>
                  <th className="text-right font-medium px-3 py-2">Days</th>
                  <th className="px-2" />
                </tr>
              </thead>
              <tbody className="tabular">
                {assets.map((a) => (
                  <tr
                    key={a.id}
                    onClick={() => setSelected(a.id)}
                    className="cursor-pointer"
                    style={{
                      borderTop: "1px solid var(--border)",
                      background: selected === a.id ? "var(--page-plane)" : undefined,
                    }}
                  >
                    <td className="px-3 py-2">
                      <div className="font-medium">{a.name}</div>
                      <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
                        {a.location} · serves {a.rooms_served || "—"} rooms
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right">{pct(a.risk)}</td>
                    <td className="px-3 py-2 text-right">{a.days_to_failure}</td>
                    <td className="px-2 py-2 text-right">
                      <StatusPill status={a.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-col gap-3">
            {tele ? (
              <>
                <div className="card p-4">
                  <div className="flex items-baseline justify-between flex-wrap gap-2">
                    <div>
                      <h3 className="text-[15px] font-semibold">{tele.asset.name}</h3>
                      <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                        {tele.asset.location} · risk model: {tele.method}
                      </p>
                    </div>
                    <div className="text-right">
                      <div className="text-xl font-semibold tabular">{pct(tele.risk)}</div>
                      <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                        failure risk · {tele.days_to_failure} days · {pct(tele.anomaly_rate)} anomalous readings
                      </div>
                    </div>
                  </div>
                </div>
                <TelemetrySmallMultiples readings={tele.readings} />
              </>
            ) : (
              <Spinner label="Loading telemetry" />
            )}
          </div>
        </div>
      </Section>

      {cards.length > 0 && (
        <Section title="Maintenance recommendations" description="Each service window is the lowest-occupancy night the forecast could find.">
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {cards.map((c) => (
              <ActionCardView key={c.id} card={c} onDecide={decide} />
            ))}
          </div>
        </Section>
      )}

      {orders.length > 0 && (
        <Section title="Work orders raised" description="Written by the action bus when a maintenance card was approved.">
          <div className="card overflow-x-auto">
            <table className="w-full text-[13px] min-w-[640px]">
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="text-left font-medium px-3 py-2">#</th>
                  <th className="text-left font-medium px-3 py-2">Asset</th>
                  <th className="text-left font-medium px-3 py-2">Scheduled</th>
                  <th className="text-left font-medium px-3 py-2">Why this window</th>
                  <th className="text-left font-medium px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((w) => (
                  <tr key={w.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td className="px-3 py-2 tabular">{w.id}</td>
                    <td className="px-3 py-2">{assets.find((a) => a.id === w.asset_id)?.name ?? w.asset_id}</td>
                    <td className="px-3 py-2 tabular">
                      {new Date(w.scheduled_for).toLocaleString("en-IN", {
                        weekday: "short",
                        day: "numeric",
                        month: "short",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </td>
                    <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                      {w.window_reason}
                    </td>
                    <td className="px-3 py-2">{w.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
