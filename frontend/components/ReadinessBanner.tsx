"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Readiness } from "@/lib/api";

const STAGE_COLOR: Record<string, string> = {
  cold: "var(--status-critical)",
  learning: "var(--status-warning)",
  trusted: "var(--status-good)",
};

const STAGE_LABEL: Record<string, string> = {
  cold: "Not enough data",
  learning: "Still learning",
  trusted: "Full history",
};

/**
 * Honesty about what the models actually know.
 *
 * The seeded demo has two years of history because Prophet needs two full
 * cycles to separate yearly seasonality from trend. A real property on day one
 * has none of that, and every engine will still emit a card with a confident
 * -looking score attached. This says so, out loud, above the numbers.
 *
 * It stays silent once every engine is trusted - a permanent banner is one
 * people stop reading.
 */
export function ReadinessBanner() {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    // Best-effort: a failure here must never take the dashboard with it.
    api.readiness().then(setReadiness).catch(() => setReadiness(null));
  }, []);

  if (!readiness || readiness.overall === "trusted") return null;

  const accent = STAGE_COLOR[readiness.overall];

  return (
    <div
      className="card px-4 py-3 flex flex-col gap-2 text-[12.5px]"
      style={{ borderLeft: `3px solid ${accent}` }}
      role="status"
    >
      <div className="flex items-start gap-3 flex-wrap">
        <span aria-hidden style={{ color: accent }} className="font-semibold">
          {readiness.overall === "cold" ? "▲" : "●"}
        </span>
        <div className="flex-1 min-w-[200px]">
          <div className="font-semibold" style={{ color: "var(--text-primary)" }}>
            {readiness.needs_onboarding
              ? "No operating data loaded yet"
              : "Recommendations are provisional"}
          </div>
          <p className="mt-0.5" style={{ color: "var(--text-secondary)" }}>
            {readiness.needs_onboarding
              ? "The engines have nothing to learn from. Import your bookings, staff and assets to get started."
              : readiness.headline}
          </p>
        </div>
        <div className="flex gap-2 items-center shrink-0">
          {readiness.needs_onboarding && (
            <Link href="/import" className="btn-ghost px-3 py-1.5 text-[12px] font-medium">
              Import data
            </Link>
          )}
          <button
            className="btn-ghost px-3 py-1.5 text-[12px] font-medium"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            {expanded ? "Hide detail" : "What's missing?"}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
          {Object.entries(readiness.engines).map(([key, engine]) => (
            <div key={key} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-medium">{engine.label}</span>
                <span style={{ color: STAGE_COLOR[engine.stage] }} className="text-[11px] font-medium">
                  {STAGE_LABEL[engine.stage]}
                </span>
              </div>
              <div className="readiness-bar">
                <span
                  style={{
                    width: `${Math.round(engine.progress * 100)}%`,
                    background: STAGE_COLOR[engine.stage],
                  }}
                />
              </div>
              <div style={{ color: "var(--text-muted)" }}>
                {engine.have.toLocaleString()} of {engine.trusted.toLocaleString()} {engine.unit}
                {engine.shortfall > 0 && ` — ${engine.shortfall.toLocaleString()} short`}
              </div>
              <div style={{ color: "var(--text-secondary)" }}>{engine.why}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
