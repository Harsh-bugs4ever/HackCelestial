"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { EASE_OUT, GrowBar } from "@/components/motion";
import { Dialog, Field } from "@/components/Dialog";
import { useSession } from "@/components/SessionProvider";
import { api, type ActionCard, type DismissReason } from "@/lib/api";
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

type DecideOptions = {
  edits?: Record<string, unknown>;
  reason?: string;
  reason_note?: string;
};

type Props = {
  card: ActionCard;
  onDecide: (
    id: number,
    decision: "approve" | "snooze" | "dismiss",
    options?: DecideOptions,
  ) => Promise<void>;
  /** Undo an executed action. Omit to hide the control entirely. */
  onUndo?: (id: number) => Promise<void>;
  dismissReasons?: DismissReason[];
  compact?: boolean;
};

/**
 * The dismissal reasons, fetched once per page load and shared by every card.
 *
 * A page can pass its own list, but it does not have to: a card that cannot
 * offer a reason cannot be dismissed at all now that the server requires one,
 * and that failure would only show up when a manager tried to use it.
 */
let reasonCache: DismissReason[] | null = null;
let reasonRequest: Promise<DismissReason[]> | null = null;

function useDismissReasons(provided: DismissReason[]): DismissReason[] {
  const [fetched, setFetched] = useState<DismissReason[]>(reasonCache ?? []);
  useEffect(() => {
    if (provided.length > 0 || reasonCache) return;
    reasonRequest ??= api
      .dismissReasons()
      .then((r) => {
        reasonCache = r.reasons;
        return r.reasons;
      })
      .catch(() => {
        reasonRequest = null;      // let a later card retry after a blip
        return [];
      });
    let live = true;
    void reasonRequest.then((r) => {
      if (live) setFetched(r);
    });
    return () => {
      live = false;
    };
  }, [provided.length]);
  return provided.length > 0 ? provided : fetched;
}

/**
 * Labels for the payload fields a manager may override, per card kind.
 * `editable_fields` comes from the server, so this only supplies presentation -
 * a field the server does not allow can never be shown, and a new editable
 * field simply renders with its raw key until it is named here.
 */
const EDIT_LABEL: Record<string, { label: string; hint: string; numeric?: boolean }> = {
  proposed_rate: {
    label: "Rate to apply (INR)",
    hint: "Approving at a different rate still counts as agreeing with the direction.",
    numeric: true,
  },
  quantity: { label: "Quantity to order", hint: "", numeric: true },
  total_cost: { label: "Total cost (INR)", hint: "", numeric: true },
  needed_by: { label: "Needed by", hint: "YYYY-MM-DD" },
  scheduled_for: { label: "Scheduled for", hint: "YYYY-MM-DDTHH:MM" },
  short_by: { label: "Shifts to add", hint: "", numeric: true },
  offer: { label: "Offer", hint: "" },
  offer_value: { label: "Offer value (INR)", hint: "", numeric: true },
};

/**
 * The differentiator, rendered: a recommendation, a confidence score, a rupee
 * figure, the drivers behind it, and three buttons. Never a chart alone.
 */
export function ActionCardView({
  card,
  onDecide,
  onUndo,
  dismissReasons = [],
  compact = false,
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showWhy, setShowWhy] = useState(!compact);
  const [dismissOpen, setDismissOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [reasonNote, setReasonNote] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const { canDecide, can, session } = useSession();
  const reasons = useDismissReasons(dismissReasons);

  const decided = card.status !== "pending";
  const mayDecide = canDecide(card.engine);
  const editable = card.editable_fields ?? [];

  // Seed the counter-offer form from the recommendation, so the manager adjusts
  // a real number instead of typing one from scratch.
  useEffect(() => {
    if (!editOpen) return;
    const seeded: Record<string, string> = {};
    for (const key of editable) {
      const value = (card.payload as Record<string, unknown>)?.[key];
      seeded[key] = value === undefined || value === null ? "" : String(value);
    }
    setDraft(seeded);
  }, [editOpen, card.payload, editable]);

  const impactPositive = card.impact_kind !== "cost" && card.impact_inr > 0;
  const impactLabel =
    card.impact_kind === "cost_avoided"
      ? "cost avoided"
      : card.impact_kind === "cost"
        ? "net cost"
        : "revenue impact";

  async function act(
    decision: "approve" | "snooze" | "dismiss",
    options?: DecideOptions,
  ) {
    setBusy(decision);
    setError(null);
    try {
      await onDecide(card.id, decision, options);
      setDismissOpen(false);
      setEditOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save your decision. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  async function undo() {
    if (!onUndo) return;
    setBusy("undo");
    setError(null);
    try {
      await onUndo(card.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not undo this action.");
    } finally {
      setBusy(null);
    }
  }

  /** Only send fields the manager actually changed. */
  function changedEdits(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [key, raw] of Object.entries(draft)) {
      const original = (card.payload as Record<string, unknown>)?.[key];
      if (raw === "" || raw === String(original ?? "")) continue;
      const numeric = EDIT_LABEL[key]?.numeric;
      const parsed = numeric ? Number(raw) : raw;
      if (numeric && !Number.isFinite(parsed as number)) continue;
      out[key] = parsed;
    }
    return out;
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
      {error && <p role="alert" className="text-[13px]" style={{ color: "var(--status-critical)" }}>{error}</p>}
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
            {card.decided_by_role ? ` (${titleCase(card.decided_by_role.replace(/_/g, " "))})` : ""}
          </span>
          {card.shadow && (
            <div className="mt-0.5" style={{ color: "var(--status-warning)" }}>
              Shadow mode — recorded and previewed, nothing was changed.
            </div>
          )}
          {card.was_edited && (
            <div style={{ color: "var(--text-secondary)" }}>
              Applied with your changes:{" "}
              {Object.entries(card.edited_payload)
                .map(([k, v]) => `${EDIT_LABEL[k]?.label ?? k} → ${String(v)}`)
                .join(", ")}
            </div>
          )}
          {typeof card.execution_result?.message === "string" && (
            <div style={{ color: "var(--text-secondary)" }}>
              {card.execution_result.message as string}
            </div>
          )}

          {/* Undo is what makes the first approval possible: without a way back,
              a cautious manager never says yes to anything. */}
          {onUndo && card.status === "executed" && can("action:undo") && (
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              {card.can_revert ? (
                <>
                  <button
                    className="btn-ghost px-3 py-1.5 text-[12px] font-medium disabled:opacity-50"
                    disabled={busy !== null}
                    onClick={undo}
                  >
                    {busy === "undo" ? "Undoing…" : "Undo this action"}
                  </button>
                  <span style={{ color: "var(--text-muted)" }}>
                    Reversible for {session?.undo_window_minutes ?? 30} minutes after execution.
                  </span>
                </>
              ) : (
                <span style={{ color: "var(--text-muted)" }}>
                  Cannot be undone — {card.revert_blocked_reason}.
                </span>
              )}
            </div>
          )}
          {card.status === "reverted" && (
            <div style={{ color: "var(--text-muted)" }}>
              Reverted{card.reverted_by ? ` by ${card.reverted_by}` : ""}. Everyone notified
              earlier has been told it no longer applies.
            </div>
          )}
        </motion.div>
      ) : (
        <motion.div layout className="flex flex-col gap-2 pt-1">
          {session?.shadow_mode && (
            <p className="text-[12px]" style={{ color: "var(--status-warning)" }}>
              Shadow mode is on — approving records the decision and previews the
              result without changing anything.
            </p>
          )}
          {!mayDecide ? (
            <p className="text-[12.5px]" style={{ color: "var(--text-muted)" }}>
              Your role ({titleCase((session?.role ?? "").replace(/_/g, " ")) || "viewer"}) cannot
              approve {ENGINE_LABEL[card.engine] ?? card.engine} recommendations.
            </p>
          ) : (
            /* Wraps to two rows on a phone rather than overflowing. */
            <div className="flex items-center gap-2 flex-wrap">
              <button
                className="btn-approve px-5 py-2 text-[13px] disabled:opacity-50"
                disabled={busy !== null}
                onClick={() => act("approve")}
              >
                {busy === "approve" ? "Executing…" : "Approve"}
              </button>
              {editable.length > 0 && (
                <button
                  className="btn-ghost px-3 py-2 text-[13px] font-medium disabled:opacity-50"
                  disabled={busy !== null}
                  onClick={() => setEditOpen(true)}
                >
                  Adjust &amp; approve
                </button>
              )}
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
                onClick={() => setDismissOpen(true)}
              >
                Dismiss
              </button>
            </div>
          )}
        </motion.div>
      )}

      {/* Counter-offer. "Right direction, half the number" used to be
          expressible only as a rejection, which taught the engine it was
          simply wrong. */}
      <Dialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title="Adjust and approve"
        description="Change what gets applied. Your version executes, and the difference is recorded as feedback for this engine."
        footer={
          <>
            <button className="btn-ghost px-3 py-2 text-[13px]" onClick={() => setEditOpen(false)}>
              Cancel
            </button>
            <button
              className="btn-approve px-4 py-2 text-[13px] disabled:opacity-50"
              disabled={busy !== null}
              onClick={() => act("approve", { edits: changedEdits() })}
            >
              {busy === "approve" ? "Executing…" : "Approve with changes"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          {editable.map((key) => {
            const meta = EDIT_LABEL[key] ?? { label: key, hint: "" };
            const original = (card.payload as Record<string, unknown>)?.[key];
            return (
              <Field
                key={key}
                label={meta.label}
                hint={meta.hint || `Recommended: ${String(original ?? "—")}`}
              >
                <input
                  className="input"
                  inputMode={meta.numeric ? "decimal" : undefined}
                  value={draft[key] ?? ""}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                />
              </Field>
            );
          })}
          {editable.length === 0 && (
            <p className="text-[12.5px]" style={{ color: "var(--text-muted)" }}>
              This recommendation has no adjustable fields.
            </p>
          )}
        </div>
      </Dialog>

      {/* Dismiss reason. The most valuable signal in the product, and it used to
          be thrown away. */}
      <Dialog
        open={dismissOpen}
        onClose={() => setDismissOpen(false)}
        title="Why are you dismissing this?"
        description="This is what the system learns from. 'Already handled' tells it the timing was wrong; 'the data is wrong' tells it the model was."
        footer={
          <>
            <button className="btn-ghost px-3 py-2 text-[13px]" onClick={() => setDismissOpen(false)}>
              Cancel
            </button>
            <button
              className="btn-ghost px-4 py-2 text-[13px] font-medium disabled:opacity-50"
              style={{ color: "var(--status-critical)" }}
              disabled={busy !== null || !reason}
              onClick={() => act("dismiss", { reason, reason_note: reasonNote })}
            >
              {busy === "dismiss" ? "Dismissing…" : "Dismiss recommendation"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-2">
          {reasons.map((r) => (
            <label
              key={r.id}
              className="flex items-start gap-2 text-[13px] rounded-md px-2.5 py-2 cursor-pointer"
              style={{
                background: reason === r.id ? "var(--page-plane)" : "transparent",
                border: `1px solid ${reason === r.id ? "var(--border)" : "transparent"}`,
              }}
            >
              <input
                type="radio"
                name={`dismiss-reason-${card.id}`}
                className="mt-0.5"
                checked={reason === r.id}
                onChange={() => setReason(r.id)}
              />
              <span>{r.label}</span>
            </label>
          ))}
          <Field label="Anything to add? (optional)">
            <textarea
              className="input"
              rows={2}
              value={reasonNote}
              onChange={(e) => setReasonNote(e.target.value)}
              placeholder="e.g. the wedding party cancelled this morning"
            />
          </Field>
        </div>
      </Dialog>
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
