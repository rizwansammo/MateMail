"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiRequest } from "@/lib/api";
import { Building2, Search, ChevronRight, Loader2 } from "lucide-react";

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

const STATUS_STYLES: Record<string, string> = {
  trial:     "bg-cyan-900/50 text-cyan-300 ring-cyan-700/50",
  active:    "bg-emerald-900/50 text-emerald-300 ring-emerald-700/50",
  past_due:  "bg-amber-900/50 text-amber-300 ring-amber-700/50",
  suspended: "bg-red-900/50 text-red-300 ring-red-700/50",
  cancelled: "bg-slate-800 text-slate-500 ring-slate-700/50",
};

const STATUS_OPTIONS = ["all", "trial", "active", "past_due", "suspended", "cancelled"];

export default function AdminTenantsPage() {
  const [tenants, setTenants] = useState<TenantRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (statusFilter !== "all") params.set("status", statusFilter);
    if (debouncedSearch) params.set("search", debouncedSearch);
    apiRequest(`/api/platform/tenants/?${params}`)
      .then((r) => r.ok ? r.json() : [])
      .then(setTenants)
      .finally(() => setLoading(false));
  }, [statusFilter, debouncedSearch]);

  return (
    <div className="space-y-5 p-6">
      <div className="flex items-center gap-3">
        <Building2 className="h-5 w-5 text-slate-400" />
        <h1 className="text-xl font-semibold text-white">Tenants</h1>
        <span className="ml-2 rounded-full bg-slate-800 px-2.5 py-0.5 text-xs font-semibold text-slate-400">
          {tenants.length}
        </span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            placeholder="Search name or email…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-64 rounded-md border border-slate-700 bg-slate-800 pl-8 pr-3 py-2 text-sm text-white placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          />
        </div>
        <div className="flex gap-1">
          {STATUS_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold capitalize transition ${
                statusFilter === s
                  ? "bg-cyan-600 text-white"
                  : "border border-slate-700 text-slate-400 hover:border-slate-500 hover:text-white"
              }`}
            >
              {s.replace("_", " ")}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
        </div>
      ) : tenants.length === 0 ? (
        <div className="py-16 text-center text-sm text-slate-500">No tenants found.</div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Workspace</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Owner</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Plan</th>
                <th className="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider text-slate-500">Domains</th>
                <th className="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider text-slate-500">Mailboxes</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Created</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-950">
              {tenants.map((t) => (
                <tr key={t.id} className="hover:bg-slate-900/50">
                  <td className="px-4 py-3">
                    <p className="font-medium text-white">{t.name}</p>
                    <p className="text-xs text-slate-500">{t.slug}</p>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{t.owner_email}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold capitalize ring-1 ring-inset ${STATUS_STYLES[t.status] ?? STATUS_STYLES.cancelled}`}>
                      {t.status.replace("_", " ")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-400 capitalize">
                    {t.plan_tier?.replace("_", " ") ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-center font-mono text-slate-300">{t.domain_count}</td>
                  <td className="px-4 py-3 text-center font-mono text-slate-300">{t.mailbox_count}</td>
                  <td className="px-4 py-3 text-slate-500 text-xs">
                    {new Date(t.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/tenants/${t.id}`}
                      className="flex items-center gap-1 text-xs font-medium text-cyan-400 hover:text-cyan-300"
                    >
                      View <ChevronRight className="h-3 w-3" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
