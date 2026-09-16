"use client";

import { useState } from "react";
import { api, type NotificationRow } from "@/lib/api";
import { useLiveData } from "@/lib/useLiveData";
import { useSession } from "@/components/SessionProvider";
import { ErrorNote, Section, Spinner, StaleBanner, StatTile } from "@/components/ui";
import { timeAgo, titleCase } from "@/lib/format";

const STATUS_COLOR: Record<string, string> = {
  sent: "var(--status-good)",
  queued: "var(--status-warning)",
  failed: "var(--status-critical)",
};

const CHANNEL_ICON: Record<string, string> = {
  whatsapp: "◍",
  sms: "✉",
  email: "✉",
  webhook: "⇥",
  console: "▤",
};

/**
 * The outbox.
 *
 * Approving a card used to end at an INSERT - the rostered housekeeper, the
 * technician and the supplier all learned nothing. This is the evidence that it
 * no longer does, and the answer to "nobody told me", which is a real
 * operational dispute rather than a hypothetical one.
 */
export default function NotificationsPage() {
  const [filter, setFilter] = useState<"all" | "queued" | "sent" | "failed">("all");
  const { data, error, loading, stale, reload } = useLiveData(
    () => api.notifications(filter),
    { intervalMs: 15000 },
  );
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const { can } = useSession();

  async function retry() {
    setRetrying(true);
    setRetryError(null);
    try {
      await api.retryNotifications();
      await reload();
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : String(e));
    } finally {
      setRetrying(false);
    }
  }

  if (loading && !data) return <Spinner label="Loading the outbox" />;
  if (error && !data) return <ErrorNote error={error} />;
  if (!data) return null;

  return (
    <div className="flex flex-col gap-6">
      {stale && error && <StaleBanner error={error} onRetry={reload} />}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Delivered" value={data.counts.sent} sub="reached a person" accent="var(--status-good)" />
        <StatTile label="Queued" value={data.counts.queued} sub="awaiting the next drain" accent="var(--status-warning)" />
        <StatTile
          label="Failed"
          value={data.counts.failed}
          sub={data.counts.failed ? "instructions nobody received" : "none"}
          accent="var(--status-critical)"
        />
        <StatTile
          label="Channels"
          value={data.channels_enabled.length}
          sub={data.channels_enabled.join(", ") || "console only"}
        />
      </div>

      <Section
        title="Outbox"
        description="Every message the system sent when an action executed — and every one that did not get through."
        action={
          <div className="flex gap-2 items-center flex-wrap">
            <select
              className="input w-auto"
              value={filter}
              onChange={(e) => setFilter(e.target.value as typeof filter)}
              aria-label="Filter by status"
            >
              <option value="all">All</option>
              <option value="queued">Queued</option>
              <option value="sent">Sent</option>
              <option value="failed">Failed</option>
            </select>
            {data.counts.failed > 0 && can("engines:run") && (
              <button
                className="btn-ghost px-3 py-2 text-[12px] font-medium disabled:opacity-50"
                disabled={retrying}
                onClick={retry}
              >
                {retrying ? "Retrying…" : `Retry ${data.counts.failed} failed`}
              </button>
            )}
          </div>
        }
      >
        {retryError && (
          <p role="alert" className="text-[13px]" style={{ color: "var(--status-critical)" }}>
            {retryError}
          </p>
        )}

        {data.notifications.length === 0 ? (
          <div className="card p-6 text-[13px]" style={{ color: "var(--text-muted)" }}>
            Nothing here yet. Approve an action and the people it affects get told — the message
            lands in this list.
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {data.notifications.map((n) => (
              <MessageRow key={n.id} row={n} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}

function MessageRow({ row }: { row: NotificationRow }) {
  const accent = STATUS_COLOR[row.status] ?? "var(--text-muted)";
  return (
    <article
      className="card px-4 py-3 flex flex-col gap-1.5 text-[12.5px]"
      style={{ borderLeft: `3px solid ${accent}` }}
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          <span aria-hidden style={{ color: "var(--text-muted)" }}>
            {CHANNEL_ICON[row.channel] ?? "•"}
          </span>
          <span className="font-semibold truncate">{row.recipient_name || row.recipient}</span>
          <span style={{ color: "var(--text-muted)" }} className="truncate">
            {row.recipient}
          </span>
          <span
            className="text-[11px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded"
            style={{ color: accent, background: `color-mix(in oklab, ${accent} 12%, transparent)` }}
          >
            {row.status}
          </span>
        </div>
        <span style={{ color: "var(--text-muted)" }} className="shrink-0">
          {timeAgo(row.sent_at ?? row.at)}
        </span>
      </div>

      <div className="font-medium">{row.subject}</div>
      <p style={{ color: "var(--text-secondary)" }}>{row.body}</p>

      <div className="flex gap-3 flex-wrap" style={{ color: "var(--text-muted)" }}>
        <span>{titleCase(row.kind.replace(/_/g, " "))}</span>
        {row.action_card_id && <span>Card #{row.action_card_id}</span>}
        {row.attempts > 1 && <span>{row.attempts} attempts</span>}
      </div>

      {row.error && (
        <div style={{ color: "var(--status-critical)" }}>{row.error}</div>
      )}
    </article>
  );
}
