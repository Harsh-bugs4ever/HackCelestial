"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

const navigation = [
  { href: "/", label: "Overview", icon: "▦", group: "WORKSPACE" },
  { href: "/actions", label: "Action queue", icon: "↗" },
  { href: "/revenue", label: "Demand & revenue", icon: "▥", group: "OPERATIONS" },
  { href: "/assets", label: "Maintenance", icon: "◇" },
  { href: "/workforce", label: "Workforce", icon: "♧" },
  { href: "/guests", label: "Guest intelligence", icon: "♡" },
  { href: "/simulator", label: "Simulator", icon: "⌘", group: "INTELLIGENCE" },
  { href: "/learning", label: "Feedback loop", icon: "↻" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const current = navigation.find(n => n.href === pathname);
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar">
      <Link href="/" className="brand" onClick={() => setOpen(false)}><span className="brand-mark">360</span><span>Smart Resort<span className="brand-caption">PROPERTY INTELLIGENCE</span></span></Link>
      <button className="mobile-menu btn-ghost" aria-expanded={open} aria-controls="main-navigation" onClick={() => setOpen(!open)}>{open ? "Close menu ×" : "Menu ☰"}</button>
      <nav id="main-navigation" aria-label="Main navigation" className={`sidebar-nav ${open ? "is-open" : ""}`}>
        {navigation.map(n => <div key={n.href}>{n.group && <div className="nav-group">{n.group}</div>}<Link href={n.href} onClick={() => setOpen(false)} aria-current={pathname === n.href ? "page" : undefined} className="sidebar-link"><span aria-hidden="true" className="nav-icon">{n.icon}</span>{n.label}{pathname === n.href && <span className="nav-active-dot" />}</Link></div>)}
      </nav>
      <div className="sidebar-bottom"><div className="workspace-note"><span className="eyebrow">Your decision. More clarity.</span><p>Turn property insights into thoughtful action.</p><Link href="/simulator">Explore the simulator →</Link></div><div className="workspace-profile"><span className="profile-avatar">SR</span><div>Resort workspace<small>Operations & intelligence</small></div></div></div>
    </aside>
    <div className="workspace-main">
      <header className="workspace-header"><div><span className="breadcrumb-parent">Workspace</span><span className="breadcrumb-slash">/</span><strong>{current?.label ?? "Resort operations"}</strong></div><span className="workspace-badge">Smart Resort 360 <span>· Operations console</span></span></header>
      <main id="main-content" tabIndex={-1} className="workspace-content">{children}</main>
      <footer className="workspace-footer"><span>Smart Resort 360</span><span>Built by Team HPSA · HackCelestial 3.0</span></footer>
    </div>
  </div>;
}
