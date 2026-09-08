"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorNote, Section, Spinner, StatTile } from "@/components/ui";
import { api, type LearningSummary } from "@/lib/api";
import { inr, pct, titleCase } from "@/lib/format";

const ENGINE_COLOR: Record<string, string> = {
  demand: "var(--engine-demand)",
  maintenance: "var(--engine-maintenance)",
  workforce: "var(--engine-workforce)",
  guest: "var(--engine-guest)",
};

type DecisionRow = {
  id: number;
  action_card_id: number;
  engine: string;
  decision: string;
  predicted_impact_inr: number;
  confidence: number;
  decided_by: string;
  at: string | null;
  realised_impact_inr: number | null;
};

export default function LearningPage() {
  const [data, setData] = useState<LearningSummary | null>(null);
  const [decisions, setDecisions] = useState<DecisionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [scoring, setScoring] = useState(false);

  const load = useCallback(async () => {
    try {
      const [l, d] = await Promise.all([
        api.learning(),
        api.decisions(),
      ]);
      setData(l);
      setDecisions(d.decisions);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function scoreOutcomes() {
    setScoring(true);
    try {
      const result = await api.observe();
      setData(result.summary);
      await load();
    } finally {
      setScoring(false);
    }
  }

  if (error) return <ErrorNote error={error} />;
  if (!data) return <Spinner label="Loading the feedback loop" />;

  const t = data.totals;
  const engines = Object.entries(data.engines).sort(
    ([, a], [, b]) => b.proposed - a.proposed
  );
  const signals = Object.entries(data.signal);

  return (
    <div className="flex flex-col gap-7">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Feedback Loop</h1>
          <p
            className="text-[13px] mt-0.5"
            style={{ color: "var(--text-secondary)" }}
          >
            Every decision is a training signal. Accepted recommendations are scored
            against what actually happened — and the next batch gets sharper.
          </p>
        </div>
        <button
          onClick={scoreOutcomes}
          disabled={scoring}
          className="rounded-md px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
          style={{ background: "var(--series-3)" }}
        >
          {scoring ? "Scoring…" : "Score outcomes now"}
        </button>
      </div>

      {/* Totals */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile
          label="Total decisions"
          value={String(t.decisions)}
          sub={`${t.approved} approved`}
          accent="var(--series-1)"
        />
        <StatTile
          label="Acceptance rate"
          value={t.acceptance_rate != null ? pct(t.acceptance_rate) : "—"}
          sub="approved ÷ proposed"
          accent="var(--series-1)"
        />
        <StatTile
          label="Predicted impact"
          value={inr(t.predicted_inr)}
          sub="across all approved cards"
          accent="var(--engine-demand)"
        />
        <StatTile
          label="Realised impact"
          value={inr(t.realised_inr)}
          sub={`${t.outcomes_scored} outcomes scored`}
          accent="var(--status-good)"
        />
      </div>

      {/* Per-engine breakdown */}
      <Section
        title="Engine performance"
        description="Each engine's recommendation quality, measured by manager acceptance and real-world outcomes."
      >
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {engines.map(([name, e]) => (
            <div
              key={name}
              className="card p-4"
              style={{ borderLeft: `3px solid ${ENGINE_COLOR[name] ?? "var(--text-muted)"}` }}
            >
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-[14px] font-semibold">{titleCase(name)}</h3>
                <span
                  className="text-[11px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
                  style={{
                    color: ENGINE_COLOR[name],
                    background: `color-mix(in oklab, ${ENGINE_COLOR[name] ?? "var(--text-muted)"} 12%, transparent)`,
                  }}
                >
                  {e.proposed} proposed
                </span>
              </div>

              <div className="grid grid-cols-3 gap-4 text-[12px]">
                <div>
                  <div style={{ color: "var(--text-muted)" }}>Approved</div>
                  <div className="text-[18px] font-semibold tabular">{e.approved}</div>
                </div>
                <div>
                  <div style={{ color: "var(--text-muted)" }}>Dismissed</div>
                  <div className="text-[18px] font-semibold tabular">{e.dismissed}</div>
                </div>
                <div>
                  <div style={{ color: "var(--text-muted)" }}>Snoozed</div>
                  <div className="text-[18px] font-semibold tabular">{e.snoozed}</div>
                </div>
              </div>

              <div className="mt-3 flex flex-col gap-2">
                <ProgressRow
                  label="Acceptance rate"
                  value={e.acceptance_rate}
                  color="var(--series-1)"
                />
                <ProgressRow
                  label="Accuracy"
                  value={e.accuracy}
                  color="var(--status-good)"
                />
                <ProgressRow
                  label="Realisation rate"
                  value={e.realisation_rate}
                  color="var(--series-3)"
                  max={1.2}
                />
              </div>

              {e.predicted_inr > 0 && (
                <div
                  className="mt-3 text-[12px] flex items-center gap-3"
                  style={{ color: "var(--text-secondary)" }}
                >
                  <span>
                    Predicted{" "}
                    <span className="font-semibold tabular">{inr(e.predicted_inr)}</span>
                  </span>
                  <span>→</span>
                  <span>
                    Realised{" "}
                    <span
                      className="font-semibold tabular"
                      style={{
                        color:
                          e.realised_inr >= e.predicted_inr * 0.8
                            ? "var(--delta-up)"
                            : "var(--delta-down)",
                      }}
                    >
                      {inr(e.realised_inr)}
                    </span>
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      </Section>

      {/* Confidence adjustment signal */}
      {signals.length > 0 && (
        <Section
          title="Confidence adjustments"
          description="How the loop changes the next batch. An engine the manager keeps dismissing gets its confidence discounted; one whose predictions land gets a lift."
        >
          <div className="card p-4">
            <div className="flex flex-wrap gap-3">
              {signals.map(([engine, delta]) => (
                <div
                  key={engine}
                  className="flex items-center gap-2 rounded-lg px-4 py-3 text-[13px]"
                  style={{
                    background: "var(--page-plane)",
                    border: "1px solid var(--border)",
                  }}
                >
                  <span
                    className="h-2 w-2 rounded-full shrink-0"
                    style={{ background: ENGINE_COLOR[engine] ?? "var(--text-muted)" }}
                  />
                  <span className="font-medium">{titleCase(engine)}</span>
                  <span
                    className="text-[15px] font-semibold tabular"
                    style={{
                      color:
                        delta >= 0 ? "var(--delta-up)" : "var(--delta-down)",
                    }}
                  >
                    {delta >= 0 ? "+" : ""}
                    {(delta * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
            <p
              className="text-[12px] mt-3"
              style={{ color: "var(--text-muted)" }}
            >
              Applied to all pending cards before ranking. Capped at ±15% per
              cycle.
            </p>
          </div>
        </Section>
      )}

      {/* Decision history */}
      <Section
        title="Decision history"
        description="Every approve, snooze, and dismiss — with the predicted vs realised outcome when scored."
      >
        <div className="card overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th className="text-left font-medium px-3 py-2">Engine</th>
                <th className="text-left font-medium px-3 py-2">Decision</th>
                <th className="text-right font-medium px-3 py-2">Predicted</th>
                <th className="text-right font-medium px-3 py-2">Realised</th>
                <th className="text-right font-medium px-3 py-2">Confidence</th>
                <th className="text-left font-medium px-3 py-2">By</th>
                <th className="text-right font-medium px-3 py-2">When</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {decisions.slice(0, 30).map((d) => (
                <tr key={d.id} style={{ borderTop: "1px solid var(--border)" }}>
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-1.5">
                      <span
                        className="h-2 w-2 rounded-full shrink-0"
                        style={{
                          background: ENGINE_COLOR[d.engine] ?? "var(--text-muted)",
                        }}
                      />
                      {titleCase(d.engine)}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <DecisionBadge decision={d.decision} />
                  </td>
                  <td className="px-3 py-2 text-right">{inr(d.predicted_impact_inr)}</td>
                  <td
                    className="px-3 py-2 text-right"
                    style={{
                      color:
                        d.realised_impact_inr != null
                          ? d.realised_impact_inr >= d.predicted_impact_inr * 0.8
                            ? "var(--delta-up)"
                            : "var(--delta-down)"
                          : "var(--text-muted)",
                    }}
                  >
                    {d.realised_impact_inr != null ? inr(d.realised_impact_inr) : "—"}
                  </td>
                  <td className="px-3 py-2 text-right">{pct(d.confidence)}</td>
                  <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                    {d.decided_by}
                  </td>
                  <td
                    className="px-3 py-2 text-right whitespace-nowrap"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {d.at
                      ? new Date(d.at).toLocaleDateString("en-IN", {
                          day: "numeric",
                          month: "short",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {decisions.length === 0 && (
            <div
              className="px-3 py-6 text-[13px] text-center"
              style={{ color: "var(--text-muted)" }}
            >
              No decisions recorded yet. Approve or dismiss some action cards first.
            </div>
          )}
        </div>
      </Section>
    </div>
  );
}

function ProgressRow({
  label,
  value,
  color,
  max = 1,
}: {
  label: string;
  value: number | null;
  color: string;
  max?: number;
}) {
  return (
    <div className="flex items-center gap-3 text-[12px]">
      <span className="w-28 shrink-0" style={{ color: "var(--text-secondary)" }}>
        {label}
      </span>
      <span
        className="flex-1 h-1.5 rounded-full overflow-hidden"
        style={{ background: "var(--gridline)" }}
      >
        <span
          className="block h-full rounded-full transition-all duration-500"
          style={{
            width: value != null ? `${Math.min((value / max) * 100, 100)}%` : "0%",
            background: color,
          }}
        />
      </span>
      <span className="w-12 text-right tabular font-medium">
        {value != null ? pct(value) : "—"}
      </span>
    </div>
  );
}

function DecisionBadge({ decision }: { decision: string }) {
  const styles: Record<string, { color: string; bg: string }> = {
    approved: { color: "var(--status-good)", bg: "color-mix(in oklab, var(--status-good) 12%, transparent)" },
    dismissed: { color: "var(--status-critical)", bg: "color-mix(in oklab, var(--status-critical) 12%, transparent)" },
    snoozed: { color: "var(--status-warning)", bg: "color-mix(in oklab, var(--status-warning) 12%, transparent)" },
  };
  const s = styles[decision] ?? { color: "var(--text-muted)", bg: "var(--gridline)" };
  return (
    <span
      className="inline-block text-[11px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
      style={{ color: s.color, background: s.bg }}
    >
      {decision}
    </span>
  );
}
