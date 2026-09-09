"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Polls an endpoint and keeps the last good payload.
 *
 * The dashboard refreshes every 20s. Before this, a single failed poll set
 * `data` back to null and the whole screen fell back to the error card — the
 * operator lost a working view because one request timed out. Now a failure
 * only raises `error` alongside the data that is still on screen, and the UI
 * marks itself stale (see `StaleBanner`) instead of emptying.
 *
 * `error` with no `data` is a genuine cold failure and still gets the full
 * error card.
 */
export function useLiveData<T>(
  fetcher: () => Promise<T>,
  { intervalMs = 0 }: { intervalMs?: number } = {},
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Keeps the polling timer from re-registering on every render when the
  // caller passes an inline fetcher.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(async () => {
    try {
      setData(await fetcherRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    if (!intervalMs) return;
    const id = setInterval(reload, intervalMs);
    return () => clearInterval(id);
  }, [reload, intervalMs]);

  return {
    data,
    error,
    loading,
    /** Data on screen is real but the newest refresh failed. */
    stale: data !== null && error !== null,
    reload,
  };
}
