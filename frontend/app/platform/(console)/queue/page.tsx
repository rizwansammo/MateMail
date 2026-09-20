"use client";

/**
 * The outbound queue, across organizations.
 *
 * Metadata only. The subject line is here because triaging a spam run means
 * noticing that four thousand messages share one; the body is not, and no
 * endpoint behind this page can return one.
 *
 * Cancel is the only action. Retry and release are not offered because the
 * application has no supported way to push a message back into Postfix's
 * queue — a button that looked like it retried and did nothing would be the
 * queue's version of a fake backup.
 */
import { useCallback, useState } from "react";
import Link from "next/link";
import { XCircle } from "lucide-react";

import { platformApi, type PlatformQueueMessage } from "@/lib/platform-api";
import { describeError } from "@/contexts/platform-auth-context";
import {
  Badge,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorNote,
  FilterSelect,
  PageHeader,
  Pagination,
  SearchInput,
  Spinner,
  SuccessNote,
  Toolbar,
  formatRelative,
  useDebounced,
  useListData,
} from "@/components/platform/ui";

const STATUS_OPTIONS = [
  { value: "", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "deferred", label: "Deferred" },
  { value: "failed", label: "Failed" },
  { value: "delivered", label: "Delivered" },
  { value: "cancelled", label: "Cancelled" },
];

export default function QueuePage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(search);

  const { data, error, loading, reload } = useListData(
    () => platformApi.queue({ search: term, status, page }),
    [term, status, page],
  );

  const [target, setTarget] = useState<PlatformQueueMessage | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const cancel = useCallback(
    async (reason: string) => {
      if (!target) return;
      setBusy(true);
      setActionError(null);
      try {
        await platformApi.cancelQueued(target.id, reason);
        setNotice("Message cancelled.");
        setTarget(null);
        reload();
      } catch (caught) {
        setActionError(describeError(caught, "The message could not be cancelled."));
      } finally {
        setBusy(false);
      }
    },
    [target, reload],
  );

  return (
    <>
      <PageHeader
        title="Mail Queue"
        description="Outbound messages across the platform. Envelope metadata only — message contents are never shown here."
      />

      <Toolbar>
        <SearchInput
          value={search}
          onChange={(v) => { setSearch(v); setPage(1); }}
          placeholder="Search sender, recipient, subject…"
        />
        <FilterSelect
          label="Status"
          value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          options={STATUS_OPTIONS}
        />
      </Toolbar>

      {notice && (
        <div className="mb-3">
          <SuccessNote message={notice} />
        </div>
      )}
      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data || data.results.length === 0 ? (
        <div className="pf-card">
          <EmptyState
            title="The queue is empty"
            detail="Messages awaiting delivery appear here."
          />
        </div>
      ) : (
        <>
          <DataTable
            columns={["Sender", "Recipient", "Subject", "Organization", "Status", "Retries", "Queued", ""]}
          >
            {data.results.map((message) => (
              <tr key={message.id}>
                <td style={{ color: "var(--pf-text)" }}>{message.sender}</td>
                <td className="pf-muted">{message.recipient}</td>
                <td className="pf-muted" style={{ maxWidth: "18rem" }}>
                  <div className="truncate">{message.subject || "—"}</div>
                  {message.reason && (
                    <div className="truncate text-xs" style={{ color: "var(--pf-warn)" }}>
                      {message.reason}
                    </div>
                  )}
                </td>
                <td>
                  {message.tenant ? (
                    <Link
                      href={`/platform/organizations/${message.tenant.id}`}
                      style={{ color: "var(--pf-accent-text)" }}
                    >
                      {message.tenant.name}
                    </Link>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  <Badge status={message.status} />
                </td>
                <td className="pf-num">{message.retry_count}</td>
                <td className="pf-muted whitespace-nowrap">
                  {formatRelative(message.queued_at)}
                </td>
                <td>
                  {["pending", "deferred"].includes(message.status) && (
                    <button
                      type="button"
                      className="pf-btn pf-btn-danger"
                      onClick={() => {
                        setNotice(null);
                        setTarget(message);
                      }}
                    >
                      <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
                      Cancel
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </DataTable>
          <Pagination
            page={data.page}
            pageSize={data.page_size}
            total={data.total}
            hasNext={data.has_next}
            onPage={setPage}
          />
        </>
      )}

      <ConfirmDialog
        open={target !== null}
        title="Cancel queued message"
        body={
          <>
            The message from <strong>{target?.sender}</strong> to{" "}
            <strong>{target?.recipient}</strong> will not be delivered. This
            cannot be undone.
          </>
        }
        confirmLabel="Cancel message"
        destructive
        busy={busy}
        error={actionError}
        onConfirm={cancel}
        onCancel={() => {
          setTarget(null);
          setActionError(null);
        }}
      />
    </>
  );
}
