"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export function NavLink({ href, children }: { href: string; children: ReactNode }) {
  const pathname = usePathname();
  const active = href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <Link
      href={href}
      className={`px-2.5 py-1.5 rounded-md transition-colors duration-150 ${
        active ? "nav-link-active" : ""
      }`}
      style={active ? undefined : { color: "var(--text-secondary)" }}
    >
      {children}
    </Link>
  );
}
