"use client";

/**
 * Plans and the limits they carry.
 *
 * Read-only, on purpose. Assigning a plan to an organization is an operational
 * action and lives on the organization's own page; editing the catalogue is a
 * pricing decision, and a console button is the wrong place to make one.
 */
import { platformApi } from "@/lib/platform-api";
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
  formatBytes,
  humanise,
  useListData,
} from "@/components/platform/ui";

const LIMIT_LABELS: Record<string, string> = {
  max_domains: "Domains",
  max_mailboxes: "Mailboxes",
  max_members: "Members",
  max_aliases: "Aliases",
  default_storage_per_mailbox_mb: "Default storage / mailbox",
  max_storage_per_mailbox_mb: "Max storage / mailbox",
  max_storage_total_mb: "Total storage",
  max_messages_per_hour_per_mailbox: "Messages / hour / mailbox",
  max_messages_per_day_per_tenant: "Messages / day / org",
};

const STORAGE_KEYS = new Set([
  "default_storage_per_mailbox_mb",
  "max_storage_per_mailbox_mb",
  "max_storage_total_mb",
]);

export default function PlansPage() {
  const { data, error, loading, reload } = useListData(() => platformApi.plans(), []);

  return (
    <>
      <PageHeader
        title="Plans & Limits"
        description="The plan catalogue and the quotas each tier enforces. Assign a plan from an organization's page."
      />

      {error && <ErrorNote message={error} onRetry={reload} />}

      {loading ? (
        <Spinner />
      ) : !data || data.results.length === 0 ? (
        <div className="pf-card">
          <EmptyState title="No plans configured" />
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {data.results.map((plan) => (
            <Card key={plan.id}>
              <div className="mb-3 flex items-start justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
                    {plan.display_name}
                  </h2>
                  <p className="text-xs pf-faint">{plan.tier}</p>
                </div>
                <Badge tone={plan.is_active ? "ok" : "neutral"}>
                  {plan.is_active ? "Active" : "Retired"}
                </Badge>
              </div>

              <div className="mb-3 flex items-baseline gap-2">
                <span className="pf-num text-xl font-semibold" style={{ color: "var(--pf-text)" }}>
                  {plan.price_monthly === 0
                    ? "Free"
                    : plan.price_monthly.toLocaleString(undefined, {
                        style: "currency",
                        currency: "USD",
                      })}
                </span>
                {plan.price_monthly > 0 && <span className="text-xs pf-faint">/ month</span>}
              </div>

              <p className="mb-3 text-xs pf-muted">
                <span className="pf-num font-medium">{plan.subscriber_count}</span>{" "}
                organization{plan.subscriber_count === 1 ? "" : "s"} on this plan
              </p>

              <dl className="space-y-1.5 text-xs">
                {Object.entries(plan.limits).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between gap-3">
                    <dt className="pf-muted">{LIMIT_LABELS[key] ?? humanise(key)}</dt>
                    <dd className="pf-num font-medium" style={{ color: "var(--pf-text)" }}>
                      {STORAGE_KEYS.has(key) ? formatBytes(value) : value.toLocaleString()}
                    </dd>
                  </div>
                ))}
              </dl>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {Object.entries(plan.features)
                  .filter(([, enabled]) => enabled)
                  .map(([key]) => (
                    <Badge key={key} tone="neutral">
                      {humanise(key.replace(/^includes_/, ""))}
                    </Badge>
                  ))}
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
