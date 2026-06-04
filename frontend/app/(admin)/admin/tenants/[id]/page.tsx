"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { apiRequest } from "@/lib/api";
import {
  ArrowLeft,
  Building2,
  CheckCircle2,
  ChevronDown,
  Globe2,
  Inbox,
  Loader2,
  Users,
  XCircle,
} from "lucide-react";

interface Domain { id: string; name: string; status: string; created_at: string }
interface Member { id: string; email: string; full_name: string; role: string; joined_at: string }
interface LogEntry { event_type: string; result: string; source: string; ip_address: string | null; created_at: string }
interface Plan { tier: string; display_name: string }
interface Subscription {
  status: string;
  status_display: string;
  plan: { tier: string; display_name: string; price_monthly: string };
  trial_ends_at: string | null;
}

interface TenantDetail {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
  owner_email: string;
  owner_id: string;
  created_at: string;
  updated_at: string;
  domain_count: number;
  mailbox_count: number;
  member_count: number;
  subscription: Subscription | null;
  domains: Domain[];
  members: Member[];
  recent_logs: LogEntry[];
  available_plans: Plan[];
}

const STATUS_STYLES: Record<string, string> = {
  trial:     "bg-cyan-900/50 text-cyan-300 ring-cyan-700/50",
  active:    "bg-emerald-900/50 text-emerald-300 ring-emerald-700/50",
  past_due:  "bg-amber-900/50 text-amber-300 ring-amber-700/50",
  suspended: "bg-red-900/50 text-red-300 ring-red-700/50",
  cancelled: "bg-slate-800 text-slate-500 ring-slate-700/50",
  trialing:  "bg-cyan-900/50 text-cyan-300 ring-cyan-700/50",
};

const DOMAIN_STATUS_STYLES: Record<string, string> = {
  active:   "text-emerald-400",
  pending:  "text-amber-400",
  inactive: "text-slate-500",
  error:    "text-red-400",
};

export default function AdminTenantDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [tenant, setTenant] = useState<TenantDetail | null>(null);
  const [loading, setLoading] = useState(true);

  // Plan assignment
  const [planTier, setPlanTier] = useState("");
  const [assigningPlan, setAssigningPlan] = useState(false);
  const [planMsg, setPlanMsg] = useState("");

  // Trial extension
  const [extendDays, setExtendDays] = useState("30");
  const [extendingTrial, setExtendingTrial] = useState(false);
  const [extendMsg, setExtendMsg] = useState("");

  // Suspend/activate
  const [actioning, setActioning] = useState(false);

  async function load() {
    setLoading(true);
    const res = await apiRequest(`/api/platform/tenants/${id}/`);
    if (res.ok) setTenant(await res.json());
    setLoading(false);
  }

  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSuspend() {
    setActioning(true);
    const res = await apiRequest(`/api/platform/tenants/${id}/suspend/`, { method: "POST" });
    if (res.ok) await load();
    setActioning(false);
  }

  async function handleActivate() {
    setActioning(true);
    const res = await apiRequest(`/api/platform/tenants/${id}/activate/`, { method: "POST" });
    if (res.ok) await load();
    setActioning(false);
  }

  async function handleAssignPlan(e: React.FormEvent) {
    e.preventDefault();
    if (!planTier) return;
    setPlanMsg("");
    setAssigningPlan(true);
    const res = await apiRequest(`/api/platform/tenants/${id}/plan/`, {
      method: "POST",
      body: JSON.stringify({ plan_tier: planTier }),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      setPlanMsg("Plan assigned.");
      await load();
    } else {
      setPlanMsg(body.detail ?? "Failed.");
    }
    setAssigningPlan(false);
  }

  async function handleExtendTrial(e: React.FormEvent) {
    e.preventDefault();
    setExtendMsg("");
    setExtendingTrial(true);
    const res = await apiRequest(`/api/platform/tenants/${id}/plan/`, {
      method: "POST",
      body: JSON.stringify({ extend_trial_days: parseInt(extendDays, 10) }),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      setExtendMsg(`Trial extended by ${extendDays} days.`);
      await load();
    } else {
      setExtendMsg(body.detail ?? "Failed.");
    }
    setExtendingTrial(false);
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
      </div>
    );
  }

  if (!tenant) {
    return <div className="p-6 text-sm text-red-400">Tenant not found.</div>;
  }

  const isSuspended = tenant.status === "suspended";

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.push("/admin/tenants")}
          className="rounded-md p-1.5 text-slate-500 hover:bg-slate-800 hover:text-white"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-white">{tenant.name}</h1>
            <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ring-1 ring-inset ${STATUS_STYLES[tenant.status] ?? STATUS_STYLES.cancelled}`}>
              {tenant.status.replace("_", " ")}
            </span>
          </div>
          <p className="mt-0.5 text-sm text-slate-500">{tenant.owner_email} · {tenant.slug}</p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left column — actions + info */}
        <div className="space-y-5 lg:col-span-1">

          {/* Quick stats */}
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: "Domains",   value: tenant.domain_count,  icon: Globe2  },
              { label: "Mailboxes", value: tenant.mailbox_count, icon: Inbox   },
              { label: "Members",   value: tenant.member_count,  icon: Users   },
            ].map(({ label, value, icon: Icon }) => (
              <div key={label} className="rounded-lg border border-slate-800 bg-slate-900 p-3 text-center">
                <Icon className="mx-auto h-4 w-4 text-slate-500" />
                <p className="mt-1 text-xl font-bold text-white">{value}</p>
                <p className="text-xs text-slate-500">{label}</p>
              </div>
            ))}
          </div>

          {/* Workspace info */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Info</p>
            {[
              { label: "ID",      value: tenant.id, mono: true },
              { label: "Created", value: new Date(tenant.created_at).toLocaleDateString() },
              { label: "Updated", value: new Date(tenant.updated_at).toLocaleDateString() },
            ].map(({ label, value, mono }) => (
              <div key={label} className="flex items-center justify-between text-xs">
                <span className="text-slate-500">{label}</span>
                <span className={`text-slate-300 ${mono ? "font-mono text-[10px]" : ""}`}>{value}</span>
              </div>
            ))}
          </div>

          {/* Subscription */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Subscription</p>
            {tenant.subscription ? (
              <>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-400">Plan</span>
                  <span className="font-medium text-white capitalize">{tenant.subscription.plan.display_name}</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-400">Status</span>
                  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold capitalize ring-1 ring-inset ${STATUS_STYLES[tenant.subscription.status] ?? STATUS_STYLES.cancelled}`}>
                    {tenant.subscription.status_display}
                  </span>
                </div>
                {tenant.subscription.trial_ends_at && (
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-400">Trial ends</span>
                    <span className="text-slate-300">{new Date(tenant.subscription.trial_ends_at).toLocaleDateString()}</span>
                  </div>
                )}
              </>
            ) : (
              <p className="text-xs text-slate-500">No subscription record.</p>
            )}
          </div>

          {/* Suspend / Activate */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Access control</p>
            {isSuspended ? (
              <button
                onClick={handleActivate}
                disabled={actioning}
                className="flex w-full items-center justify-center gap-2 rounded-md bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
              >
                {actioning ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                Activate workspace
              </button>
            ) : (
              <button
                onClick={handleSuspend}
                disabled={actioning}
                className="flex w-full items-center justify-center gap-2 rounded-md bg-red-800 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {actioning ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />}
                Suspend workspace
              </button>
            )}
          </div>

          {/* Assign plan */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Assign plan</p>
            <form onSubmit={handleAssignPlan} className="space-y-2">
              <div className="relative">
                <select
                  value={planTier}
                  onChange={(e) => setPlanTier(e.target.value)}
                  className="w-full appearance-none rounded-md border border-slate-700 bg-slate-800 px-3 py-2 pr-8 text-sm text-white focus:border-cyan-500 focus:outline-none"
                >
                  <option value="">Select plan…</option>
                  {tenant.available_plans.map((p) => (
                    <option key={p.tier} value={p.tier}>{p.display_name}</option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
              </div>
              <button
                type="submit"
                disabled={assigningPlan || !planTier}
                className="w-full rounded-md bg-cyan-700 px-3 py-2 text-sm font-medium text-white hover:bg-cyan-600 disabled:opacity-50"
              >
                {assigningPlan ? "Assigning…" : "Assign plan"}
              </button>
              {planMsg && <p className="text-xs text-cyan-400">{planMsg}</p>}
            </form>
          </div>

          {/* Extend trial */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Extend trial</p>
            <form onSubmit={handleExtendTrial} className="flex gap-2">
              <input
                type="number"
                min="1"
                max="365"
                value={extendDays}
                onChange={(e) => setExtendDays(e.target.value)}
                className="w-20 rounded-md border border-slate-700 bg-slate-800 px-2 py-2 text-sm text-white focus:border-cyan-500 focus:outline-none"
              />
              <span className="self-center text-xs text-slate-400">days</span>
              <button
                type="submit"
                disabled={extendingTrial}
                className="flex-1 rounded-md bg-slate-700 px-3 py-2 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-50"
              >
                {extendingTrial ? "Extending…" : "Extend"}
              </button>
            </form>
            {extendMsg && <p className="text-xs text-cyan-400">{extendMsg}</p>}
          </div>
        </div>

        {/* Right column — domains, members, logs */}
        <div className="space-y-5 lg:col-span-2">

          {/* Domains */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Domains ({tenant.domain_count})
            </p>
            {tenant.domains.length === 0 ? (
              <p className="text-xs text-slate-600">No domains yet.</p>
            ) : (
              <div className="divide-y divide-slate-800">
                {tenant.domains.map((d) => (
                  <div key={d.id} className="flex items-center justify-between py-2 text-sm">
                    <span className="font-medium text-white">{d.name}</span>
                    <span className={`text-xs font-medium capitalize ${DOMAIN_STATUS_STYLES[d.status] ?? "text-slate-400"}`}>
                      {d.status}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Members */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Members ({tenant.member_count})
            </p>
            {tenant.members.length === 0 ? (
              <p className="text-xs text-slate-600">No members yet.</p>
            ) : (
              <div className="divide-y divide-slate-800">
                {tenant.members.map((m) => (
                  <div key={m.id} className="flex items-center gap-3 py-2">
                    <div className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-800 text-xs font-bold text-slate-400">
                      {(m.full_name || m.email).charAt(0).toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-white">{m.full_name || m.email}</p>
                      <p className="truncate text-xs text-slate-500">{m.email}</p>
                    </div>
                    <span className="text-xs font-medium capitalize text-slate-400">
                      {m.role.replace("_", " ")}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Recent audit logs */}
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Recent audit logs</p>
            {tenant.recent_logs.length === 0 ? (
              <p className="text-xs text-slate-600">No log entries yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-800">
                      <th className="pb-2 text-left font-semibold text-slate-500">Event</th>
                      <th className="pb-2 text-left font-semibold text-slate-500">Result</th>
                      <th className="pb-2 text-left font-semibold text-slate-500">Source</th>
                      <th className="pb-2 text-left font-semibold text-slate-500">Time</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50">
                    {tenant.recent_logs.map((log, i) => (
                      <tr key={i}>
                        <td className="py-1.5 font-medium text-slate-300 capitalize">
                          {log.event_type.replace(/_/g, " ")}
                        </td>
                        <td className={`py-1.5 ${log.result === "success" ? "text-emerald-400" : "text-red-400"}`}>
                          {log.result}
                        </td>
                        <td className="py-1.5 text-slate-500">{log.source}</td>
                        <td className="py-1.5 text-slate-600">
                          {new Date(log.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
