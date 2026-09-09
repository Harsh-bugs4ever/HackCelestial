"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { motion } from "motion/react";
import { EASE_OUT } from "@/components/motion";

/**
 * The active pill is a single shared element (`layoutId`), so moving between
 * sections slides one highlight across the nav rather than blinking a new box
 * into place. Under reduced motion the MotionConfig in the layout drops the
 * travel and the pill simply appears.
 */
export function NavLink({ href, children }: { href: string; children: ReactNode }) {
  const pathname = usePathname();
  const active = href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className="relative px-2.5 py-1.5 rounded-md transition-colors duration-150"
      style={{ color: active ? "var(--text-primary)" : "var(--text-secondary)", fontWeight: active ? 600 : 400 }}
    >
      {active && (
        <motion.span
          layoutId="nav-active-pill"
          className="absolute inset-0 rounded-md"
          style={{
            background: "color-mix(in oklab, var(--series-1) 13%, transparent)",
            boxShadow: "inset 0 0 0 1px color-mix(in oklab, var(--series-1) 24%, transparent)",
          }}
          transition={{ type: "spring", stiffness: 380, damping: 32 }}
        />
      )}
      <span className="relative">{children}</span>
    </Link>
  );
}
