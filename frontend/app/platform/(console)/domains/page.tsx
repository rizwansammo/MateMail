"use client";

/**
 * Domains across every organization.
 *
 * Read-only. Re-verification and re-provisioning are organization-scoped
 * operations with their own service layer and their own rate limits; giving
 * the platform console a second path into them would duplicate the business
 * logic this codebase deliberately keeps in one place.
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
  useDebounced,
  useListData,
} from "@/components/platform/ui";

const STATUS_OPTIONS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "pending", label: "Pending" },
  { value: "failed", label: "Failed" },
];

const OWNERSHIP_OPTIONS = [
  { value: "", label: "All" },
  { value: "verified", label: "Verified" },
  { value: "pending", label: "Unverified" },
];

export default function DomainsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [ownership, setOwnership] = useState("");
  const [page, setPage] = useState(1);
  const term = useDebounced(search);

  const { data, error, loading, reload } = useListData(
    () =>
      platformApi.domains({
        search: term,
        status,
        ownership_status: ownership,
        page,
      }),
    [term, status, ownership, page],
  );

  return (
    <>
      <PageHeader
        title="Domains"
        description="Every domain on the platform, with verification, DKIM and provisioning state."
      />

      <Toolbar>
        <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }}
          placeholder="Search domain or organization…" />
        <FilterSelect label="Status" value={status}
          onChange={(v) => { setStatus(v); setPage(1); }} options={STATUS_OPTIONS} />
        <FilterSelect label="Ownership" value={ownership}
          onChange={(v) => { setOwnership(v); setPage(1); }} options={OWNERSHIP_OPTIONS} />
      </Toolbar>

      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data || data.results.length === 0 ? (
        <div className="pf-card">
          <EmptyState title="No domains match" detail="Adjust the filters above." />
        </div>
      ) : (
        <>
          <DataTable
            columns={[
              "Domain",
              "Organization",
              "Status",
              "Ownership",
              "DKIM",
              "Engine",
              "DNS score",
              "Added",
            ]}
          >
            {data.results.map((domain) => (
              <tr key={domain.id}>
                <td style={{ color: "var(--pf-text)" }}>
                  <span className="font-medium">{domain.domain}</span>
                  {domain.mail_engine_error && (
                    <div className="text-xs" style={{ color: "var(--pf-danger)" }}>
                      {domain.mail_engine_error}
                    </div>
                  )}
                </td>
                <td>
                  {domain.tenant ? (
                    <Link
                      href={`/platform/organizations/${domain.tenant.id}`}
                      style={{ color: "var(--pf-accent-text)" }}
                    >
                      {domain.tenant.name}
                    </Link>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  <Badge status={domain.status} />
                </td>
                <td>
                  <Badge status={domain.ownership_status} />
                  {domain.verification_last_error && (
                    <div className="text-xs pf-faint">{domain.verification_last_error}</div>
                  )}
                </td>
                <td>
                  <Badge tone={domain.dkim_configured ? "ok" : "warn"}>
                    {domain.dkim_configured ? domain.dkim_selector : "Not set"}
                  </Badge>
                </td>
                <td>
                  <Badge tone={domain.mail_engine_provisioned ? "ok" : "neutral"}>
                    {domain.mail_engine_provisioned ? "Provisioned" : "Not provisioned"}
                  </Badge>
                </td>
                <td className="pf-num">{domain.dns_health_score}</td>
                <td className="pf-muted whitespace-nowrap">
                  {formatDateTime(domain.added_at)}
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
