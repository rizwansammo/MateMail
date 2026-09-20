"use client";

/**
 * All organizations.
 *
 * The list endpoint predates this console and returns a plain array capped at
 * 200 rather than a paged envelope. Rather than change an endpoint the
 * existing admin screens already use, the filtering happens server-side and
 * the cap is stated in the UI — an operator who hits it needs to know the list
 * is truncated, not wonder why an organization is missing.
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
  SearchInput,
  Spinner,
  Toolbar,
  UnknownNote,
  formatDateTime,
  useDebounced,
  useListData,
} from "@/components/platform/ui";

interface TenantRow {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan_tier: string | null;
  sub_status: string | null;
  trial_ends_at: string | null;
  owner_email: string;
  domain_count: number;
  mailbox_count: number;
  member_count: number;
  created_at: string;
}

const STATUS_OPTIONS = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "trial", label: "Trial" },
  { value: "pending", label: "Pending" },
  { value: "suspended", label: "Suspended" },
  { value: "rejected", label: "Rejected" },
];

//: The backend slices the queryset at 200.
const SERVER_CAP = 200;

export default function OrganizationsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const term = useDebounced(search);

  const { data, error, loading, reload } = useListData(
    () => platformApi.tenants({ search: term, status }) as Promise<TenantRow[]>,
    [term, status],
  );

  return (
    <>
      <PageHeader
        title="Organizations"
        description="Every organization on the platform, with its plan and current state."
      />

      <Toolbar>
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="Search name or owner email…"
        />
        <FilterSelect label="Status" value={status} onChange={setStatus} options={STATUS_OPTIONS} />
      </Toolbar>

      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data || data.length === 0 ? (
        <div className="pf-card">
          <EmptyState
            title="No organizations match"
            detail="Adjust the search or status filter."
          />
        </div>
      ) : (
        <>
          <DataTable
            columns={[
              "Organization",
              "Owner",
              "Status",
              "Plan",
              "Domains",
              "Mailboxes",
              "Members",
              "Created",
            ]}
          >
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
                <td>
                  <Badge status={tenant.status} />
                </td>
                <td className="pf-muted">
                  {tenant.plan_tier ? (
                    <>
                      {tenant.plan_tier}
                      {tenant.sub_status && (
                        <div className="text-xs pf-faint">{tenant.sub_status}</div>
                      )}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="pf-num">{tenant.domain_count}</td>
                <td className="pf-num">{tenant.mailbox_count}</td>
                <td className="pf-num">{tenant.member_count}</td>
                <td className="pf-muted whitespace-nowrap">
                  {formatDateTime(tenant.created_at)}
                </td>
              </tr>
            ))}
          </DataTable>

          {data.length >= SERVER_CAP && (
            <div className="mt-3">
              <UnknownNote
                message={`Showing the first ${SERVER_CAP} matches. Narrow the search to see the rest — this list is truncated, not complete.`}
              />
            </div>
          )}
        </>
      )}
    </>
  );
}
