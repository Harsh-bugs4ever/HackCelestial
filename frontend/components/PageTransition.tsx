"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { motion } from "motion/react";
import { EASE_OUT } from "@/components/motion";

/** Animate the current route without retaining an outgoing router subtree. */
export function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (

      <motion.div
        key={pathname}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.22, ease: EASE_OUT }}
      >
        {children}
      </motion.div>

  );
}
