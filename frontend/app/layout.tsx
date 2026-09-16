import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { MotionProvider } from "@/components/motion";
import { PageTransition } from "@/components/PageTransition";
import { SessionProvider } from "@/components/SessionProvider";

export const metadata: Metadata = {
  title: "Smart Resort 360",
  description: "Resort operations, guest experience and revenue intelligence in one workspace.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body><SessionProvider><MotionProvider><AppShell><PageTransition>{children}</PageTransition></AppShell></MotionProvider></SessionProvider></body></html>;
}
