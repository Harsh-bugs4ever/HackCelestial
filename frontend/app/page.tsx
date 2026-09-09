"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence } from "motion/react";
import { ActionCardView } from "@/components/ActionCardView";
import { ResortHero } from "@/components/ResortHero";
import { AnimatedNumber, GrowBar, Stagger } from "@/components/motion";
import { ErrorNote, Section, Spinner, StaleBanner, StatTile, StatusPill } from "@/components/ui";
import { useLiveData } from "@/lib/useLiveData";
import { API_BASE, api } from "@/lib/api";
import { inr, pct, titleCase } from "@/lib/format";

const intFmt = (v: number) => String(Math.round(v));
const score2 = (v: number) => v.toFixed(2);

export default function DashboardPage() {
  const { data, error, loading, stale, reload } = useLiveData(api.dashboard, {
    intervalMs: 20000,
  });
  const [running, setRunning] = useState(false);
  const [live, setLive] = useState(false);

  useEffect(() => {
    // live gateway: new cards arrive without waiting for the next poll
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`${API_BASE.replace(/^http/, "ws")}/ws`);
      ws.onopen = () => setLive(true);
      ws.onclose = () => setLive(false);
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data as string) as { type?: string };
        if (msg.type === "actions.new") void reload();
      };
    } catch {
      setLive(false);
    }
    return () => ws?.close();
  }, [reload]);

  async function decide(id: number, decision: "approve" | "snooze" | "dismiss") {
    await api.decide(id, decision);
    await reload();
  }

  async function runEngines() {
    setRunning(true);
    try {
      await api.runEngines();
      await reload();
    } finally {
      setRunning(false);
    }
  }

  if (loading && !data) return <Spinner label="Loading the live dashboard" />;
  if (!data) return <ErrorNote error={error ?? "No data returned"} />;

  return (
    <Stagger className="flex flex-col gap-7">
      <AnimatePresence>
        {stale && <StaleBanner key="stale" error={error!} onRetry={() => void reload()} />}
      </AnimatePresence>

      <ResortHero data={data} live={live} running={running} onRun={runEngines} />

      {/* Live status - occupancy, staffing gaps, open requests, equipment health */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile
          label="Arrivals today"
          value={<AnimatedNumber value={data.movements.arrivals} format={intFmt} />}
          sub={`${data.movements.departures} departures`}
          accent="var(--series-1)"
        />
        <StatTile
          label="Open guest requests"
          value={<AnimatedNumber value={data.requests.open} format={intFmt} />}
          sub={`${data.requests.high_priority} high priority`}
          accent="var(--engine-guest)"
        />
        <StatTile
          label="Staffing gaps"
          value={<AnimatedNumber value={data.staffing.gaps} format={intFmt} />}
          sub={`${data.staffing.short_by} shifts short over 2 days`}
          accent="var(--engine-workforce)"
        />
        <StatTile
          label="Guest sentiment"
          value={<AnimatedNumber value={data.sentiment.overall} format={score2} />}
          sub="mean across departments"
          accent="var(--engine-maintenance)"
        />
      </div>

      {/* The action bus - the differentiator, above everything else */}
      <Section
        title="Action queue"
        description={`${data.actions.pending} recommendations waiting, ${inr(
          data.actions.total_impact_inr,
        )} of combined impact. AI recommends, you decide, the system executes.`}
        action={
          <Link
            href="/actions"
            className="text-[13px] font-medium underline underline-offset-2"
            style={{ color: "var(--text-secondary)" }}
          >
            See all {data.actions.pending} →
          </Link>
        }
      >
        {data.actions.top.length === 0 ? (
          <div className="card p-6 text-[13px]" style={{ color: "var(--text-secondary)" }}>
            No open recommendations. Run the engines to generate the next batch.
          </div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {/* AnimatePresence lets a decided card play its exit before the
                refreshed queue closes the gap over it. */}
            <AnimatePresence initial={false} mode="popLayout">
              {data.actions.top.map((c) => (
                <ActionCardView key={c.id} card={c} onDecide={decide} compact />
              ))}
            </AnimatePresence>
          </div>
        )}
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Equipment health */}
        <Section
          title="Equipment health"
          description={`${data.asset_health.critical} critical, ${data.asset_health.watch} on watch, ${data.asset_health.healthy} healthy.`}
          action={
            <Link
              href="/assets"
              className="text-[13px] underline underline-offset-2"
              style={{ color: "var(--text-secondary)" }}
            >
              All assets →
            </Link>
          }
        >
          <div className="card overflow-hidden">
            <table className="w-full text-[13px]">
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="text-left font-medium px-3 py-2">Asset</th>
                  <th className="text-right font-medium px-3 py-2">Risk</th>
                  <th className="text-right font-medium px-3 py-2">Days</th>
                  <th className="text-right font-medium px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {data.asset_health.worst.map((a) => (
                  <tr key={a.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td className="px-3 py-2">
                      <div className="font-medium">{a.name}</div>
                      <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
                        {a.location}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right">{pct(a.risk)}</td>
                    <td className="px-3 py-2 text-right">{a.days_to_failure}</td>
                    <td className="px-3 py-2 text-right">
                      <StatusPill status={a.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        {/* Requests + sentiment */}
        <Section
          title="Service load"
          description="Open requests by department, and how guests are describing each one."
        >
          <div className="card p-4 flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {Object.entries(data.requests.by_department).map(([dept, n]) => (
                <span
                  key={dept}
                  className="text-[12.5px] px-2 py-1 rounded-md"
                  style={{ background: "var(--page-plane)", color: "var(--text-secondary)" }}
                >
                  {titleCase(dept)} <span className="tabular font-semibold">{n}</span>
                </span>
              ))}
            </div>

            <div className="flex flex-col gap-2 pt-1">
              {Object.entries(data.sentiment.by_department)
                .sort((a, b) => a[1].recent - b[1].recent)
                .slice(0, 5)
                .map(([dept, s], i) => (
                  <div key={dept} className="flex items-center gap-3 text-[13px]">
                    <span className="w-28 shrink-0" style={{ color: "var(--text-secondary)" }}>
                      {titleCase(dept)}
                    </span>
                    {/* Diverging bar. The half it grows into is fixed by the
                        sign, so the bar animates outward from the zero line at
                        the centre and direction reads before the number does. */}
                    <span
                      className="flex-1 h-1.5 rounded-full relative overflow-hidden"
                      style={{ background: "var(--gridline)" }}
                    >
                      <span
                        className="absolute top-0 bottom-0 flex"
                        style={{
                          left: s.recent >= 0 ? "50%" : 0,
                          right: s.recent >= 0 ? 0 : "50%",
                          justifyContent: s.recent >= 0 ? "flex-start" : "flex-end",
                        }}
                      >
                        <GrowBar
                          fraction={Math.min(1, Math.abs(s.recent))}
                          color={s.recent >= 0 ? "var(--series-1)" : "var(--status-critical)"}
                          delay={0.05 * i}
                        />
                      </span>
                    </span>
                    <span
                      className="w-14 text-right tabular"
                      style={{
                        color: s.delta >= 0 ? "var(--delta-up)" : "var(--delta-down)",
                      }}
                    >
                      {s.recent.toFixed(2)}
                    </span>
                  </div>
                ))}
            </div>

            <Link
              href="/guests"
              className="text-[13px] underline underline-offset-2 mt-1"
              style={{ color: "var(--text-secondary)" }}
            >
              Guest intelligence →
            </Link>
          </div>
        </Section>
      </div>
    </Stagger>
  );
}
