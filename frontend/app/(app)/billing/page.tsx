"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { CreditCard, CheckCircle2, XCircle, Clock, AlertTriangle } from "lucide-react";

interface Plan {
  tier: string;
  display_name: string;
  price_monthly: string;
  max_domains: number;
  max_mailboxes: number;
  max_members: number;
  max_storage_per_mailbox_mb: number;
  includes_spam_quarantine: boolean;
  includes_audit_logs: boolean;
  includes_queue_visibility: boolean;
  includes_backup_controls: boolean;
  includes_team_roles: boolean;
}

interface Subscription {
  status: string;
  status_display: string;
  plan: Plan;
  trial_ends_at: string | null;
}

interface Usage {
  domains: number;
  mailboxes: number;
  members: number;
  storage_allocated_mb: number;
  max_domains: number | null;
  max_mailboxes: number | null;
  max_members: number | null;
  max_storage_per_mailbox_mb: number | null;
}

interface BillingData {
  subscription: Subscription | null;
  usage: Usage;
  trial_days_left: number;
}

const STATUS_STYLES: Record<string, string> = {
  trialing: "bg-cyan-50 text-cyan-700 ring-cyan-600/20",
  active:   "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  past_due: "bg-red-50 text-red-700 ring-red-600/20",
  cancelled:"bg-slate-100 text-slate-500 ring-slate-400/20",
};

function UsageBar({ label, used, max }: { label: string; used: number; max: number | null }) {
  const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const danger = pct >= 90;
  const warn = pct >= 70;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-slate-500">
        <span>{label}</span>
        <span>
          {used} {max !== null ? `/ ${max}` : "/ —"}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full transition-all ${danger ? "bg-red-500" : warn ? "bg-amber-400" : "bg-cyan-500"}`}
          style={{ width: `${max ? pct : 0}%` }}
        />
      </div>
    </div>
  );
}

function FeatureRow({ label, included }: { label: string; included: boolean }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      {included
        ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
        : <XCircle className="h-4 w-4 shrink-0 text-slate-300" />}
      <span className={included ? "text-slate-700" : "text-slate-400"}>{label}</span>
    </div>
  );
}

export default function BillingPage() {
  const [data, setData] = useState<BillingData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiRequest("/api/billing/")
      .then((r) => r.ok ? r.json() : null)
      .then((d) => setData(d))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="p-6 py-16 text-center text-sm text-slate-400">Loading billing info…</div>;
  }

  if (!data || !data.subscription) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-5 text-sm text-amber-800">
          No active subscription found. Contact support to activate your workspace.
        </div>
      </div>
    );
  }

  const { subscription: sub, usage, trial_days_left } = data;
  const plan = sub.plan;
  const isTrialing = sub.status === "trialing";
  const isExpired = sub.status === "past_due";

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <CreditCard className="h-5 w-5 text-slate-400" />
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Billing &amp; Plan</h1>
          <p className="text-sm text-slate-500">Your current plan, usage, and trial status.</p>
        </div>
      </div>

      {/* Trial expiry banner */}
      {isExpired && (
        <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-semibold">Your free trial has expired.</p>
            <p className="mt-0.5">You cannot add new domains or mailboxes. Contact us to upgrade your plan and restore full access.</p>
          </div>
        </div>
      )}

      {/* Trial countdown */}
      {isTrialing && trial_days_left <= 7 && trial_days_left > 0 && (
        <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <Clock className="mt-0.5 h-4 w-4 shrink-0" />
          <p>
            Your free trial ends in <strong>{trial_days_left} day{trial_days_left !== 1 ? "s" : ""}</strong>.
            Contact us to upgrade before it expires.
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Plan card */}
        <div className="rounded-lg border border-slate-200 bg-white p-6 space-y-4">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Current Plan</p>
              <p className="mt-1 text-2xl font-bold text-slate-900">{plan.display_name}</p>
              {parseFloat(plan.price_monthly) > 0 ? (
                <p className="text-sm text-slate-500">${plan.price_monthly}/month</p>
              ) : (
                <p className="text-sm text-slate-500">Free</p>
              )}
            </div>
            <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset capitalize ${STATUS_STYLES[sub.status] ?? ""}`}>
              {sub.status_display}
            </span>
          </div>

          {isTrialing && sub.trial_ends_at && (
            <div className="flex items-center gap-2 rounded-md bg-cyan-50 px-3 py-2 text-sm text-cyan-700">
              <Clock className="h-4 w-4 shrink-0" />
              <span>
                Trial ends {new Date(sub.trial_ends_at).toLocaleDateString(undefined, { dateStyle: "medium" })}
                {trial_days_left > 0 ? ` · ${trial_days_left} day${trial_days_left !== 1 ? "s" : ""} left` : " · Expired"}
              </span>
            </div>
          )}

          <div className="border-t border-slate-100 pt-4 space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Included Features</p>
            <FeatureRow label="Spam filter &amp; quarantine" included={plan.includes_spam_quarantine} />
            <FeatureRow label="Audit logs" included={plan.includes_audit_logs} />
            <FeatureRow label="Mail queue visibility" included={plan.includes_queue_visibility} />
            <FeatureRow label="Backup controls" included={plan.includes_backup_controls} />
            <FeatureRow label="Team roles &amp; permissions" included={plan.includes_team_roles} />
          </div>

          <div className="border-t border-slate-100 pt-4">
            <p className="text-xs text-slate-400">
              To upgrade your plan, contact{" "}
              <a href="mailto:support@matemail.online" className="font-medium text-cyan-600 hover:underline">
                support@matemail.online
              </a>
            </p>
          </div>
        </div>

        {/* Usage card */}
        <div className="rounded-lg border border-slate-200 bg-white p-6 space-y-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Usage</p>

          <UsageBar label="Domains" used={usage.domains} max={usage.max_domains} />
          <UsageBar label="Mailboxes" used={usage.mailboxes} max={usage.max_mailboxes} />
          <UsageBar label="Team members" used={usage.members} max={usage.max_members} />

          <div className="border-t border-slate-100 pt-4 space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Limits</p>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-slate-600">
              <span>Domains</span>
              <span className="font-medium text-slate-900">{usage.max_domains ?? "—"}</span>
              <span>Mailboxes</span>
              <span className="font-medium text-slate-900">{usage.max_mailboxes ?? "—"}</span>
              <span>Team members</span>
              <span className="font-medium text-slate-900">{usage.max_members ?? "—"}</span>
              <span>Storage / mailbox</span>
              <span className="font-medium text-slate-900">
                {usage.max_storage_per_mailbox_mb
                  ? usage.max_storage_per_mailbox_mb >= 1024
                    ? `${usage.max_storage_per_mailbox_mb / 1024} GB`
                    : `${usage.max_storage_per_mailbox_mb} MB`
                  : "—"}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
