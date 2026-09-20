"use client";

/**
 * The approval queue.
 *
 * Oldest first, which is the order the backend returns and the order a queue
 * should be worked. Waiting time is shown as its own column because it is the
 * thing that turns a list into a queue — an organization that has been waiting
 * nine days is a different problem from one that arrived this morning.
 */
import { useCallback, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Clock, XCircle } from "lucide-react";

import { platformApi } from "@/lib/platform-api";
import { describeError } from "@/contexts/platform-auth-context";
import {
  Badge,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
  SuccessNote,
  formatDateTime,
  useListData,
} from "@/components/platform/ui";

interface PendingRow {
  id: string;
  name: string;
  slug: string;
  owner_email: string;
  created_at: string;
  waiting_days: number;
}

type PendingAction = { kind: "approve" | "reject"; tenant: PendingRow } | null;

export default function PendingApprovalsPage() {
  const { data, error, loading, reload } = useListData(
    () => platformApi.pendingTenants() as Promise<PendingRow[]>,
    [],
  );

  const [action, setAction] = useState<PendingAction>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useCallback(
    async (reason: string) => {
      if (!action) return;
      setBusy(true);
      setActionError(null);
      try {
        if (action.kind === "approve") {
          await platformApi.approveTenant(action.tenant.id);
          setNotice(`${action.tenant.name} approved.`);
        } else {
          await platformApi.rejectTenant(action.tenant.id, reason);
          setNotice(`${action.tenant.name} rejected.`);
        }
        setAction(null);
        // Refresh immediately: a queue that still shows a decided row is how
        // the same organization gets approved twice.
        reload();
      } catch (caught) {
        setActionError(describeError(caught, "The action could not be completed."));
      } finally {
        setBusy(false);
      }
    },
    [action, reload],
  );

  return (
    <>
      <PageHeader
        title="Pending Approvals"
        description="Organizations waiting for a decision before they can send or receive mail."
      />

      {notice && (
        <div className="mb-3">
          <SuccessNote message={notice} />
        </div>
      )}
      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data || data.length === 0 ? (
        <div className="pf-card">
          <EmptyState
            title="Nothing waiting"
            detail="New organizations appear here as soon as they sign up."
            icon={CheckCircle2}
          />
        </div>
      ) : (
        <DataTable columns={["Organization", "Owner", "Requested", "Waiting", "Decision"]}>
          {data.map((tenant) => (
            <tr key={tenant.id}>
              <td>
                <Link
                  href={`/platform/organizations/${tenant.id}`}
                  className="font-medium"
                  style={{ color: "var(--pf-accent-text)" }}
                >
                  {tenant.name}
                </Link>
                <div className="text-xs pf-faint">{tenant.slug}</div>
              </td>
              <td className="pf-muted">{tenant.owner_email}</td>
              <td className="pf-muted whitespace-nowrap">
                {formatDateTime(tenant.created_at)}
              </td>
              <td>
                <Badge tone={tenant.waiting_days >= 3 ? "warn" : "neutral"}>
                  <Clock className="h-3 w-3" aria-hidden="true" />
                  {tenant.waiting_days}d
                </Badge>
              </td>
              <td>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="pf-btn pf-btn-primary"
                    onClick={() => {
                      setNotice(null);
                      setAction({ kind: "approve", tenant });
                    }}
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                    Approve
                  </button>
                  <button
                    type="button"
                    className="pf-btn pf-btn-ghost"
                    onClick={() => {
                      setNotice(null);
                      setAction({ kind: "reject", tenant });
                    }}
                  >
                    <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
                    Reject
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </DataTable>
      )}

      <ConfirmDialog
        open={action !== null}
        title={
          action?.kind === "approve" ? "Approve organization" : "Reject organization"
        }
        body={
          action?.kind === "approve" ? (
            <>
              <strong>{action.tenant.name}</strong> will be able to provision domains
              and send mail. The owner is {action.tenant.owner_email}.
            </>
          ) : (
            <>
              <strong>{action?.tenant.name}</strong> will be refused. The reason you
              give is shown to the owner, so keep it customer-appropriate — internal
              notes belong in the audit log.
            </>
          )
        }
        confirmLabel={action?.kind === "approve" ? "Approve" : "Reject"}
        destructive={action?.kind === "reject"}
        // Approval records an actor and a timestamp on its own; a rejection is
        // shown to the customer, so it has to say why.
        requireReason={action?.kind === "reject"}
        busy={busy}
        error={actionError}
        onConfirm={run}
        onCancel={() => {
          setAction(null);
          setActionError(null);
        }}
      />
    </>
  );
}
