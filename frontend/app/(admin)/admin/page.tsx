"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiRequest } from "@/lib/api";
import { Activity, Building2, Globe2, Users, Inbox, TrendingUp } from "lucide-react";

interface Stats {
  total_tenants: number;
  total_users: number;
  total_domains: number;
  total_mailboxes: number;
  tenant_by_status: Record<string, number>;
  subscriptions_by_status: Record<string, number>;
  recent_tenants: {
    id: string;
    name: string;
    status: string;
    owner_email: string;
    created_at: string;
  }[];
}

const STATUS_COLORS: Record<string, string> = {
  trial:     "text-cyan-400",
  active:    "text-emerald-400",
  past_due:  "text-amber-400",
  suspended: "text-red-400",
  cancelled: "text-slate-500",
};

export default function AdminDashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiRequest("/api/platform/stats/")
      .then((r) => r.ok ? r.json() : null)
      .then(setStats)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="p-6 text-sm text-slate-500">Loading…</div>;
  }
  if (!stats) {
    return <div className="p-6 text-sm text-red-400">Failed to load stats.</div>;
  }

  const statCards = [
    { label: "Total tenants",  value: stats.total_tenants,  icon: Building2, color: "text-cyan-400" },
    { label: "Total users",    value: stats.total_users,    icon: Users,     color: "text-violet-400" },
    { label: "Total domains",  value: stats.total_domains,  icon: Globe2,    color: "text-sky-400" },
    { label: "Total mailboxes",value: stats.total_mailboxes,icon: Inbox,     color: "text-emerald-400" },
  ];

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center gap-3">
        <Activity className="h-5 w-5 text-slate-400" />
        <h1 className="text-xl font-semibold text-white">Platform Overview</h1>
      </div>

      {/* Summary cards */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {statCards.map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="rounded-lg border border-slate-800 bg-slate-900 p-5">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p>
              <Icon className={`h-4 w-4 ${color}`} />
            </div>
            <p className={`mt-3 text-3xl font-bold ${color}`}>{value.toLocaleString()}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Tenant status breakdown */}
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-5 space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Tenants by status</p>
          {Object.entries(stats.tenant_by_status).map(([status, count]) => (
            <div key={status} className="flex items-center justify-between text-sm">
              <span className={`capitalize font-medium ${STATUS_COLORS[status] ?? "text-slate-400"}`}>
                {status.replace("_", " ")}
              </span>
              <span className="font-mono text-white">{count}</span>
            </div>
          ))}
        </div>

        {/* Subscription breakdown */}
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-5 space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Subscriptions by status</p>
          {Object.entries(stats.subscriptions_by_status).map(([status, count]) => (
            <div key={status} className="flex items-center justify-between text-sm">
              <span className={`capitalize font-medium ${STATUS_COLORS[status] ?? "text-slate-400"}`}>
                {status.replace("_", " ")}
              </span>
              <span className="font-mono text-white">{count}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Recent sign-ups */}
      <div className="rounded-lg border border-slate-800 bg-slate-900 p-5 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Recent sign-ups</p>
          <Link href="/admin/tenants" className="text-xs text-cyan-400 hover:underline">
            View all →
          </Link>
        </div>
        <div className="divide-y divide-slate-800">
          {stats.recent_tenants.map((t) => (
            <div key={t.id} className="flex items-center gap-4 py-3">
              <div className="min-w-0 flex-1">
                <Link
                  href={`/admin/tenants/${t.id}`}
                  className="text-sm font-medium text-white hover:text-cyan-400"
                >
                  {t.name}
                </Link>
                <p className="text-xs text-slate-500">{t.owner_email}</p>
              </div>
              <span className={`text-xs font-medium capitalize ${STATUS_COLORS[t.status] ?? "text-slate-400"}`}>
                {t.status}
              </span>
              <span className="text-xs text-slate-600">
                {new Date(t.created_at).toLocaleDateString()}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
