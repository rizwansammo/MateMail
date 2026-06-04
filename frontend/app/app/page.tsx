"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/auth-context";
import { Activity, Globe2, HardDrive, Inbox, Users } from "lucide-react";
import Link from "next/link";
import { apiRequest } from "@/lib/api";

interface WorkspaceStats {
  domain_count: number;
  active_domain_count: number;
  mailbox_count: number;
  active_mailbox_count: number;
  storage_used_mb: number;
  storage_quota_mb: number;
  member_count: number;
  my_role: string;
  tenant_status: string;
  tenant_plan: string;
}

const STATUS_STYLES: Record<string, string> = {
  trial:     "bg-cyan-50 text-cyan-700 ring-cyan-600/20",
  active:    "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  past_due:  "bg-amber-50 text-amber-700 ring-amber-600/20",
  suspended: "bg-red-50 text-red-700 ring-red-600/20",
  cancelled: "bg-slate-100 text-slate-500 ring-slate-400/20",
};

export default function DashboardPage() {
  const { user, tenant } = useAuth();
  const [stats, setStats] = useState<WorkspaceStats | null>(null);

  useEffect(() => {
    if (!tenant?.id) return;
    apiRequest(`/api/workspaces/${tenant.id}/stats/`)
      .then((res) => res.ok ? res.json() : null)
      .then((data) => { if (data) setStats(data); })
      .catch(() => {});
  }, [tenant?.id]);

  const storageUsedGb = stats ? (stats.storage_used_mb / 1024).toFixed(1) : null;
  const storageQuotaGb = stats ? Math.round(stats.storage_quota_mb / 1024) : null;

  const statCards = [
    {
      label: "Domains",
      value: stats ? `${stats.domain_count}` : "—",
      sub: stats ? `${stats.active_domain_count} active` : null,
      icon: Globe2,
      href: "/app/domains",
    },
    {
      label: "Mailboxes",
      value: stats ? `${stats.mailbox_count}` : "—",
      sub: stats ? `${stats.active_mailbox_count} active` : null,
      icon: Users,
      href: "/app/mailboxes",
    },
    {
      label: "Team members",
      value: stats ? `${stats.member_count}` : "—",
      sub: stats ? stats.my_role : null,
      icon: Inbox,
      href: "/app/team",
    },
    {
      label: "Storage used",
      value: storageUsedGb !== null ? `${storageUsedGb} GB` : "—",
      sub: storageQuotaGb !== null ? `of ${storageQuotaGb} GB` : null,
      icon: HardDrive,
      href: "/app/mailboxes",
    },
  ];

  const setupComplete = stats && stats.domain_count > 0 && stats.mailbox_count > 0;

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-black tracking-tight text-slate-950">
            {tenant ? `${tenant.name}` : "Overview"}
          </h1>
          <p className="mt-0.5 text-sm text-slate-500">Workspace overview</p>
        </div>
        {stats && (
          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[stats.tenant_status] ?? STATUS_STYLES.active}`}>
              {stats.tenant_status.replace("_", " ")}
            </span>
            <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-400/20 capitalize">
              {stats.tenant_plan}
            </span>
          </div>
        )}
      </div>

      {/* Email verification banner */}
      {user && !user.email_verified && (
        <div className="flex items-start gap-3 border border-amber-200 bg-amber-50 p-4">
          <p className="text-sm text-amber-800">
            <strong>Verify your email address</strong> — check your inbox for a
            verification link.{" "}
            <Link href="/verify-email" className="font-semibold underline">
              Resend
            </Link>
          </p>
        </div>
      )}

      {/* Stat cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {statCards.map(({ label, value, sub, icon: Icon, href }) => (
          <Link
            key={label}
            href={href}
            className="flex items-center gap-4 border border-slate-200 bg-white p-5 shadow-sm transition hover:border-slate-300 hover:shadow"
          >
            <div className="grid h-10 w-10 shrink-0 place-items-center bg-slate-50">
              <Icon className="h-5 w-5 text-slate-500" />
            </div>
            <div className="min-w-0">
              <p className="text-2xl font-black text-slate-950">{value}</p>
              <p className="text-xs font-semibold text-slate-500">{label}</p>
              {sub && (
                <p className="mt-0.5 truncate text-xs capitalize text-slate-400">{sub}</p>
              )}
            </div>
          </Link>
        ))}
      </div>

      {/* Onboarding CTA — hidden once fully set up */}
      {!setupComplete && (
        <div className="border border-cyan-200 bg-cyan-50 p-6">
          <h2 className="text-lg font-black text-slate-950">
            Complete your workspace setup
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            Add your first domain, configure DNS records, and create your first
            mailbox to start sending and receiving email.
          </p>
          <Link
            href="/app/onboarding"
            className="mt-4 inline-flex items-center gap-2 bg-cyan-500 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400"
          >
            Start setup
          </Link>
        </div>
      )}

      {/* Quick links */}
      {setupComplete && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Link href="/app/domains" className="border border-slate-200 bg-white p-4 shadow-sm transition hover:border-slate-300">
            <p className="text-sm font-semibold text-slate-800">Manage domains</p>
            <p className="mt-0.5 text-xs text-slate-400">DNS health, DKIM, provisioning</p>
          </Link>
          <Link href="/app/mailboxes" className="border border-slate-200 bg-white p-4 shadow-sm transition hover:border-slate-300">
            <p className="text-sm font-semibold text-slate-800">Manage mailboxes</p>
            <p className="mt-0.5 text-xs text-slate-400">Enable, disable, change passwords</p>
          </Link>
          <Link href="/app/team" className="border border-slate-200 bg-white p-4 shadow-sm transition hover:border-slate-300">
            <p className="text-sm font-semibold text-slate-800">Team &amp; settings</p>
            <p className="mt-0.5 text-xs text-slate-400">Members, roles, workspace name</p>
          </Link>
        </div>
      )}

      {/* Suspended banner */}
      {stats?.tenant_status === "suspended" && (
        <div className="border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <strong>Workspace suspended.</strong> Mail sending and receiving is disabled.
          Contact support to resolve your account status.
        </div>
      )}

      {/* Activity placeholder */}
      <div className="border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-4">
          <h2 className="font-bold text-slate-950">Recent activity</h2>
        </div>
        <div className="flex h-28 items-center justify-center text-sm text-slate-400">
          Mail logs available in Phase 10.
        </div>
      </div>
    </div>
  );
}
