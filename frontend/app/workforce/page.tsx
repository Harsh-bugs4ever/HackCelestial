"use client";

import { useCallback, useEffect, useState } from "react";
import { ActionCardView } from "@/components/ActionCardView";
import { ErrorNote, Section, Spinner, StatTile } from "@/components/ui";
import { api, type ActionCard } from "@/lib/api";
import { inr, pct, titleCase, shortDate, weekday } from "@/lib/format";

type RosterRow = {
  date: string;
  slot: string;
  role: string;
  required: number;
  rostered: number;
  occupancy: number;
};

type RosterData = {
  requirement: RosterRow[];
  cross_domain_inputs: string[];
  sentiment_lift: Record<string, number>;
  backlog_pressure: Record<string, number>;
};

const SLOT_ORDER: Record<string, number> = { morning: 0, evening: 1, night: 2 };
const SLOT_ICON: Record<string, string> = { morning: "☀", evening: "🌅", night: "🌙" };

export default function WorkforcePage() {
  const [roster, setRoster] = useState<RosterData | null>(null);
  const [cards, setCards] = useState<ActionCard[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, a] = await Promise.all([
        api.roster(7),
        api.actions("pending", "workforce"),
      ]);
      setRoster(r);
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
  if (!roster) return <Spinner label="Loading workforce data" />;

  const gaps = roster.requirement.filter((r) => r.rostered < r.required);
  const totalShort = gaps.reduce((a, g) => a + (g.required - g.rostered), 0);
  const totalRequired = roster.requirement.reduce((a, r) => a + r.required, 0);
  const totalRostered = roster.requirement.reduce((a, r) => a + r.rostered, 0);
  const coveragePct = totalRequired > 0 ? totalRostered / totalRequired : 1;

  const sentimentLifts = Object.entries(roster.sentiment_lift);
  const backlogDepts = Object.entries(roster.backlog_pressure).filter(
    ([, v]) => v > 1.05
  );

  // Group by date for the heatmap-style table
  const dates = [...new Set(roster.requirement.map((r) => r.date))].sort();
  const roles = [...new Set(roster.requirement.map((r) => r.role))].sort();

  return (
    <div className="flex flex-col gap-7">
      <Section
        title="Workforce Optimizer"
        description="OR-Tools CP-SAT assigns real people to real shifts under labour constraints. Demand comes from the forecast, and housekeeping staffing lifts when the guest engine reports a sentiment slide."
      >
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatTile
            label="Coverage"
            value={pct(coveragePct)}
            sub={`${totalRostered} of ${totalRequired} shifts filled`}
            accent="var(--engine-workforce)"
          />
          <StatTile
            label="Gaps"
            value={String(gaps.length)}
            sub={`${totalShort} shifts short this week`}
            accent={gaps.length > 0 ? "var(--status-serious)" : "var(--status-good)"}
          />
          <StatTile
            label="Cross-domain inputs"
            value={String(roster.cross_domain_inputs.length)}
            sub={roster.cross_domain_inputs.map(titleCase).join(" + ") || "None"}
            accent="var(--series-3)"
          />
          <StatTile
            label="Action cards"
            value={String(cards.length)}
            sub={`${inr(cards.reduce((a, c) => a + c.impact_inr, 0))} combined impact`}
            accent="var(--engine-workforce)"
          />
        </div>
      </Section>

      {/* Cross-domain signals */}
      {(sentimentLifts.length > 0 || backlogDepts.length > 0) && (
        <Section
          title="Cross-domain signals driving staffing"
          description="Other engines' outputs that changed the headcount requirement."
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {sentimentLifts.map(([dept, lift]) => (
              <div
                key={dept}
                className="card p-4 flex items-start gap-3"
                style={{ borderLeft: "3px solid var(--engine-guest)" }}
              >
                <div
                  className="text-[11px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded shrink-0"
                  style={{
                    color: "var(--engine-guest)",
                    background: "color-mix(in oklab, var(--engine-guest) 12%, transparent)",
                  }}
                >
                  Guest Engine
                </div>
                <div>
                  <div className="text-[13px] font-semibold">
                    {titleCase(dept)} staffing raised {pct(lift)}
                  </div>
                  <div className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                    Negative sentiment in recent reviews triggered an automatic headcount
                    lift for {titleCase(dept).toLowerCase()}.
                  </div>
                </div>
              </div>
            ))}
            {backlogDepts.map(([dept, mult]) => (
              <div
                key={dept}
                className="card p-4 flex items-start gap-3"
                style={{ borderLeft: "3px solid var(--status-warning)" }}
              >
                <div
                  className="text-[11px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded shrink-0"
                  style={{
                    color: "var(--status-warning)",
                    background: "color-mix(in oklab, var(--status-warning) 12%, transparent)",
                  }}
                >
                  Backlog
                </div>
                <div>
                  <div className="text-[13px] font-semibold">
                    {titleCase(dept)} queue running {pct(mult - 1)} above normal
                  </div>
                  <div className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                    Open request backlog is elevating the staffing requirement for this
                    department.
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Staffing requirement heatmap */}
      <Section
        title="Staffing requirement vs coverage"
        description="7-day view. Red cells are unfilled gaps; green cells are fully covered."
      >
        <div className="card overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th className="text-left font-medium px-3 py-2 sticky left-0" style={{ background: "var(--surface-1)" }}>
                  Role / Slot
                </th>
                {dates.map((d) => (
                  <th key={d} className="text-center font-medium px-2 py-2 whitespace-nowrap">
                    <div>{weekday(d)}</div>
                    <div className="text-[10px]">{shortDate(d)}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {roles.map((role) =>
                ["morning", "evening", "night"].map((slot) => {
                  const rowData = dates.map((d) =>
                    roster.requirement.find(
                      (r) => r.date === d && r.slot === slot && r.role === role
                    )
                  );
                  const hasAny = rowData.some((r) => r && r.required > 0);
                  if (!hasAny) return null;
                  return (
                    <tr
                      key={`${role}-${slot}`}
                      style={{ borderTop: "1px solid var(--border)" }}
                    >
                      <td
                        className="px-3 py-2 font-medium whitespace-nowrap sticky left-0"
                        style={{ background: "var(--surface-1)" }}
                      >
                        <span>{SLOT_ICON[slot]} </span>
                        {titleCase(role)}
                        <span
                          className="text-[10px] ml-1"
                          style={{ color: "var(--text-muted)" }}
                        >
                          {slot}
                        </span>
                      </td>
                      {rowData.map((r, i) => {
                        if (!r)
                          return (
                            <td key={i} className="text-center px-2 py-2" style={{ color: "var(--text-muted)" }}>
                              —
                            </td>
                          );
                        const short = r.required - r.rostered;
                        const full = short <= 0;
                        return (
                          <td key={i} className="text-center px-2 py-2">
                            <span
                              className="inline-flex items-center justify-center h-7 min-w-[2.5rem] rounded-md text-[12px] font-semibold tabular"
                              style={{
                                background: full
                                  ? "color-mix(in oklab, var(--status-good) 12%, transparent)"
                                  : "color-mix(in oklab, var(--status-critical) 14%, transparent)",
                                color: full
                                  ? "var(--status-good)"
                                  : "var(--status-critical)",
                              }}
                            >
                              {r.rostered}/{r.required}
                            </span>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </Section>

      {/* Action cards */}
      <Section
        title="Roster & inventory recommendations"
        description="Staffing changes and purchase orders driven by the forecast and cross-domain signals."
      >
        {cards.length === 0 ? (
          <div
            className="card p-6 text-[13px]"
            style={{ color: "var(--text-secondary)" }}
          >
            No open workforce recommendations. Run the engines to generate the next
            batch.
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
