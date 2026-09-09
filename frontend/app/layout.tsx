import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { NavLink } from "@/components/NavLink";
import { MotionProvider } from "@/components/motion";
import { PageTransition } from "@/components/PageTransition";

export const metadata: Metadata = {
  title: "Smart Resort 360",
  description:
    "AI-powered resort operations, guest experience and revenue intelligence — every insight is an approvable action.",
};

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/actions", label: "Action Bus" },
  { href: "/revenue", label: "Demand & Revenue" },
  { href: "/assets", label: "Maintenance" },
  { href: "/workforce", label: "Workforce" },
  { href: "/guests", label: "Guest Intelligence" },
  { href: "/simulator", label: "Simulator" },
  { href: "/learning", label: "Feedback Loop" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <MotionProvider>
        <div className="min-h-screen flex flex-col">
          <header
            className="sticky top-0 z-20"
            style={{
              background: "color-mix(in oklab, var(--page-plane) 74%, transparent)",
              backdropFilter: "blur(20px) saturate(1.7)",
              WebkitBackdropFilter: "blur(20px) saturate(1.7)",
              borderBottom: "1px solid var(--border)",
            }}
          >
            <div className="mx-auto max-w-[1400px] px-5 py-3 flex items-center gap-6 flex-wrap">
              <Link href="/" className="flex items-center gap-2.5 shrink-0 group">
                <span
                  aria-hidden
                  className="inline-flex h-8 w-8 items-center justify-center rounded-[10px] text-[13px] font-bold text-white transition-transform duration-200 group-hover:scale-105"
                  style={{
                    background: "linear-gradient(135deg, var(--series-1), #1a5bb5)",
                    boxShadow: "0 4px 14px var(--glow-1), inset 0 1px 0 rgba(255,255,255,0.28)",
                  }}
                >
                  360
                </span>
                <span className="text-[15px] font-semibold tracking-tight">Smart Resort 360</span>
              </Link>

              <nav className="flex items-center gap-0.5 flex-wrap text-[13px]">
                {NAV.map((n) => (
                  <NavLink key={n.href} href={n.href}>
                    {n.label}
                  </NavLink>
                ))}
              </nav>
            </div>
          </header>

          <main className="mx-auto w-full max-w-[1400px] px-5 py-6 flex-1">
            <PageTransition>{children}</PageTransition>
          </main>

          <footer
            className="mx-auto w-full max-w-[1400px] px-5 py-5 text-[12px]"
            style={{ color: "var(--text-muted)", borderTop: "1px solid var(--border)" }}
          >
            <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>Team HPSA</span>
            {" · HackCelestial 3.0 · PS 4 — one data spine, four AI engines, an action layer on top."}
          </footer>
        </div>
        </MotionProvider>
      </body>
    </html>
  );
}
