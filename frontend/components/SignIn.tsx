"use client";

import { useState } from "react";
import { useSession } from "@/components/SessionProvider";
import { Dialog, Field } from "@/components/Dialog";
import { titleCase } from "@/lib/format";

/**
 * Identity in the sidebar.
 *
 * Approvals now carry a verified name into the decision log, so the console has
 * to show whose name that is. When the backend runs without AUTH_USERS it
 * reports `auth_enabled:false` and this collapses to a plain "demo mode" note -
 * a local demo should not be made to sign in, but it should never be mistaken
 * for a secured deployment either.
 */
export function SignIn() {
  const { session, loading, signIn, signOut, hasToken } = useSession();
  const [open, setOpen] = useState(false);
  const [token, setTokenValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await signIn(token.trim());
      setToken("");
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function setToken(value: string) {
    setTokenValue(value);
  }

  if (loading && !session) {
    return <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>Checking access…</div>;
  }

  // No auth configured on the server: say so plainly rather than offering a
  // sign-in that would do nothing.
  if (session && !session.auth_enabled) {
    return (
      <div className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
        <span style={{ color: "var(--status-warning)" }}>●</span> Demo mode — no sign-in required.
        Decisions are recorded as unauthenticated.
      </div>
    );
  }

  return (
    <>
      <div className="flex flex-col gap-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
        {session ? (
          <>
            <span style={{ color: "var(--text-secondary)" }} className="font-medium">
              {session.name}
            </span>
            <span>{titleCase(session.role.replace(/_/g, " "))}</span>
            <button className="underline underline-offset-2 w-fit" onClick={signOut}>
              Sign out
            </button>
          </>
        ) : (
          <>
            <span style={{ color: "var(--status-warning)" }}>
              {hasToken ? "Access token rejected" : "Not signed in"}
            </span>
            <button className="underline underline-offset-2 w-fit" onClick={() => setOpen(true)}>
              Sign in
            </button>
          </>
        )}
      </div>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Sign in"
        description="Paste the access token your general manager issued. It is kept for this browser tab only."
        footer={
          <>
            <button className="btn-ghost px-3 py-2 text-[13px]" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              className="btn-approve px-4 py-2 text-[13px] disabled:opacity-50"
              disabled={busy || !token.trim()}
              onClick={submit}
            >
              {busy ? "Checking…" : "Sign in"}
            </button>
          </>
        }
      >
        <Field label="Access token">
          <input
            className="input"
            type="password"
            value={token}
            autoComplete="off"
            onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && token.trim()) void submit();
            }}
          />
        </Field>
        {error && (
          <p role="alert" className="text-[12.5px]" style={{ color: "var(--status-critical)" }}>
            {error}
          </p>
        )}
      </Dialog>
    </>
  );
}
