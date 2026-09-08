"use client";

import { useCallback, useEffect, useState } from "react";
import { ActionCardView } from "@/components/ActionCardView";
import { SentimentChart } from "@/components/charts";
import { ErrorNote, Section, Spinner } from "@/components/ui";
import { api, type ActionCard, type DeptSentiment } from "@/lib/api";
import { inr, titleCase } from "@/lib/format";

type Guests = Awaited<ReturnType<typeof api.guests>>["guests"];
type Reviews = Awaited<ReturnType<typeof api.sentiment>>["recent_reviews"];

export default function GuestsPage() {
  const [sentiment, setSentiment] = useState<Record<string, DeptSentiment>>({});
  const [lift, setLift] = useState<Record<string, number>>({});
  const [reviews, setReviews] = useState<Reviews>([]);
  const [guests, setGuests] = useState<Guests>([]);
  const [cards, setCards] = useState<ActionCard[]>([]);
  const [error, setError] = useState<string | null>(null);

  // concierge
  const [question, setQuestion] = useState("What time does the spa close today?");
  const [guestId, setGuestId] = useState<number | undefined>(undefined);
  const [answer, setAnswer] = useState<Awaited<ReturnType<typeof api.concierge>> | null>(null);
  const [asking, setAsking] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, g, c] = await Promise.all([
        api.sentiment(),
        api.guests(),
        api.actions("pending", "guest"),
      ]);
      setSentiment(s.by_department);
      setLift(s.staffing_lift_published);
      setReviews(s.recent_reviews);
      setGuests(g.guests);
      setCards(c.cards);
      setGuestId((v) => v ?? g.guests[0]?.id);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function ask() {
    setAsking(true);
    try {
      setAnswer(await api.concierge(question, guestId));
    } finally {
      setAsking(false);
    }
  }

  async function decide(id: number, d: "approve" | "snooze" | "dismiss") {
    await api.decide(id, d);
    await load();
  }

  if (error) return <ErrorNote error={error} />;
  if (!guests.length) return <Spinner label="Building guest profiles" />;

  const selected = guests.find((g) => g.id === guestId);

  return (
    <div className="flex flex-col gap-7">
      <Section
        title="Guest intelligence"
        description="Sentiment is scored per review at ingest and rolled up per department. When a department slides, the workforce engine raises that department's staffing requirement — that link is live, not illustrative."
      >
        {Object.keys(lift).length > 0 && (
          <div
            className="card p-3 text-[13px]"
            style={{ borderLeft: "3px solid var(--engine-workforce)" }}
          >
            <span className="font-semibold">Published to the workforce engine: </span>
            <span style={{ color: "var(--text-secondary)" }}>
              {Object.entries(lift)
                .map(([d, v]) => `${titleCase(d)} staffing requirement +${Math.round(v * 100)}%`)
                .join(" · ")}
            </span>
          </div>
        )}
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SentimentChart data={sentiment} />

        {/* RAG concierge */}
        <div className="card p-4 flex flex-col gap-3">
          <div>
            <h3 className="text-[15px] font-semibold">AI concierge</h3>
            <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
              Answers are retrieved from the resort SOP corpus and personalised with the guest&apos;s
              profile. It cites what it used.
            </p>
          </div>

          <label className="text-[13px] flex flex-col gap-1">
            <span style={{ color: "var(--text-secondary)" }}>Answering as</span>
            <select
              value={guestId ?? ""}
              onChange={(e) => setGuestId(Number(e.target.value))}
              className="rounded-md px-2.5 py-2 text-[13px]"
              style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            >
              {guests.slice(0, 12).map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name} — {g.tier}, {g.stay_count} stays
                </option>
              ))}
            </select>
          </label>

          {selected && (
            <div className="text-[12.5px] rounded-md px-3 py-2" style={{ background: "var(--page-plane)" }}>
              <span className="font-semibold">Guest DNA: </span>
              <span style={{ color: "var(--text-secondary)" }}>{selected.dna_summary}</span>
            </div>
          )}

          <div className="flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void ask()}
              className="flex-1 rounded-md px-3 py-2 text-[13px]"
              style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              placeholder="Ask the concierge…"
            />
            <button
              onClick={ask}
              disabled={asking}
              className="rounded-md px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"
              style={{ background: "var(--series-1)" }}
            >
              {asking ? "…" : "Ask"}
            </button>
          </div>

          {answer && (
            <div className="flex flex-col gap-2">
              <div
                className="text-[13.5px] leading-relaxed rounded-md px-3 py-2.5"
                style={{ background: "color-mix(in oklab, var(--series-1) 8%, transparent)" }}
              >
                {answer.answer}
              </div>
              <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
                {answer.source === "claude" ? "Claude, grounded in" : "Retrieved from"}:{" "}
                {answer.grounded_in.map((g) => g.title).join(" · ") || "no SOP matched"}
              </div>
            </div>
          )}
        </div>
      </div>

      {cards.length > 0 && (
        <Section title="Guest recommendations">
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {cards.map((c) => (
              <ActionCardView key={c.id} card={c} onDecide={decide} />
            ))}
          </div>
        </Section>
      )}

      <Section title="Highest-value guests" description="Guest DNA built from stay history, spend and review text.">
        <div className="card overflow-x-auto">
          <table className="w-full text-[13px] min-w-[720px]">
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th className="text-left font-medium px-3 py-2">Guest</th>
                <th className="text-left font-medium px-3 py-2">Tier</th>
                <th className="text-right font-medium px-3 py-2">Stays</th>
                <th className="text-right font-medium px-3 py-2">Lifetime value</th>
                <th className="text-left font-medium px-3 py-2">Profile</th>
              </tr>
            </thead>
            <tbody>
              {guests.slice(0, 12).map((g) => (
                <tr key={g.id} style={{ borderTop: "1px solid var(--border)" }}>
                  <td className="px-3 py-2 font-medium">{g.name}</td>
                  <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                    {titleCase(g.tier)}
                  </td>
                  <td className="px-3 py-2 text-right tabular">{g.stay_count}</td>
                  <td className="px-3 py-2 text-right tabular">{inr(g.lifetime_value)}</td>
                  <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                    {g.dna_summary}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Recent reviews" description="Scored at ingest, so the dashboard never waits on a model.">
        <div className="flex flex-col gap-2">
          {reviews.slice(0, 10).map((r) => (
            <div key={r.id} className="card p-3 flex gap-3 items-start">
              <span
                className="mt-0.5 text-[12px] font-semibold tabular px-1.5 py-0.5 rounded shrink-0"
                style={{
                  color: r.sentiment >= 0 ? "var(--delta-up)" : "var(--delta-down)",
                  background: `color-mix(in oklab, ${
                    r.sentiment >= 0 ? "var(--status-good)" : "var(--status-critical)"
                  } 12%, transparent)`,
                }}
              >
                {r.sentiment >= 0 ? "+" : ""}
                {r.sentiment.toFixed(2)}
              </span>
              <div className="min-w-0">
                <p className="text-[13px]">{r.text}</p>
                <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
                  {titleCase(r.department)} · {r.source} · {r.rating}★ ·{" "}
                  {new Date(r.posted_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                  {r.topics.length > 0 && ` · ${r.topics.map(titleCase).join(", ")}`}
                </p>
              </div>
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}
