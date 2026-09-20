"use client";

/**
 * Mailboxes across every organization, with platform suspension.
 *
 * Platform suspension is deliberately a different thing from the organization
 * disabling a mailbox: it is applied by MateMail, and a tenant administrator
 * cannot simply undo it. No password and no message content appears here.
 */
import { useCallback, useState } from "react";
import Link from "next/link";
import { Ban, Play } from "lucide-react";

import { platformApi, type PlatformMailbox } from "@/lib/platform-api";
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
  formatBytes,
  formatRelative,
  useDebounced,
  useListData,
} from "@/components/platform/ui";

const STATUS_OPTIONS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "disabled", label: "Disabled" },
  { value: "suspended", label: "Suspended" },
];

type Target = { mailbox: PlatformMailbox; suspend: boolean } | null;

export default function MailboxesPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(search);

  const { data, error, loading, reload } = useListData(
    () => platformApi.mailboxes({ search: term, status, page }),
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
        if (target.suspend) {
          await platformApi.suspendMailbox(target.mailbox.id, reason);
          setNotice(`${target.mailbox.email} suspended.`);
        } else {
          await platformApi.releaseMailbox(target.mailbox.id);
          setNotice(`${target.mailbox.email} released.`);
        }
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
        title="Mailboxes"
        description="Every mailbox on the platform. Suspension here is applied by MateMail and cannot be reversed by the organization."
      />

      <Toolbar>
        <SearchInput
          value={search}
          onChange={(v) => { setSearch(v); setPage(1); }}
          placeholder="Search address, name, domain…"
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
          <EmptyState title="No mailboxes match" detail="Adjust the filters above." />
        </div>
      ) : (
        <>
          <DataTable
            columns={[
              "Mailbox",
              "Organization",
              "Status",
              "Storage",
              "Engine",
              "Last login",
              "",
            ]}
          >
            {data.results.map((mailbox) => {
              const suspended = mailbox.status === "suspended";
              return (
                <tr key={mailbox.id}>
                  <td>
                    <div className="font-medium" style={{ color: "var(--pf-text)" }}>
                      {mailbox.email}
                    </div>
                    {mailbox.full_name && (
                      <div className="text-xs pf-faint">{mailbox.full_name}</div>
                    )}
                  </td>
                  <td>
                    {mailbox.tenant ? (
                      <Link
                        href={`/platform/organizations/${mailbox.tenant.id}`}
                        style={{ color: "var(--pf-accent-text)" }}
                      >
                        {mailbox.tenant.name}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <Badge status={mailbox.status} />
                  </td>
                  <td className="pf-num pf-muted whitespace-nowrap">
                    {formatBytes(mailbox.storage_used_mb)} / {formatBytes(mailbox.quota_mb)}
                  </td>
                  <td>
                    <Badge tone={mailbox.mail_engine_provisioned ? "ok" : "neutral"}>
                      {mailbox.mail_engine_provisioned ? "Provisioned" : "Pending"}
                    </Badge>
                  </td>
                  <td className="pf-muted whitespace-nowrap">
                    {formatRelative(mailbox.last_login)}
                  </td>
                  <td>
                    <button
                      type="button"
                      className={`pf-btn ${suspended ? "pf-btn-ghost" : "pf-btn-danger"}`}
                      onClick={() => {
                        setNotice(null);
                        setTarget({ mailbox, suspend: !suspended });
                      }}
                    >
                      {suspended ? (
                        <>
                          <Play className="h-3.5 w-3.5" aria-hidden="true" />
                          Release
                        </>
                      ) : (
                        <>
                          <Ban className="h-3.5 w-3.5" aria-hidden="true" />
                          Suspend
                        </>
                      )}
                    </button>
                  </td>
                </tr>
              );
            })}
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
        title={target?.suspend ? "Suspend mailbox" : "Release mailbox"}
        body={
          target?.suspend ? (
            <>
              <strong>{target.mailbox.email}</strong> will stop sending and
              receiving. The organization cannot reverse a platform suspension.
            </>
          ) : (
            <>
              <strong>{target?.mailbox.email}</strong> will return to normal
              operation.
            </>
          )
        }
        confirmLabel={target?.suspend ? "Suspend" : "Release"}
        destructive={target?.suspend}
        requireReason={target?.suspend ?? false}
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
