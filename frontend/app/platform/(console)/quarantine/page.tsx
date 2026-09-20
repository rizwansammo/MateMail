"use client";

/**
 * Quarantine, across organizations.
 *
 * Releasing changes a message's disposition; it does not display the message.
 * This page is an abuse and false-positive triage tool, not a mail reader, and
 * the API behind it returns no body to read.
 */
import { useCallback, useState } from "react";
import Link from "next/link";
import { ShieldCheck, Trash2 } from "lucide-react";

import { platformApi, type PlatformQuarantineMessage } from "@/lib/platform-api";
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
  { value: "held", label: "Held" },
  { value: "released", label: "Released" },
  { value: "deleted", label: "Deleted" },
];

type Target = { message: PlatformQuarantineMessage; release: boolean } | null;

export default function QuarantinePage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("held");
  const [page, setPage] = useState(1);
  const term = useDebounced(search);

  const { data, error, loading, reload } = useListData(
    () => platformApi.quarantine({ search: term, status, page }),
    [term, status, page],
  );

  const [target, setTarget] = useState<Target>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useCallback(
    async (reason: string) => {
      if (!target) return;
      setBusy(true);
      setActionError(null);
      try {
        await platformApi.quarantineAction(target.message.id, target.release, reason);
        setNotice(target.release ? "Message released." : "Message deleted.");
        setTarget(null);
        reload();
      } catch (caught) {
        setActionError(describeError(caught, "The action could not be completed."));
      } finally {
        setBusy(false);
      }
    },
    [target, reload],
  );

  return (
    <>
      <PageHeader
        title="Quarantine"
        description="Messages held by the spam filter. Metadata and score only — contents are not shown."
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
            title="Nothing in quarantine"
            detail="Held messages appear here for review."
          />
        </div>
      ) : (
        <>
          <DataTable
            columns={["Sender", "Recipient", "Subject", "Score", "Organization", "Status", "Received", ""]}
          >
            {data.results.map((message) => (
              <tr key={message.id}>
                <td style={{ color: "var(--pf-text)" }}>{message.sender}</td>
                <td className="pf-muted">{message.recipient}</td>
                <td className="pf-muted" style={{ maxWidth: "16rem" }}>
                  <div className="truncate">{message.subject || "—"}</div>
                </td>
                <td>
                  <Badge tone={message.spam_score >= 10 ? "danger" : "warn"}>
                    <span className="pf-num">{message.spam_score.toFixed(2)}</span>
                  </Badge>
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
                <td className="pf-muted whitespace-nowrap">
                  {formatRelative(message.received_at)}
                </td>
                <td>
                  {message.status === "held" && (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        className="pf-btn pf-btn-ghost"
                        onClick={() => {
                          setNotice(null);
                          setTarget({ message, release: true });
                        }}
                      >
                        <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                        Release
                      </button>
                      <button
                        type="button"
                        className="pf-btn pf-btn-danger"
                        onClick={() => {
                          setNotice(null);
                          setTarget({ message, release: false });
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        Delete
                      </button>
                    </div>
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
        title={target?.release ? "Release message" : "Delete message"}
        body={
          target?.release ? (
            <>
              The message from <strong>{target.message.sender}</strong> will be
              delivered to <strong>{target.message.recipient}</strong>.
            </>
          ) : (
            <>
              The message from <strong>{target?.message.sender}</strong> will be
              discarded. This cannot be undone.
            </>
          )
        }
        confirmLabel={target?.release ? "Release" : "Delete"}
        destructive={!target?.release}
        busy={busy}
        error={actionError}
        onConfirm={run}
        onCancel={() => {
          setTarget(null);
          setActionError(null);
        }}
      />
    </>
  );
}
