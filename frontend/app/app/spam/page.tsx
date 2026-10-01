"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  RefreshCw,
  Search,
  ShieldAlert,
  Trash2,
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

interface QuarantineMessage {
  id: string;
  engine_message_id: string;
  sender: string;
  recipient: string;
  subject: string;
  spam_score: string;
  status: string;
  status_display: string;
  received_at: string;
  actioned_at: string | null;
}

const STATUSES = [
  { value: "", label: "Held messages" },
  { value: "held", label: "Held" },
  { value: "released", label: "Released" },
  { value: "deleted", label: "Deleted" },
];

function fmt(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function scoreTone(score: string) {
  const value = Number(score);
  if (value >= 15) return "high";
  if (value >= 8) return "medium";
  return "low";
}

export default function SpamPage() {
  const [messages, setMessages] = useState<QuarantineMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [senderSearch, setSenderSearch] = useState("");
  const [senderInput, setSenderInput] = useState("");
  const [releasing, setReleasing] = useState("");
  const [deleting, setDeleting] = useState("");
  const [confirmDelete, setConfirmDelete] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [actionFailed, setActionFailed] = useState(false);

  const fetchQuarantine = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.set("status", statusFilter);
      if (senderSearch) params.set("sender", senderSearch);
      const suffix = params.toString() ? "?" + params.toString() : "";
      const response = await apiRequest("/api/quarantine/" + suffix);
      const data = await response.json().catch(() => null);
      if (response.ok && Array.isArray(data)) {
        setMessages(data);
      } else if (response.status === 403) {
        setLoadError("Your workspace role does not permit access to quarantine.");
      } else {
        setLoadError(data?.detail ?? "Quarantine could not be loaded.");
      }
    } catch {
      setLoadError("Quarantine could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [senderSearch, statusFilter]);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchQuarantine(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchQuarantine]);

  const heldCount = useMemo(
    () => messages.filter((message) => message.status === "held").length,
    [messages]
  );

  async function release(message: QuarantineMessage) {
    setReleasing(message.id);
    setActionMessage("");
    setActionFailed(false);
    try {
      const response = await apiRequest("/api/quarantine/" + message.id + "/release/", {
        method: "POST",
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setMessages((current) => current.map((item) => item.id === message.id ? data : item));
        setActionMessage("Message released from quarantine.");
      } else {
        setActionFailed(true);
        setActionMessage(data?.detail ?? "The message could not be released.");
      }
    } catch {
      setActionFailed(true);
      setActionMessage("The message could not be released.");
    } finally {
      setReleasing("");
    }
  }

  async function remove(message: QuarantineMessage) {
    setDeleting(message.id);
    setActionMessage("");
    setActionFailed(false);
    try {
      const response = await apiRequest("/api/quarantine/" + message.id + "/", {
        method: "DELETE",
      });
      if (response.ok || response.status === 204) {
        setMessages((current) => current.filter((item) => item.id !== message.id));
        setConfirmDelete("");
        setActionMessage("Quarantined message deleted.");
      } else {
        const data = await response.json().catch(() => null);
        setActionFailed(true);
        setActionMessage(data?.detail ?? "The quarantined message could not be deleted.");
      }
    } catch {
      setActionFailed(true);
      setActionMessage("The quarantined message could not be deleted.");
    } finally {
      setDeleting("");
    }
  }

  return (
    <div className="portal-page">
      <PortalPageHeading
        title="Spam & quarantine"
        description="Review messages held by the mail filter and release only mail you trust."
        actions={
          <button type="button" className="portal-button secondary" onClick={fetchQuarantine} disabled={loading}>
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
              value={senderInput}
              onChange={(event) => setSenderInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") setSenderSearch(senderInput.trim());
              }}
              placeholder="Filter by sender…"
              aria-label="Filter quarantine by sender"
            />
          </div>
          <div className="portal-inline-actions">
            <PortalButton
              type="button"
              variant="secondary"
              onClick={() => setSenderSearch(senderInput.trim())}
              disabled={senderInput.trim() === senderSearch}
            >
              Search
            </PortalButton>
            {(senderSearch || senderInput) && (
              <PortalButton
                type="button"
                variant="secondary"
                onClick={() => {
                  setSenderInput("");
                  setSenderSearch("");
                }}
              >
                Clear
              </PortalButton>
            )}
            <select
              className="portal-input !h-[34px] !min-h-[34px] !w-[150px] !py-0 text-[10px]"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              aria-label="Filter quarantine status"
            >
              {STATUSES.map((status) => (
                <option key={status.value} value={status.value}>{status.label}</option>
              ))}
            </select>
          </div>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : !loadError && messages.length === 0 ? (
          <PortalEmptyState
            title={senderSearch ? "No matching quarantined mail" : "No quarantined messages"}
            description={senderSearch ? "Try a different sender filter." : "There are no messages in the selected quarantine state."}
          />
        ) : !loadError ? (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Message</th>
                  <th>Recipient</th>
                  <th>Spam score</th>
                  <th>Status</th>
                  <th>Received</th>
                  <th>Actioned</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {messages.map((message) => (
                  <tr key={message.id}>
                    <td>
                      <div className="portal-identity-cell">
                        <span className="portal-avatar"><ShieldAlert className="h-4 w-4" /></span>
                        <span>
                          <strong>{message.subject || "No subject"}</strong>
                          <small>{message.sender}</small>
                        </span>
                      </div>
                    </td>
                    <td><code>{message.recipient}</code></td>
                    <td>
                      <span className={"portal-score " + scoreTone(message.spam_score)}>
                        {message.spam_score}
                      </span>
                    </td>
                    <td><PortalStatus value={message.status_display || message.status} /></td>
                    <td>{fmt(message.received_at)}</td>
                    <td>{fmt(message.actioned_at)}</td>
                    <td>
                      <div className="portal-inline-actions">
                        {message.status === "held" && (
                          <>
                            <button
                              type="button"
                              className="portal-action-button"
                              onClick={() => release(message)}
                              disabled={releasing === message.id}
                              aria-label={"Release " + message.subject}
                              title="Release to inbox"
                            >
                              <CheckCircle2 className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              className="portal-action-button danger"
                              onClick={() => setConfirmDelete(message.id)}
                              disabled={deleting === message.id}
                              aria-label={"Delete " + message.subject}
                              title="Delete quarantined message"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </>
                        )}
                      </div>

                      {confirmDelete === message.id && (
                        <div className="portal-confirm-inline">
                          <PortalNotice tone="warn">
                            <div className="flex-1">
                              Delete this quarantined message for <strong>{message.recipient}</strong>?
                            </div>
                            <div className="flex gap-2">
                              <PortalButton type="button" variant="secondary" onClick={() => setConfirmDelete("")} disabled={deleting === message.id}>Cancel</PortalButton>
                              <PortalButton type="button" variant="danger" onClick={() => remove(message)} disabled={deleting === message.id}>
                                {deleting === message.id ? "Deleting…" : "Delete"}
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
        <PortalNotice tone={heldCount ? "warn" : "info"}>
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{heldCount ? heldCount + " held message(s) are waiting for review. " : ""}Releasing asks the Mail Engine to release first; MateMail only marks the item released after that succeeds.</span>
        </PortalNotice>
      </div>
    </div>
  );
}
