"use client";

/**
 * The platform audit trail: who did what in this console.
 *
 * Distinct from Mail Logs, which record what the platform did for an
 * organization. These rows record what a platform administrator did to one,
 * and the reason column is the part that is read six months later — "who"
 * is usually obvious from the row, "why" never is.
 */
import { useState } from "react";
import Link from "next/link";

import { platformApi } from "@/lib/platform-api";
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorNote,
  FilterSelect,
  PageHeader,
  Pagination,
  SearchInput,
  Spinner,
  Toolbar,
  formatDateTime,
  humanise,
  useDebounced,
  useListData,
} from "@/components/platform/ui";

export default function AuditLogsPage() {
  const [search, setSearch] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(search);

  // The filter's options come from the data rather than a hard-coded list, so
  // an action added later appears without a frontend change.
  const actions = useListData(() => platformApi.auditActions(), []);

  const { data, error, loading, reload } = useListData(
    () => platformApi.audit({ search: term, action, page }),
    [term, action, page],
  );

  const actionOptions = [
    { value: "", label: "All actions" },
    ...(actions.data?.actions ?? []).map((value) => ({
      value,
      label: humanise(value),
    })),
  ];

  return (
    <>
      <PageHeader
        title="Audit Logs"
        description="Every platform administrator action, with the reason given at the time."
      />

      <Toolbar>
        <SearchInput
          value={search}
          onChange={(v) => { setSearch(v); setPage(1); }}
          placeholder="Search actor, target, organization…"
        />
        <FilterSelect
          label="Action"
          value={action}
          onChange={(v) => { setAction(v); setPage(1); }}
          options={actionOptions}
        />
      </Toolbar>

      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data || data.results.length === 0 ? (
        <div className="pf-card">
          <EmptyState
            title="No platform actions recorded"
            detail="Approvals, suspensions, plan changes and owner recovery appear here."
          />
        </div>
      ) : (
        <>
          <DataTable
            columns={["When", "Actor", "Action", "Organization", "Target", "Result", "Reason"]}
          >
            {data.results.map((row) => (
              <tr key={row.id}>
                <td className="pf-muted whitespace-nowrap">
                  {formatDateTime(row.created_at)}
                </td>
                <td style={{ color: "var(--pf-text)" }}>{row.actor_email || "system"}</td>
                <td>
                  <Badge tone="info">{humanise(row.action)}</Badge>
                </td>
                <td>
                  {row.tenant ? (
                    <Link
                      href={`/platform/organizations/${row.tenant.id}`}
                      style={{ color: "var(--pf-accent-text)" }}
                    >
                      {row.tenant.name}
                    </Link>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="pf-muted">{row.target_label || "—"}</td>
                <td>
                  <Badge status={row.result} />
                </td>
                <td className="pf-muted" style={{ maxWidth: "20rem" }}>
                  {row.reason || "—"}
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
    </>
  );
}
