"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AtSign,
  CheckCircle2,
  Clock3,
  CreditCard,
  Gauge,
  Globe2,
  HardDrive,
  Mail,
  RefreshCw,
  ShieldCheck,
  Users,
  XCircle,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalCard,
  PortalMetric,
  PortalNotice,
  PortalPageHeading,
  PortalProgress,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface Plan {
  tier: string;
  display_name: string;
  price_monthly: string;
  max_domains: number;
  max_mailboxes: number;
  max_members: number;
  max_aliases: number;
  default_storage_per_mailbox_mb: number;
  max_storage_per_mailbox_mb: number;
  max_storage_total_mb: number;
  max_messages_per_hour_per_mailbox: number;
  max_messages_per_day_per_tenant: number;
  includes_spam_quarantine: boolean;
  includes_audit_logs: boolean;
  includes_queue_visibility: boolean;
  includes_backup_controls: boolean;
  includes_team_roles: boolean;
}

interface Subscription {
  id: string;
  status: string;
  status_display: string;
  plan: Plan;
  trial_ends_at: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
  created_at: string;
}

interface Usage {
  domains: number;
  mailboxes: number;
  members: number;
  aliases: number;
  storage_allocated_mb: number;
  storage_used_mb: number;
  max_domains: number | null;
  max_mailboxes: number | null;
  max_members: number | null;
  max_aliases: number | null;
  max_storage_per_mailbox_mb: number | null;
  max_storage_total_mb: number | null;
}

interface BillingData {
  subscription: Subscription | null;
  usage: Usage;
  trial_days_left: number;
}

function formatStorage(mb: number | null | undefined) {
  if (mb === null || mb === undefined) return "—";
  if (mb >= 1024) {
    const gb = mb / 1024;
    return (Number.isInteger(gb) ? gb : gb.toFixed(1)) + " GB";
  }
  return mb + " MB";
}

function UsageItem({
  label,
  used,
  max,
  icon,
}: {
  label: string;
  used: number;
  max: number | null;
  icon: React.ReactNode;
}) {
  const percent = max && max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  return (
    <div className="portal-usage-item">
      <div className="portal-usage-head">
        <span>{icon}<strong>{label}</strong></span>
        <span><strong>{used}</strong>{max !== null ? " / " + max : " / —"}</span>
      </div>
      <PortalProgress value={percent} />
    </div>
  );
}

function Feature({
  label,
  included,
}: {
  label: string;
  included: boolean;
}) {
  return (
    <div className={"portal-plan-feature " + (included ? "included" : "excluded")}>
      {included ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
      <span>{label}</span>
    </div>
  );
}

export default function BillingPage() {
  const [data, setData] = useState<BillingData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const fetchBilling = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setLoadError("");
    try {
      const response = await apiRequest("/api/billing/");
      const body = await response.json().catch(() => null);
      if (response.ok && body) {
        setData(body);
      } else {
        setLoadError(body?.detail ?? "Billing information could not be loaded.");
      }
    } catch {
      setLoadError("Billing information could not be loaded.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await fetchBilling(false);
      setLoading(false);
    })();
  }, [fetchBilling]);

  if (loading) {
    return (
      <div className="portal-page">
        <PortalSkeleton className="mb-5 h-20 w-full" />
        <div className="grid gap-5 lg:grid-cols-2">
          <PortalSkeleton className="h-[330px] w-full" />
          <PortalSkeleton className="h-[330px] w-full" />
        </div>
      </div>
    );
  }

  if (!data || loadError) {
    return (
      <div className="portal-page">
        <PortalPageHeading title="Billing & usage" description="Your plan, resource allowances and current usage." />
        <PortalNotice tone="danger">{loadError || "Billing information could not be loaded."}</PortalNotice>
      </div>
    );
  }

  const { subscription, usage, trial_days_left: trialDaysLeft } = data;

  if (!subscription) {
    return (
      <div className="portal-page">
        <PortalPageHeading title="Billing & usage" description="Your plan, resource allowances and current usage." />
        <PortalNotice tone="warn">
          <CreditCard className="mt-0.5 h-4 w-4 shrink-0" />
          <span>No subscription is currently attached to this workspace. Contact MateMail support for plan activation.</span>
        </PortalNotice>
      </div>
    );
  }

  const plan = subscription.plan;
  const price = Number(plan.price_monthly || 0);
  const isTrial = subscription.status === "trialing";

  return (
    <div className="portal-page">
      <PortalPageHeading
        title="Billing & usage"
        description="Your current plan, resource allowances and room to grow."
        actions={
          <button type="button" className="portal-button secondary" onClick={() => fetchBilling(true)}>
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
            Refresh
          </button>
        }
      />

      {isTrial && (
        <div className="mb-5">
          <PortalNotice tone="info">
            <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <strong>{trialDaysLeft} {trialDaysLeft === 1 ? "day" : "days"} left in this trial.</strong>
              {subscription.trial_ends_at ? " Trial end: " + new Date(subscription.trial_ends_at).toLocaleDateString() + "." : ""}
            </span>
          </PortalNotice>
        </div>
      )}

      <div className="portal-billing-grid">
        <PortalCard className="portal-plan-card">
          <div className="portal-plan-top">
            <span className="portal-plan-symbol"><CreditCard className="h-5 w-5" /></span>
            <PortalStatus value={subscription.status_display} />
          </div>
          <div className="portal-plan-eyebrow">CURRENT PLAN</div>
          <h2>{plan.display_name}</h2>
          <div className="portal-plan-price">
            {price > 0 ? (
              <><strong>{"$" + price.toFixed(2)}</strong><span>/ month</span></>
            ) : (
              <strong>Free</strong>
            )}
          </div>
          <p>
            Plan tier: <strong>{plan.tier}</strong>. Resource and mail-policy limits below come directly from the active MateMail plan configuration.
          </p>

          <div className="portal-plan-allowances">
            <span><CheckCircle2 className="h-4 w-4" />{plan.max_mailboxes} mailboxes</span>
            <span><CheckCircle2 className="h-4 w-4" />{plan.max_domains} domains</span>
            <span><CheckCircle2 className="h-4 w-4" />{plan.max_members} team members</span>
            <span><CheckCircle2 className="h-4 w-4" />{plan.max_aliases} aliases</span>
          </div>

          <PortalNotice tone="info">
            Self-service plan switching is not implemented in the MateMail billing backend. Plan changes are handled through support rather than simulated in this Portal.
          </PortalNotice>
        </PortalCard>

        <PortalCard title="Workspace usage" subtitle="Live counts and storage reported by MateMail.">
          <div className="portal-usage-list">
            <UsageItem label="Domains" used={usage.domains} max={usage.max_domains} icon={<Globe2 className="h-4 w-4" />} />
            <UsageItem label="Mailboxes" used={usage.mailboxes} max={usage.max_mailboxes} icon={<Mail className="h-4 w-4" />} />
            <UsageItem label="Team members" used={usage.members} max={usage.max_members} icon={<Users className="h-4 w-4" />} />
            <UsageItem label="Aliases" used={usage.aliases} max={usage.max_aliases} icon={<AtSign className="h-4 w-4" />} />
          </div>

          <div className="portal-storage-summary">
            <div>
              <span>Actual mailbox storage used</span>
              <strong>{formatStorage(usage.storage_used_mb)}</strong>
            </div>
            <div>
              <span>Allocated mailbox quota</span>
              <strong>{formatStorage(usage.storage_allocated_mb)}</strong>
            </div>
          </div>
        </PortalCard>
      </div>

      <div className="portal-metrics-grid mt-5">
        <PortalMetric
          label="Default mailbox storage"
          value={formatStorage(plan.default_storage_per_mailbox_mb)}
          detail="Default quota assigned to a new mailbox"
          icon={<HardDrive className="h-4 w-4" />}
        />
        <PortalMetric
          label="Per-mailbox ceiling"
          value={formatStorage(plan.max_storage_per_mailbox_mb)}
          detail="Largest quota one mailbox may receive"
          icon={<Gauge className="h-4 w-4" />}
        />
        <PortalMetric
          label="Per-domain storage pool"
          value={formatStorage(plan.max_storage_total_mb)}
          detail="Mail Engine storage pool configured for each domain"
          icon={<HardDrive className="h-4 w-4" />}
        />
        <PortalMetric
          label="Workspace daily send cap"
          value={plan.max_messages_per_day_per_tenant.toLocaleString()}
          detail="Outbound messages per day across this workspace"
          icon={<Mail className="h-4 w-4" />}
        />
      </div>

      <div className="grid gap-5 mt-5 lg:grid-cols-2">
        <PortalCard title="Mail policy limits" subtitle="Real outbound reputation safeguards configured by your plan.">
          <div className="portal-detail-list">
            <div className="portal-detail-row">
              <dt>Per mailbox / hour</dt>
              <dd>{plan.max_messages_per_hour_per_mailbox.toLocaleString()} outbound messages</dd>
            </div>
            <div className="portal-detail-row">
              <dt>Workspace / day</dt>
              <dd>{plan.max_messages_per_day_per_tenant.toLocaleString()} outbound messages</dd>
            </div>
            <div className="portal-detail-row">
              <dt>Plan period</dt>
              <dd>
                {subscription.current_period_start
                  ? new Date(subscription.current_period_start).toLocaleDateString()
                  : "Not set"}
                {" — "}
                {subscription.current_period_end
                  ? new Date(subscription.current_period_end).toLocaleDateString()
                  : "Not set"}
              </dd>
            </div>
          </div>
          <div className="mt-4">
            <PortalNotice tone="info">
              These are policy ceilings, not a guarantee of delivery volume. Mail policy and abuse controls can still refuse traffic that violates workspace or sender rules.
            </PortalNotice>
          </div>
        </PortalCard>

        <PortalCard title="Included capabilities" subtitle="Features enabled by this plan.">
          <div className="portal-plan-features">
            <Feature label="Spam filter & quarantine" included={plan.includes_spam_quarantine} />
            <Feature label="Audit logs" included={plan.includes_audit_logs} />
            <Feature label="Mail queue visibility" included={plan.includes_queue_visibility} />
            <Feature label="Backup controls" included={plan.includes_backup_controls} />
            <Feature label="Team roles & permissions" included={plan.includes_team_roles} />
          </div>

          <div className="mt-5">
            <PortalNotice tone="success">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
              <span>All values on this page are read from your real MateMail subscription and usage state.</span>
            </PortalNotice>
          </div>
        </PortalCard>
      </div>
    </div>
  );
}
