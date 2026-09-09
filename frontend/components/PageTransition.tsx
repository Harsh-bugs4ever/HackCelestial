"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { EASE_OUT } from "@/components/motion";

/**
 * Cross-fades route content. `mode="wait"` would leave the frame empty between
 * pages, which reads as a broken load on a dashboard, so the incoming page
 * fades over the outgoing one in the same grid cell.
 */
export function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <AnimatePresence initial={false}>
      <motion.div
        key={pathname}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.22, ease: EASE_OUT }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
