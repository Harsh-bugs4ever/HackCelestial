"use client";

import type { ReactNode } from "react";
import { motion } from "motion/react";
import { EASE_OUT } from "@/components/motion";

/** A stat tile: one number, its label, an optional delta. Not a chart. */
export function StatTile({
  label,
  value,
  sub,
  delta,
  deltaGood,
  accent,
}: {
  label: string;
  /** ReactNode so a caller can pass <AnimatedNumber/> for a figure that ticks. */
  value: ReactNode;
  sub?: string;
  delta?: string;
  deltaGood?: boolean;
  accent?: string;
}) {
  return (
    <motion.div
      className="card stat-tile flex flex-col gap-2"
      style={accent ? { borderTop: `2px solid ${accent}` } : undefined}
      whileHover={{ y: -2 }}
      transition={{ duration: 0.25, ease: EASE_OUT }}
    >
      <div className="text-[12px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
        {label}
      </div>
      <div className="text-[28px] font-semibold leading-tight tabular">{value}</div>
      <div className="flex items-center gap-2 text-[12px]" style={{ color: "var(--text-secondary)" }}>
        {delta && (
          <span
            style={{ color: deltaGood ? "var(--delta-up)" : "var(--delta-down)" }}
            className="font-medium"
          >
            {delta}
          </span>
        )}
        {sub && <span>{sub}</span>}
      </div>
    </motion.div>
  );
}

export function Section({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-[17px] font-semibold tracking-tight">{title}</h2>
          {description && (
            <p className="text-[13px] mt-0.5" style={{ color: "var(--text-secondary)" }}>
              {description}
            </p>
          )}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function StatusPill({ status }: { status: "critical" | "watch" | "healthy" | string }) {
  const map: Record<string, { color: string; icon: string; label: string }> = {
    critical: { color: "var(--status-critical)", icon: "▲", label: "Critical" },
    watch: { color: "var(--status-warning)", icon: "●", label: "Watch" },
    healthy: { color: "var(--status-good)", icon: "✓", label: "Healthy" },
  };
  const s = map[status] ?? { color: "var(--text-muted)", icon: "○", label: status };
  return (
    <span
      className="inline-flex items-center gap-1 text-[12px] font-medium px-1.5 py-0.5 rounded"
      style={{ color: s.color, background: `color-mix(in oklab, ${s.color} 12%, transparent)` }}
    >
      <span aria-hidden>{s.icon}</span>
      {s.label}
    </span>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 text-[13px] py-8" style={{ color: "var(--text-muted)" }}>
      <span
        aria-hidden
        className="inline-block h-3.5 w-3.5 rounded-full border-2 animate-spin"
        style={{ borderColor: "var(--gridline)", borderTopColor: "var(--series-1)" }}
      />
      {label}…
    </div>
  );
}

/**
 * Shown above still-valid data when a background refresh fails. The dashboard
 * repolls every 20s; blanking a working screen because one poll timed out lost
 * the operator their context, so a stale screen now says it is stale instead.
 */
export function StaleBanner({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <motion.div
      role="status"
      className="card px-4 py-2.5 text-[12.5px] flex items-center gap-3 flex-wrap"
      style={{ borderLeft: "3px solid var(--status-warning)" }}
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.28, ease: EASE_OUT }}
    >
      <span style={{ color: "var(--text-primary)" }} className="font-medium">
        Showing the last good data — the latest refresh failed.
      </span>
      <span style={{ color: "var(--text-muted)" }} className="truncate">
        {error}
      </span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="btn-ghost ml-auto px-2.5 py-1 text-[12px] font-medium"
        >
          Retry now
        </button>
      )}
    </motion.div>
  );
}

export function ErrorNote({ error, hint }: { error: string; hint?: string }) {
  return (
    <div
      className="card p-4 text-[13px]"
      style={{ borderLeft: "3px solid var(--status-critical)" }}
      role="alert"
    >
      <button className="btn-ghost float-right px-3 py-2" onClick={() => window.location.reload()}>Try again</button>
      <div className="font-semibold mb-1">Could not reach the engine service</div>
      <div style={{ color: "var(--text-secondary)" }}>{error}</div>
      <div className="mt-2" style={{ color: "var(--text-muted)" }}>
        {hint ?? "The service is temporarily unavailable. Please try again in a moment."}
      </div>
    </div>
  );
}
