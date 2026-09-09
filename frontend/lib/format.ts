/** Indian-format currency and the small formatters the UI reuses. */

export function inr(value: number, opts: { compact?: boolean } = {}): string {
  const abs = Math.abs(value);
  if (opts.compact !== false) {
    if (abs >= 1_00_00_000) return `${sign(value)}₹${(abs / 1_00_00_000).toFixed(2)} Cr`;
    if (abs >= 1_00_000) return `${sign(value)}₹${(abs / 1_00_000).toFixed(2)} L`;
    if (abs >= 1_000) return `${sign(value)}₹${(abs / 1_000).toFixed(1)}k`;
  }
  return `${sign(value)}₹${abs.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function sign(v: number): string {
  return v < 0 ? "-" : "";
}

export function inrExact(value: number): string {
  return `₹${Math.round(value).toLocaleString("en-IN")}`;
}

export function pct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function shortDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function weekday(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { weekday: "short" });
}

export function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export const ENGINE_LABEL: Record<string, string> = {
  demand: "Demand & Revenue",
  maintenance: "Predictive Maintenance",
  workforce: "Workforce Optimizer",
  guest: "Guest Intelligence",
};

export const ENGINE_COLOR: Record<string, string> = {
  demand: "var(--engine-demand)",
  maintenance: "var(--engine-maintenance)",
  workforce: "var(--engine-workforce)",
  guest: "var(--engine-guest)",
};

export const URGENCY_COLOR: Record<string, string> = {
  critical: "var(--status-critical)",
  high: "var(--status-serious)",
  normal: "var(--status-warning)",
  low: "var(--text-muted)",
};

export function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Direction marker for a delta, for StatTile's delta/deltaGood pair.
 *
 * A zero delta returns neither: an up-arrow against "no change" reads as an
 * improvement that did not happen. `epsilon` should match the precision the
 * value is rendered at, so a delta that displays as 0 is not marked as a move.
 */
export function trend(
  value: number,
  epsilon = 0,
): { delta?: string; deltaGood?: boolean } {
  if (!Number.isFinite(value) || Math.abs(value) <= epsilon) return {};
  return { delta: value > 0 ? "▲" : "▼", deltaGood: value > 0 };
}
