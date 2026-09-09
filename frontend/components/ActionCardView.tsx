"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { EASE_OUT, GrowBar } from "@/components/motion";
import type { ActionCard } from "@/lib/api";
import {
  ENGINE_COLOR,
  ENGINE_LABEL,
  URGENCY_COLOR,
  inr,
  inrExact,
  pct,
  timeAgo,
  titleCase,
} from "@/lib/format";

type Props = {
  card: ActionCard;
  onDecide: (id: number, decision: "approve" | "snooze" | "dismiss") => Promise<void>;
  compact?: boolean;
};

/**
 * The differentiator, rendered: a recommendation, a confidence score, a rupee
 * figure, the drivers behind it, and three buttons. Never a chart alone.
 */
export function ActionCardView({ card, onDecide, compact = false }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [showWhy, setShowWhy] = useState(!compact);
  const decided = card.status !== "pending";

  const impactPositive = card.impact_kind !== "cost";
  const impactLabel =
    card.impact_kind === "cost_avoided"
      ? "cost avoided"
      : card.impact_kind === "cost"
        ? "net cost"
        : "revenue impact";

  async function act(decision: "approve" | "snooze" | "dismiss") {
    setBusy(decision);
    try {
      await onDecide(card.id, decision);
    } finally {
      setBusy(null);
    }
  }

  return (
    <motion.article
      layout
      className="card p-4 flex flex-col gap-3"
      style={{ borderLeft: `3px solid ${ENGINE_COLOR[card.engine]}` }}
      aria-label={card.title}
      aria-busy={busy !== null}
      initial={{ opacity: 0, y: 14, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      /* A decided card slides out of the queue rather than blinking away, so
         the operator can see which one they just acted on. */
      exit={{ opacity: 0, x: 28, scale: 0.97, transition: { duration: 0.24, ease: EASE_OUT } }}
      transition={{ duration: 0.42, ease: EASE_OUT }}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="text-[11px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
              style={{
                color: ENGINE_COLOR[card.engine],
                background: `color-mix(in oklab, ${ENGINE_COLOR[card.engine]} 12%, transparent)`,
              }}
            >
              {ENGINE_LABEL[card.engine] ?? card.engine}
            </span>

            {card.urgency !== "normal" && card.urgency !== "low" && (
              <span
                className="text-[11px] font-semibold uppercase tracking-wide inline-flex items-center gap-1"
                style={{ color: URGENCY_COLOR[card.urgency] }}
              >
                {/* icon + label: status is never carried by color alone */}
                <span aria-hidden>{card.urgency === "critical" ? "▲" : "●"}</span>
                {card.urgency}
              </span>
            )}

            {card.cross_domain.length > 0 && (
              <span
                className="text-[11px] px-1.5 py-0.5 rounded"
                style={{
                  color: "var(--text-secondary)",
                  border: "1px solid var(--border)",
                }}
                title="This recommendation used another engine's output"
              >
                ↔ uses {card.cross_domain.map((e) => ENGINE_LABEL[e] ?? e).join(" + ")}
              </span>
            )}
          </div>

          <h3 className="mt-2 text-[15px] font-semibold leading-snug" style={{ color: "var(--text-primary)" }}>
            {card.title}
          </h3>
        </div>

        <div className="text-right shrink-0">
          <div
            className="text-xl font-semibold tabular"
            style={{ color: impactPositive ? "var(--delta-up)" : "var(--delta-down)" }}
            title={inrExact(card.impact_inr)}
          >
            {impactPositive ? "+" : ""}
            {inr(card.impact_inr)}
          </div>
          <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {impactLabel}
          </div>
        </div>
      </header>

      <p className="text-[13px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
        {card.detail}
      </p>

      <div
        className="text-[13px] leading-relaxed rounded-md px-3 py-2"
        style={{ background: "var(--page-plane)", color: "var(--text-primary)" }}
      >
        <span className="font-semibold">Recommended: </span>
        {card.recommendation}
      </div>

      <div className="flex items-center gap-3 text-[12px]" style={{ color: "var(--text-muted)" }}>
        <ConfidenceMeter value={card.confidence} />
        {card.impact_note && <span className="truncate">{card.impact_note}</span>}
        <span className="ml-auto shrink-0">{timeAgo(card.created_at)}</span>
      </div>

      {card.why.length > 0 && (
        <div>
          <button
            className="text-[12px] font-medium underline underline-offset-2"
            style={{ color: "var(--text-secondary)" }}
            onClick={() => setShowWhy((v) => !v)}
            aria-expanded={showWhy}
          >
            {showWhy ? "Hide" : "Why this recommendation?"}
          </button>

          <AnimatePresence initial={false}>
          {showWhy && (
            <motion.ul
              className="mt-2 flex flex-col gap-1.5 overflow-hidden"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.3, ease: EASE_OUT }}
            >
              {card.why.map((d, i) => (
                <li key={i} className="flex items-start gap-2 text-[12.5px]">
                  <span
                    aria-hidden
                    className="mt-1.5 h-1.5 w-1.5 rounded-full shrink-0"
                    style={{ background: ENGINE_COLOR[card.engine] }}
                  />
                  <span>
                    <span className="font-medium" style={{ color: "var(--text-primary)" }}>
                      {d.label}
                    </span>
                    <span style={{ color: "var(--text-secondary)" }}> — {d.detail}</span>
                  </span>
                </li>
              ))}
            </motion.ul>
          )}
          </AnimatePresence>
        </div>
      )}

      {decided ? (
        <motion.div
          layout
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-[12.5px] rounded-md px-3 py-2"
          style={{
            background: "color-mix(in oklab, var(--status-good) 10%, transparent)",
            color: "var(--text-primary)",
          }}
        >
          <span className="font-semibold">
            {titleCase(card.status)}
            {card.decided_by ? ` by ${card.decided_by}` : ""}
          </span>
          {typeof card.execution_result?.message === "string" && (
            <div style={{ color: "var(--text-secondary)" }}>
              {card.execution_result.message as string}
            </div>
          )}
        </motion.div>
      ) : (
        <motion.div layout className="flex items-center gap-2 pt-1">
          <button
            className="btn-approve px-5 py-2 text-[13px] disabled:opacity-50"
            disabled={busy !== null}
            onClick={() => act("approve")}
          >
            {busy === "approve" ? "Executing…" : "Approve"}
          </button>
          <button
            className="btn-ghost px-3 py-2 text-[13px] font-medium disabled:opacity-50"
            disabled={busy !== null}
            onClick={() => act("snooze")}
          >
            {busy === "snooze" ? "…" : "Snooze"}
          </button>
          <button
            className="btn-ghost px-3 py-2 text-[13px] font-medium disabled:opacity-50"
            disabled={busy !== null}
            onClick={() => act("dismiss")}
          >
            {busy === "dismiss" ? "…" : "Dismiss"}
          </button>
        </motion.div>
      )}
    </motion.article>
  );
}

function ConfidenceMeter({ value }: { value: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 shrink-0" title={`Model confidence ${pct(value)}`}>
      <span
        className="inline-block h-1.5 w-14 rounded-full overflow-hidden"
        style={{ background: "var(--gridline)" }}
      >
        <GrowBar
          fraction={value}
          color={
            value >= 0.75
              ? "var(--status-good)"
              : value >= 0.55
                ? "var(--status-warning)"
                : "var(--status-serious)"
          }
        />
      </span>
      <span className="tabular">{pct(value)} confidence</span>
    </span>
  );
}
