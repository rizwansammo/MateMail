"use client";

/**
 * Backups.
 *
 * This page exists mostly to say something uncomfortable clearly: the
 * application cannot see the platform's disaster-recovery backups, and it does
 * not pretend to.
 *
 * The real backups are the restic system on the host — a nightly timer, with
 * retention, restore drills and offsite copies. It is deliberately outside the
 * application, because a backup system the application can write to is one an
 * application compromise can destroy. The cost of that design is exactly this:
 * no green tick here. Showing one derived from a timer that is merely
 * configured is the same fabrication that made the old per-tenant backup task
 * report archives it never created.
 *
 * There is no restore button, and there should not be. A production restore is
 * a controlled DevOps operation with a drill behind it, not a web click.
 */
import { platformApi } from "@/lib/platform-api";
import {
  Badge,
  Card,
  DataTable,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
  UnknownNote,
  formatDateTime,
  humanise,
  useListData,
} from "@/components/platform/ui";

export default function BackupsPage() {
  const { data, error, loading, reload } = useListData(() => platformApi.backups(), []);

  return (
    <>
      <PageHeader
        title="Backups"
        description="Disaster-recovery status for the MateMail platform."
      />

      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data ? null : (
        <>
          <Card>
            <div className="mb-2 flex items-start justify-between gap-3">
              <h2 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
                Platform disaster recovery
              </h2>
              <Badge tone="warn">Not observable here</Badge>
            </div>
            <p className="mb-3 text-sm pf-muted">{data.platform_backups.detail}</p>

            <p className="pf-label mb-1.5">Where to check</p>
            <ul className="space-y-1">
              {data.platform_backups.where_to_look.map((entry) => (
                <li
                  key={entry}
                  className="rounded px-2 py-1 font-mono text-xs"
                  style={{ background: "var(--pf-surface-3)", color: "var(--pf-text)" }}
                >
                  {entry}
                </li>
              ))}
            </ul>
          </Card>

          <div className="mt-4">
            <UnknownNote
              message="No backup state is inferred or displayed as successful. This page reports only what the application can verify."
            />
          </div>

          <section className="mt-5">
            <h2 className="mb-1 text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
              Per-organization backup requests
            </h2>
            <p className="mb-3 text-sm pf-muted">{data.tenant_backup_jobs.detail}</p>

            {data.tenant_backup_jobs.recent.length === 0 ? (
              <div className="pf-card">
                <EmptyState
                  title="No requests recorded"
                  detail="Organizations that request an export appear here."
                />
              </div>
            ) : (
              <DataTable columns={["Organization", "Scope", "Status", "Outcome", "Requested"]}>
                {data.tenant_backup_jobs.recent.map((job) => (
                  <tr key={job.id}>
                    <td style={{ color: "var(--pf-text)" }}>{job.tenant ?? "—"}</td>
                    <td className="pf-muted">{humanise(job.scope)}</td>
                    <td>
                      <Badge status={job.status} />
                    </td>
                    <td className="pf-muted" style={{ maxWidth: "24rem" }}>
                      {job.error_message || "—"}
                    </td>
                    <td className="pf-muted whitespace-nowrap">
                      {formatDateTime(job.created_at)}
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}
          </section>
        </>
      )}
    </>
  );
}
