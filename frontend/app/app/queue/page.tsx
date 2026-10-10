"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Clock3,
  Mail,
  RefreshCw,
  Search,
  Send,
  XCircle,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalEmptyState,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface QueueMessage {
  id: string;
  engine_message_id: string;
  sender: string;
  recipient: string;
  subject: string;
  status: string;
  status_display: string;
  reason: string;
  queued_at: string;
  last_retry: string | null;
  next_retry: string | null;
  retry_count: number;
}

const STATUSES = [
  { value: "", label: "Active queue" },
  { value: "pending", label: "Pending" },
  { value: "deferred", label: "Deferred" },
  { value: "failed", label: "Failed" },
  { value: "delivered", label: "Delivered" },
  { value: "cancelled", label: "Cancelled" },
];

function fmt(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function canCancel(status: string) {
  return status === "pending" || status === "deferred" || status === "failed";
}

export default function QueuePage() {
  const [messages, setMessages] = useState<QueueMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [confirmCancel, setConfirmCancel] = useState("");
  const [cancelling, setCancelling] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [actionFailed, setActionFailed] = useState(false);

  const fetchQueue = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const params = statusFilter ? "?status=" + encodeURIComponent(statusFilter) : "";
      const response = await apiRequest("/api/queue/" + params);
      const data = await response.json().catch(() => null);
      if (response.ok && Array.isArray(data)) {
        setMessages(data);
      } else if (response.status === 403) {
        setLoadError("Your workspace role does not permit access to the mail queue.");
      } else {
        setLoadError(data?.detail ?? "Mail queue could not be loaded.");
      }
    } catch {
      setLoadError("Mail queue could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchQueue(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchQueue]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return messages;
    return messages.filter((message) =>
      [
        message.sender,
        message.recipient,
        message.subject,
        message.status,
        message.reason,
      ].some((value) => value.toLowerCase().includes(needle))
    );
  }, [messages, query]);

  async function cancelMessage(message: QueueMessage) {
    setCancelling(message.id);
    setActionMessage("");
    setActionFailed(false);
    try {
      const response = await apiRequest("/api/queue/" + message.id + "/cancel/", {
        method: "POST",
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setMessages((current) => current.map((item) => item.id === message.id ? data : item));
        setConfirmCancel("");
        setActionMessage("Queued message cancelled.");
      } else {
        setActionFailed(true);
        setActionMessage(data?.detail ?? "The queued message could not be cancelled.");
      }
    } catch {
      setActionFailed(true);
      setActionMessage("The queued message could not be cancelled.");
    } finally {
      setCancelling("");
    }
  }

  return (
    <div className="portal-page astra-resource-page astra-advanced-page astra-operations-page">
      <PortalPageHeading
        title="Mail queue"
        description="Outbound messages waiting for delivery, retry, or administrator action."
        actions={
          <button type="button" className="portal-button secondary" onClick={fetchQueue} disabled={loading}>
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
            Refresh
          </button>
        }
      />

      {actionMessage && (
        <div className="mb-5">
          <PortalNotice tone={actionFailed ? "danger" : "success"}>{actionMessage}</PortalNotice>
        </div>
      )}

      {loadError && (
        <div className="mb-5"><PortalNotice tone="danger">{loadError}</PortalNotice></div>
      )}

      <PortalCard className="portal-management-card" bodyClassName="!p-0">
        <div className="portal-management-toolbar">
          <div className="portal-management-search">
            <Search className="h-4 w-4" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search sender, recipient, subject or reason…"
              aria-label="Search mail queue"
            />
          </div>
          <select
            className="portal-input !h-[34px] !min-h-[34px] !w-[170px] !py-0 text-[10px]"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            aria-label="Filter queue status"
          >
            {STATUSES.map((status) => (
              <option key={status.value} value={status.value}>{status.label}</option>
            ))}
          </select>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : !loadError && filtered.length === 0 ? (
          <PortalEmptyState
            title={messages.length ? "No matching messages" : "Queue is clear"}
            description={messages.length ? "Try a different search or status filter." : "There are no messages in the selected queue state."}
          />
        ) : !loadError ? (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Message</th>
                  <th>Recipient</th>
                  <th>Status</th>
                  <th>Retries</th>
                  <th>Next retry</th>
                  <th>Queued</th>
                  <th className="text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((message) => (
                  <tr key={message.id}>
                    <td>
                      <div className="portal-identity-cell">
                        <span className="portal-avatar"><Send className="h-4 w-4" /></span>
                        <span>
                          <strong>{message.subject || "No subject"}</strong>
                          <small>{message.sender}</small>
                        </span>
                      </div>
                    </td>
                    <td><code>{message.recipient}</code></td>
                    <td>
                      <PortalStatus value={message.status_display || message.status} />
                      {message.reason && (
                        <div className="portal-table-note">{message.reason}</div>
                      )}
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1.5">
                        <Clock3 className="h-3.5 w-3.5 text-[var(--portal-faint)]" />
                        {message.retry_count}
                      </span>
                      {message.last_retry && <div className="portal-table-note">Last: {fmt(message.last_retry)}</div>}
                    </td>
                    <td>{fmt(message.next_retry)}</td>
                    <td>{fmt(message.queued_at)}</td>
                    <td>
                      <div className="portal-inline-actions">
                        {canCancel(message.status) && (
                          <button
                            type="button"
                            className="portal-action-button danger"
                            onClick={() => setConfirmCancel(message.id)}
                            disabled={cancelling === message.id}
                            aria-label={"Cancel queued message " + message.subject}
                          >
                            <XCircle className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                      {confirmCancel === message.id && (
                        <div className="portal-confirm-inline">
                          <PortalNotice tone="warn">
                            <div className="flex-1">
                              Cancel delivery to <strong>{message.recipient}</strong>? MateMail will ask the Mail Engine to remove it before changing local status.
                            </div>
                            <div className="flex gap-2">
                              <PortalButton type="button" variant="secondary" onClick={() => setConfirmCancel("")} disabled={cancelling === message.id}>Keep</PortalButton>
                              <PortalButton type="button" variant="danger" onClick={() => cancelMessage(message)} disabled={cancelling === message.id}>
                                {cancelling === message.id ? "Cancelling…" : "Cancel message"}
                              </PortalButton>
                            </div>
                          </PortalNotice>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </PortalCard>

      <div className="mt-4">
        <PortalNotice tone="info">
          <Mail className="mt-0.5 h-4 w-4 shrink-0" />
          <span>By default MateMail shows active pending, deferred and failed messages. Delivered and cancelled records can be selected explicitly.</span>
        </PortalNotice>
      </div>
    </div>
  );
}
