"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, getToken, setToken, type Session } from "@/lib/api";

/**
 * Who is using the console, and what they are allowed to do.
 *
 * The point is not to hide things for tidiness - it is that the approval
 * buttons now carry a real authority check on the server, and offering a
 * manager a button that will 403 is worse than not offering it. `can()` lets
 * each surface ask before it renders.
 *
 * When the backend has no AUTH_USERS configured it reports `auth_enabled:false`
 * and grants everything, so local demos need no sign-in at all.
 */
type SessionState = {
  session: Session | null;
  loading: boolean;
  error: string | null;
  can: (permission: string) => boolean;
  canDecide: (engine: string) => boolean;
  signIn: (token: string) => Promise<void>;
  signOut: () => void;
  hasToken: boolean;
};

const SessionContext = createContext<SessionState | null>(null);

const ENGINE_PERMISSION: Record<string, string> = {
  demand: "decide:demand",
  maintenance: "decide:maintenance",
  workforce: "decide:workforce",
  guest: "decide:guest",
};

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasToken, setHasToken] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSession(await api.session());
      setError(null);
    } catch (e) {
      setSession(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setHasToken(Boolean(getToken()));
    void load();
  }, [load]);

  const signIn = useCallback(
    async (token: string) => {
      setToken(token);
      setHasToken(Boolean(token));
      await load();
    },
    [load],
  );

  const signOut = useCallback(() => {
    setToken("");
    setHasToken(false);
    void load();
  }, [load]);

  const value = useMemo<SessionState>(() => {
    const permissions = new Set(session?.permissions ?? []);
    return {
      session,
      loading,
      error,
      hasToken,
      signIn,
      signOut,
      // Before the session resolves, assume permission: the server is the real
      // gate, and flashing every control disabled on each page load is worse
      // than briefly showing one that turns out to be unavailable.
      can: (permission) => (session ? permissions.has(permission) : true),
      canDecide: (engine) =>
        session ? permissions.has(ENGINE_PERMISSION[engine] ?? "") : true,
    };
  }, [session, loading, error, hasToken, signIn, signOut]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error("useSession must be used inside <SessionProvider>");
  }
  return context;
}
