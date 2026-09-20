"use client";

/**
 * Operational mail logs, across organizations.
 *
 * These are `MailLog` rows — provisioning, delivery and policy events the
 * application records per organization. Platform administrator actions are a
 * different trail and live on the Audit Logs page.
 */
import { useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { platformApi } from "@/lib/platform-api";
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorNote,
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

export default function MailLogsPage() {
  // useSearchParams needs a Suspense boundary in the App Router; the page is
  // linked to with ?tenant=<id> from the organization detail view.
  return (
    <Suspense fallback={<Spinner />}>
      <MailLogsInner />
    </Suspense>
  );
}

function MailLogsInner() {
  const params = useSearchParams();
  const tenantFilter = params.get("tenant") ?? "";

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(search);

  const { data, error, loading, reload } = useListData(
    () => platformApi.logs({ search: term, tenant: tenantFilter, page }),
    [term, tenantFilter, page],
  );

  return (
    <>
      <PageHeader
        title="Mail Logs"
        description="Operational events recorded across organizations."
      />

      <Toolbar>
        <SearchInput
          value={search}
          onChange={(v) => { setSearch(v); setPage(1); }}
          placeholder="Search event, source, organization…"
        />
        {tenantFilter && (
          <Link href="/platform/logs" className="pf-btn pf-btn-ghost">
            Clear organization filter
          </Link>
        )}
      </Toolbar>

      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data || data.results.length === 0 ? (
        <div className="pf-card">
          <EmptyState title="No log entries match" detail="Adjust the search above." />
        </div>
      ) : (
        <>
          <DataTable columns={["Event", "Organization", "Result", "Source", "IP", "When"]}>
            {data.results.map((row) => (
              <tr key={row.id}>
                <td style={{ color: "var(--pf-text)" }}>{humanise(row.event_type)}</td>
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
                <td>
                  <Badge status={row.result} />
                </td>
                <td className="pf-muted">{row.source || "—"}</td>
                <td className="pf-muted pf-num">{row.ip_address ?? "—"}</td>
                <td className="pf-muted whitespace-nowrap">
                  {formatDateTime(row.created_at)}
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
