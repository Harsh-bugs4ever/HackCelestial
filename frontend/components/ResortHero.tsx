"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import type { Dashboard } from "@/lib/api";
import { inr, inrExact, pct } from "@/lib/format";

// three.js touches window at import time, so keep it off the server render.
const ResortScene = dynamic(() => import("@/components/ResortScene"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center">
      <div className="shimmer h-40 w-40 rounded-2xl" />
    </div>
  ),
});

function Figure({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub: string;
  accent: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="eyebrow" style={{ color: "rgba(190, 205, 225, 0.72)" }}>
        {label}
      </div>
      <div
        className="text-[26px] font-semibold leading-none tabular"
        style={{ color: "#f2f6fb" }}
      >
        {value}
      </div>
      <div className="text-[12px]" style={{ color: accent }}>
        {sub}
      </div>
    </div>
  );
}

export function ResortHero({
  data,
  live,
  running,
  onRun,
}: {
  data: Dashboard;
  live: boolean;
  running: boolean;
  onRun: () => void;
}) {
  const occ = data.occupancy;
  const health = data.asset_health;

  return (
    <section className="hero">
      <div className="hero-grid" />

      <div className="relative grid gap-6 p-6 lg:grid-cols-[1.05fr_1fr] lg:p-8">
        {/* Left: the narrative and the numbers */}
        <div className="flex flex-col justify-between gap-6">
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <span className="eyebrow" style={{ color: "rgba(190, 205, 225, 0.72)" }}>
                PROPERTY OVERVIEW
              </span>
              <span className="flex items-center gap-1.5 text-[11px]" style={{ color: "#9fb2c8" }}>
                {live ? <span className="live-dot" /> : <span>○</span>}
                {live ? "streaming" : "polling"}
              </span>
            </div>

            <h2
              className="text-[30px] font-semibold leading-[1.1] tracking-tight lg:text-[36px]"
              style={{ color: "#f7fafd" }}
            >
              {data.resort}
            </h2>

            <p className="max-w-[46ch] text-[13.5px] leading-relaxed" style={{ color: "#9fb2c8" }}>
              Your property at a glance. Track today's performance, spot what needs
              attention, and turn recommendations into better guest experiences.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-5 sm:grid-cols-3">
            <Figure
              label="Occupancy"
              value={pct(occ.pct)}
              sub={`${occ.rooms_sold}/${occ.rooms_available} rooms`}
              accent="#6ea8f0"
            />
            <Figure
              label="RevPAR"
              value={inrExact(occ.revpar)}
              sub={`ADR ${inrExact(occ.adr)}`}
              accent="#6ea8f0"
            />
            <Figure
              label="Pending impact"
              value={inr(data.actions.total_impact_inr)}
              sub={`${data.actions.pending} recommendations`}
              accent="#f0a273"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <button
              onClick={onRun}
              disabled={running}
              className="btn-primary px-4 py-2.5 text-[13px] disabled:opacity-55"
            >
              {running ? "Running engines…" : "Refresh insights"}
            </button>
            <Link
              href="/actions"
              className="rounded-[10px] px-4 py-2.5 text-[13px] font-medium transition-colors"
              style={{
                color: "#cfdcec",
                border: "1px solid rgba(160, 185, 215, 0.24)",
              }}
            >
              Review actions →
            </Link>

            <span className="ml-1 text-[12px]" style={{ color: "#7f93ab" }}>
              {health.critical} critical · {health.watch} watch · {health.healthy} healthy
            </span>
          </div>
        </div>

        {/* Right: the model itself */}
        <div className="relative h-[240px] lg:h-[280px]">
          <ResortScene
            roomsSold={occ.rooms_sold}
            roomsAvailable={occ.rooms_available}
            assets={health.worst}
          />
          <div
            className="pointer-events-none absolute bottom-2 right-3 text-[11px]"
            style={{ color: "rgba(150, 172, 196, 0.6)" }}
          >
            drag to orbit · hover a marker
          </div>
        </div>
      </div>
    </section>
  );
}
