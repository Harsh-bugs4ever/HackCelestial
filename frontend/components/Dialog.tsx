"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { EASE_OUT } from "@/components/motion";

/**
 * A small modal used by the decision surface.
 *
 * Managers act on these cards at speed, often on a phone on the floor, so the
 * dialog has to behave like one: Escape closes it, a backdrop click closes it,
 * focus moves into it on open and returns to the trigger on close, and Tab is
 * trapped so a keyboard user cannot wander into the page behind.
 */
export function Dialog({
  open,
  title,
  description,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreTo.current = document.activeElement as HTMLElement | null;
    // Focus the first control rather than the panel, so the manager can type
    // straight away.
    const focusable = panel.current?.querySelector<HTMLElement>(
      "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
    );
    (focusable ?? panel.current)?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const items = Array.from(
        panel.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey, true);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = previousOverflow;
      restoreTo.current?.focus?.();
    };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
          style={{ background: "color-mix(in oklab, #000 45%, transparent)" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <motion.div
            ref={panel}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            tabIndex={-1}
            // Full-width sheet on a phone, centred card above it.
            className="card w-full sm:max-w-lg max-h-[90vh] overflow-y-auto p-4 sm:p-5 flex flex-col gap-3 rounded-b-none sm:rounded-b-[inherit]"
            initial={{ opacity: 0, y: 24, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.99 }}
            transition={{ duration: 0.24, ease: EASE_OUT }}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-[16px] font-semibold tracking-tight">{title}</h2>
                {description && (
                  <p className="text-[12.5px] mt-1" style={{ color: "var(--text-secondary)" }}>
                    {description}
                  </p>
                )}
              </div>
              <button
                onClick={onClose}
                className="btn-ghost px-2 py-1 text-[13px] shrink-0"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            {children}
            {footer && <div className="flex gap-2 justify-end flex-wrap pt-1">{footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** A labelled control with optional help text. Keeps the modals consistent. */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-[12.5px]">
      <span className="font-medium">{label}</span>
      {children}
      {hint && <span style={{ color: "var(--text-muted)" }}>{hint}</span>}
    </label>
  );
}
