"use client";

/**
 * Platform Overview.
 *
 * Built from endpoints that exist. Every number here is a count the backend
 * actually computed; where a subsystem cannot answer — platform backup state,
 * the mail daemons — the tile says so rather than showing a reassuring green.
 */
import Link from "next/link";
import { platformApi } from "@/lib/platform-api";
import {
  Badge,
  Card,
  ErrorNote,
  EmptyState,
  PageHeader,
  Spinner,
  StatTile,
  UnknownNote,
  formatRelative,
  humanise,
  useListData,
} from "@/components/platform/ui";

interface Stats {
  total_tenants: number;
  total_users: number;
  total_domains: number;
  total_mailboxes: number;
  tenant_by_status: Record<string, number>;
  subscriptions_by_status: Record<string, number>;
  recent_tenants: Array<{
    id: string;
    name: string;
    status: string;
    owner_email: string;
    created_at: string;
  }>;
}

export default function OverviewPage() {
  const stats = useListData(
    () => platformApi.stats() as unknown as Promise<Stats>,
    [],
  );
  const queue = useListData(() => platformApi.queue({ page_size: 1 }), []);
  const quarantine = useListData(
    () => platformApi.quarantine({ status: "held", page_size: 1 }),
    [],
  );
  const health = useListData(() => platformApi.health(), []);
  const audit = useListData(() => platformApi.audit({ page_size: 8 }), []);

  if (stats.loading) return <Spinner label="Loading platform overview" />;
  if (stats.error || !stats.data) {
    return <ErrorNote message={stats.error ?? "Overview unavailable."} onRetry={stats.reload} />;
  }

  const data = stats.data;
  const byStatus = data.tenant_by_status ?? {};
  const pending = byStatus.pending ?? 0;
  const suspended = byStatus.suspended ?? 0;

  const unhealthy = (health.data?.components ?? []).filter((c) => c.status === "error");
  const unknown = (health.data?.components ?? []).filter((c) => c.status === "unknown");

  return (
    <>
      <PageHeader
        title="Platform Overview"
        description="Current state of the MateMail platform across all organizations."
      />

      {pending > 0 && (
        <div className="mb-4">
          <Link href="/platform/pending" className="block">
            <div
              className="rounded-lg px-3 py-2.5 text-sm"
              style={{ background: "var(--pf-warn-soft)", color: "var(--pf-warn)" }}
            >
              {pending} organization{pending === 1 ? "" : "s"} waiting for approval —
              review now.
            </div>
          </Link>
        </div>
      )}

      {unhealthy.length > 0 && (
        <div className="mb-4">
          <ErrorNote
            message={`Health check failing: ${unhealthy.map((c) => c.name).join(", ")}.`}
          />
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Organizations" value={data.total_tenants.toLocaleString()} />
        <StatTile
          label="Active"
          value={(byStatus.active ?? 0).toLocaleString()}
          tone="ok"
        />
        <StatTile
          label="Pending approval"
          value={pending.toLocaleString()}
          tone={pending > 0 ? "warn" : undefined}
        />
        <StatTile
          label="Suspended"
          value={suspended.toLocaleString()}
          tone={suspended > 0 ? "danger" : undefined}
        />
      </section>

      <section className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Domains" value={data.total_domains.toLocaleString()} />
        <StatTile label="Mailboxes" value={data.total_mailboxes.toLocaleString()} />
        <StatTile label="Active users" value={data.total_users.toLocaleString()} />
        <StatTile
          label="Held in quarantine"
          value={
            quarantine.loading
              ? "…"
              : quarantine.data
                ? quarantine.data.total.toLocaleString()
                : "—"
          }
          hint={quarantine.error ? "Unavailable" : undefined}
        />
      </section>

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            Mail operations
          </h2>
          <dl className="space-y-2 text-sm">
            <Row
              label="Messages in queue"
              value={queue.loading ? "…" : queue.data ? queue.data.total.toLocaleString() : "—"}
            />
            <Row
              label="Quarantined (held)"
              value={
                quarantine.loading
                  ? "…"
                  : quarantine.data
                    ? quarantine.data.total.toLocaleString()
                    : "—"
              }
            />
          </dl>
          <div className="mt-3 flex gap-2">
            <Link href="/platform/queue" className="pf-btn pf-btn-ghost">
              Queue
            </Link>
            <Link href="/platform/quarantine" className="pf-btn pf-btn-ghost">
              Quarantine
            </Link>
          </div>
        </Card>

        <Card>
          <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            Subscriptions
          </h2>
          {Object.keys(data.subscriptions_by_status ?? {}).length === 0 ? (
            <EmptyState title="No subscription records" />
          ) : (
            <dl className="space-y-2 text-sm">
              {Object.entries(data.subscriptions_by_status).map(([status, count]) => (
                <Row key={status} label={humanise(status)} value={count.toLocaleString()} />
              ))}
            </dl>
          )}
          <div className="mt-3">
            <Link href="/platform/plans" className="pf-btn pf-btn-ghost">
              Plans &amp; limits
            </Link>
          </div>
        </Card>
      </div>

      {unknown.length > 0 && (
        <div className="mt-4">
          <UnknownNote
            message={`${unknown.length} component${
              unknown.length === 1 ? "" : "s"
            } cannot be checked from the application (${unknown
              .map((c) => c.name)
              .join(", ")}). They are not assumed healthy — see System Health.`}
          />
        </div>
      )}

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <Card padded={false}>
          <div className="flex items-center justify-between p-4 pb-2">
            <h2 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
              Recent organizations
            </h2>
            <Link href="/platform/organizations" className="text-xs font-medium"
              style={{ color: "var(--pf-accent-text)" }}>
              View all
            </Link>
          </div>
          {data.recent_tenants.length === 0 ? (
            <EmptyState title="No organizations yet" />
          ) : (
            <ul className="px-2 pb-2">
              {data.recent_tenants.map((tenant) => (
                <li key={tenant.id}>
                  <Link
                    href={`/platform/organizations/${tenant.id}`}
                    className="pf-nav-item"
                  >
                    <span className="min-w-0 flex-1 truncate" style={{ color: "var(--pf-text)" }}>
                      {tenant.name}
                    </span>
                    <span className="hidden truncate text-xs pf-faint sm:block">
                      {tenant.owner_email}
                    </span>
                    <Badge status={tenant.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card padded={false}>
          <div className="flex items-center justify-between p-4 pb-2">
            <h2 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
              Recent platform activity
            </h2>
            <Link href="/platform/audit" className="text-xs font-medium"
              style={{ color: "var(--pf-accent-text)" }}>
              Audit log
            </Link>
          </div>
          {audit.loading ? (
            <Spinner />
          ) : !audit.data || audit.data.results.length === 0 ? (
            <EmptyState
              title="No platform actions recorded yet"
              detail="Approvals, suspensions and recovery actions appear here."
            />
          ) : (
            <ul className="px-2 pb-2">
              {audit.data.results.map((row) => (
                <li key={row.id} className="pf-nav-item" style={{ cursor: "default" }}>
                  <span className="min-w-0 flex-1 truncate" style={{ color: "var(--pf-text)" }}>
                    {humanise(row.action)}
                  </span>
                  <span className="hidden truncate text-xs pf-faint sm:block">
                    {row.actor_email}
                  </span>
                  <span className="whitespace-nowrap text-xs pf-faint">
                    {formatRelative(row.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="pf-muted">{label}</dt>
      <dd className="pf-num font-medium" style={{ color: "var(--pf-text)" }}>
        {value}
      </dd>
    </div>
  );
}
