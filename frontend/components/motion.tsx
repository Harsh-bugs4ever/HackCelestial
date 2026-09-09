"use client";

import {
  AnimatePresence,
  MotionConfig,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
  type Transition,
  type Variants,
} from "motion/react";
import { Children, isValidElement, useEffect, type ReactNode } from "react";

/**
 * Motion layer for Smart Resort 360.
 *
 * One easing curve and one distance for the whole product, so a page assembling,
 * a card leaving the queue and a figure ticking all feel like the same system.
 * Everything here degrades to "no movement, same layout" under
 * prefers-reduced-motion — the MotionConfig in the root layout handles that
 * globally, and the hand-written pieces below check the hook directly.
 */

export const EASE_OUT: Transition["ease"] = [0.22, 1, 0.36, 1];
const RISE = 12;

export const riseVariants: Variants = {
  hidden: { opacity: 0, y: RISE },
  shown: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.42, ease: EASE_OUT },
  },
};

const containerVariants: Variants = {
  hidden: {},
  shown: { transition: { staggerChildren: 0.055, delayChildren: 0.02 } },
};

/** Global motion settings. `reducedMotion="user"` makes every `motion` element
 *  in the tree honour the OS setting without each component asking. */
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <MotionConfig reducedMotion="user" transition={{ duration: 0.42, ease: EASE_OUT }}>
      {children}
    </MotionConfig>
  );
}

/**
 * Page root. Each direct child is wrapped as a stagger item, so a page
 * assembles top-down instead of snapping in as one slab.
 *
 * This replaces the old `main > *:nth-child(n)` CSS rules, which never fired:
 * every page renders a single wrapper element, so `main` only ever had one
 * child and the nth-child delays matched nothing.
 */
export function Stagger({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <motion.div
      className={className}
      variants={containerVariants}
      initial="hidden"
      animate="shown"
    >
      {Children.map(children, (child) =>
        isValidElement(child) || typeof child === "string" ? (
          <motion.div variants={riseVariants}>{child}</motion.div>
        ) : (
          child
        ),
      )}
    </motion.div>
  );
}

/** A single block that rises in on its own, for use outside a Stagger. */
export function Rise({
  children,
  className,
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: RISE }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.42, ease: EASE_OUT, delay }}
    >
      {children}
    </motion.div>
  );
}

/**
 * A figure that counts to its new value instead of jumping.
 *
 * The dashboard repolls every 20s, so numbers change under the reader's eye;
 * a short spring makes the change legible as a change. `format` keeps currency
 * and percentage rendering with the existing helpers in lib/format.
 */
export function AnimatedNumber({
  value,
  format,
  className,
  style,
}: {
  value: number;
  format: (v: number) => string;
  className?: string;
  style?: React.CSSProperties;
}) {
  const reduced = useReducedMotion();
  const raw = useMotionValue(0);
  const spring = useSpring(raw, { stiffness: 110, damping: 22, mass: 0.7 });
  const text = useTransform(spring, (v) => format(v));

  useEffect(() => {
    raw.set(value);
  }, [raw, value]);

  if (reduced) return <span className={className} style={style}>{format(value)}</span>;
  return <motion.span className={className} style={style}>{text}</motion.span>;
}

/** A bar that grows to its share (confidence meters, sentiment, staffing). */
export function GrowBar({
  fraction,
  color,
  className,
  delay = 0,
}: {
  fraction: number;
  color: string;
  className?: string;
  delay?: number;
}) {
  const width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
  return (
    <motion.span
      className={className}
      style={{ background: color, display: "block", height: "100%", borderRadius: 999 }}
      initial={{ width: 0 }}
      animate={{ width }}
      transition={{ duration: 0.7, ease: EASE_OUT, delay }}
    />
  );
}

export { AnimatePresence, motion, useReducedMotion };
