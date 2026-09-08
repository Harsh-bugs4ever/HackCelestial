"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ActionCardView } from "@/components/ActionCardView";
import { ErrorNote, Section, Spinner, StatTile, StatusPill } from "@/components/ui";
import { API_BASE, api, type Dashboard } from "@/lib/api";
import { inr, inrExact, pct, titleCase } from "@/lib/format";

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [live, setLive] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.dashboard());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const poll = setInterval(load, 20000);

    // live gateway: new cards arrive without a refresh
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(`${API_BASE.replace(/^http/, "ws")}/ws`);
      ws.onopen = () => setLive(true);
      ws.onclose = () => setLive(false);
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data as string) as { type?: string };
        if (msg.type === "actions.new") void load();
      };
    } catch {
      setLive(false);
    }
    return () => {
      clearInterval(poll);
      ws?.close();
    };
  }, [load]);

  async function decide(id: number, decision: "approve" | "snooze" | "dismiss") {
    await api.decide(id, decision);
    await load();
  }

  async function runEngines() {
    setRunning(true);
    try {
      await api.runEngines();
      await load();
    } finally {
      setRunning(false);
    }
  }

  if (error) return <ErrorNote error={error} />;
  if (!data) return <Spinner label="Loading the live dashboard" />;

  const occ = data.occupancy;

  return (
    <div className="flex flex-col gap-7">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{data.resort}</h1>
          <p className="text-[13px]" style={{ color: "var(--text-secondary)" }}>
            {new Date(data.date).toLocaleDateString("en-IN", {
              weekday: "long",
              day: "numeric",
              month: "long",
              year: "numeric",
            })}
            {" · "}
            <span style={{ color: live ? "var(--status-good)" : "var(--text-muted)" }}>
              {live ? "● live" : "○ polling"}
            </span>
          </p>
        </div>
        <button
          onClick={runEngines}
          disabled={running}
          className="rounded-md px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
          style={{ background: "var(--series-1)" }}
        >
          {running ? "Running engines…" : "Run all engines now"}
        </button>
      </div>

      {/* Live status - occupancy, staffing gaps, open requests, equipment health */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile
          label="Occupancy today"
          value={pct(occ.pct)}
          sub={`${occ.rooms_sold} of ${occ.rooms_available} rooms`}
          accent="var(--series-1)"
        />
        <StatTile
          label="ADR / RevPAR"
          value={inrExact(occ.adr)}
          sub={`RevPAR ${inrExact(occ.revpar)}`}
          accent="var(--series-1)"
        />
        <StatTile
          label="Open guest requests"
          value={String(data.requests.open)}
          sub={`${data.requests.high_priority} high priority`}
          accent="var(--engine-guest)"
        />
        <StatTile
          label="Staffing gaps"
          value={String(data.staffing.gaps)}
          sub={`${data.staffing.short_by} shifts short over 2 days`}
          accent="var(--engine-workforce)"
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
            {data.actions.top.map((c) => (
              <ActionCardView key={c.id} card={c} onDecide={decide} compact />
            ))}
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
                .map(([dept, s]) => (
                  <div key={dept} className="flex items-center gap-3 text-[13px]">
                    <span className="w-28 shrink-0" style={{ color: "var(--text-secondary)" }}>
                      {titleCase(dept)}
                    </span>
                    <span
                      className="flex-1 h-1.5 rounded-full relative overflow-hidden"
                      style={{ background: "var(--gridline)" }}
                    >
                      <span
                        className="absolute top-0 h-full rounded-full"
                        style={{
                          left: s.recent >= 0 ? "50%" : `${50 + s.recent * 50}%`,
                          width: `${Math.abs(s.recent) * 50}%`,
                          background:
                            s.recent >= 0 ? "var(--series-1)" : "var(--status-critical)",
                        }}
                      />
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
    </div>
  );
}
