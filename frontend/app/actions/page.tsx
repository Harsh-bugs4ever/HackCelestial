"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ActionCardView } from "@/components/ActionCardView";
import { ErrorNote, Section, Spinner } from "@/components/ui";
import { api, type ActionCard } from "@/lib/api";
import { ENGINE_LABEL, inr } from "@/lib/format";

const FILTERS = [
  { key: "all", label: "All engines" },
  { key: "demand", label: "Demand & Revenue" },
  { key: "maintenance", label: "Maintenance" },
  { key: "workforce", label: "Workforce" },
  { key: "guest", label: "Guest" },
] as const;

export default function ActionsPage() {
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<ActionCard[]>([]);
  const [history, setHistory] = useState<ActionCard[]>([]);
  const [engine, setEngine] = useState<string>("all");
  const [tab, setTab] = useState<"queue" | "history">("queue");
  const [error, setError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, h] = await Promise.all([api.actions("pending"), api.history()]);
      setPending(p.cards);
      setHistory(h.cards);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = useMemo(
    () => (engine === "all" ? pending : pending.filter((c) => c.engine === engine)),
    [pending, engine],
  );

  async function decide(id: number, decision: "approve" | "snooze" | "dismiss") {
    await api.decide(id, decision);
    await load();
  }

  async function runAll() {
    setBusy(true);
    setRunError(null);
    try {
      await api.runEngines();
      await load();
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "Could not refresh insights. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Spinner label="Loading action queue" />;
  if (error) return <ErrorNote error={error} />;

  const totalImpact = shown.reduce((a, c) => a + c.impact_inr, 0);

  return (
    <div className="flex flex-col gap-6">
      {runError && <p role="alert" className="card p-4 text-sm" style={{ color: "var(--status-critical)" }}>{runError}</p>}
      <Section
        title="Action bus"
        description="Every engine output arrives here as an approvable card — a recommendation, its confidence, its rupee impact, and the drivers behind it. Nothing executes without approval."
        action={
          <button
            onClick={runAll}
            disabled={busy}
            className="rounded-md px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
            style={{ background: "var(--series-1)" }}
          >
            {busy ? "Running…" : "Run all engines"}
          </button>
        }
      >
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex gap-1 rounded-lg p-1" style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}>
            {(["queue", "history"] as const).map((t) => (
              <button
                key={t}
                aria-pressed={tab === t}
                onClick={() => setTab(t)}
                className="px-3 py-1.5 rounded-md text-[13px] font-medium"
                style={
                  tab === t
                    ? { background: "var(--series-1)", color: "#fff" }
                    : { color: "var(--text-secondary)" }
                }
              >
                {t === "queue" ? `Queue (${pending.length})` : `Decided (${history.length})`}
              </button>
            ))}
          </div>

          {tab === "queue" && (
            <div className="flex gap-1 flex-wrap">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  aria-pressed={engine === f.key}
                  onClick={() => setEngine(f.key)}
                  className="px-2.5 py-1.5 rounded-md text-[13px]"
                  style={
                    engine === f.key
                      ? { background: "var(--page-plane)", color: "var(--text-primary)", border: "1px solid var(--border)" }
                      : { color: "var(--text-secondary)", border: "1px solid transparent" }
                  }
                >
                  {f.label}
                </button>
              ))}
            </div>
          )}

          {tab === "queue" && (
            <span className="ml-auto text-[13px]" style={{ color: "var(--text-secondary)" }}>
              {shown.length} cards · {inr(totalImpact)} combined impact
            </span>
          )}
        </div>
      </Section>

      {tab === "queue" ? (
        shown.length === 0 ? (
          <div className="card p-8 text-center text-[13px]" style={{ color: "var(--text-secondary)" }}>
            Nothing waiting. Run the engines to generate the next batch of recommendations.
          </div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {shown.map((c) => (
              <ActionCardView key={c.id} card={c} onDecide={decide} />
            ))}
          </div>
        )
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-[13px] min-w-[720px]">
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th className="text-left font-medium px-3 py-2">Recommendation</th>
                <th className="text-left font-medium px-3 py-2">Engine</th>
                <th className="text-right font-medium px-3 py-2">Impact</th>
                <th className="text-left font-medium px-3 py-2">Decision</th>
                <th className="text-left font-medium px-3 py-2">Result</th>
              </tr>
            </thead>
            <tbody>
              {history.map((c) => (
                <tr key={c.id} style={{ borderTop: "1px solid var(--border)" }}>
                  <td className="px-3 py-2">{c.title}</td>
                  <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                    {ENGINE_LABEL[c.engine]}
                  </td>
                  <td className="px-3 py-2 text-right tabular">{inr(c.impact_inr)}</td>
                  <td className="px-3 py-2">
                    <span
                      className="text-[12px] font-medium"
                      style={{
                        color:
                          c.status === "executed" || c.status === "approved"
                            ? "var(--status-good)"
                            : "var(--text-muted)",
                      }}
                    >
                      {c.status}
                    </span>
                  </td>
                  <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                    {typeof c.execution_result?.message === "string"
                      ? (c.execution_result.message as string)
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
