"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, Globe, ChevronRight, RefreshCw } from "lucide-react";
import { apiRequest } from "@/lib/api";

interface Domain {
  id: string;
  domain: string;
  status: "pending" | "active" | "warning" | "failed" | "paused";
  dns_health_score: number;
  dkim_selector: string;
  mail_service_ready: boolean;
  added_at: string;
  verified_at: string | null;
}

const STATUS_STYLES: Record<string, string> = {
  active: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  pending: "bg-amber-50 text-amber-700 ring-amber-600/20",
  warning: "bg-orange-50 text-orange-700 ring-orange-600/20",
  failed: "bg-red-50 text-red-700 ring-red-600/20",
  paused: "bg-slate-100 text-slate-600 ring-slate-500/20",
};

function HealthBar({ score }: { score: number }) {
  const color =
    score >= 75 ? "bg-emerald-500" : score >= 50 ? "bg-amber-500" : score > 0 ? "bg-orange-500" : "bg-slate-200";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className="text-xs text-slate-500 tabular-nums">{score}/100</span>
    </div>
  );
}

export default function DomainsPage() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [newDomain, setNewDomain] = useState("");
  const [addError, setAddError] = useState("");
  const [adding, setAdding] = useState(false);

  async function fetchDomains() {
    setLoading(true);
    try {
      const res = await apiRequest("/api/domains/");
      if (res.ok) setDomains(await res.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchDomains(); }, []);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setAddError("");
    setAdding(true);
    try {
      const res = await apiRequest("/api/domains/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: newDomain.trim() }),
      });
      if (res.ok) {
        setNewDomain("");
        setAddOpen(false);
        await fetchDomains();
      } else {
        const data = await res.json();
        setAddError(data.domain?.[0] ?? data.detail ?? "Failed to add domain.");
      }
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Domains</h1>
          <p className="mt-0.5 text-sm text-slate-500">Manage email domains for this workspace.</p>
        </div>
        <button
          onClick={() => setAddOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          <Plus className="h-4 w-4" />
          Add domain
        </button>
      </div>

      {/* Add domain form */}
      {addOpen && (
        <div className="rounded-lg border border-slate-200 bg-white p-5">
          <p className="mb-3 text-sm font-medium text-slate-800">Add a new domain</p>
          <form onSubmit={handleAdd} className="flex items-start gap-3">
            <div className="flex-1">
              <input
                type="text"
                placeholder="example.com"
                value={newDomain}
                onChange={(e) => setNewDomain(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                autoFocus
              />
              {addError && <p className="mt-1 text-xs text-red-600">{addError}</p>}
            </div>
            <button
              type="submit"
              disabled={adding || !newDomain.trim()}
              className="rounded-md bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
            >
              {adding ? "Adding…" : "Add"}
            </button>
            <button
              type="button"
              onClick={() => { setAddOpen(false); setAddError(""); setNewDomain(""); }}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
            >
              Cancel
            </button>
          </form>
        </div>
      )}

      {/* Domain list */}
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">Loading domains…</div>
      ) : domains.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-16 text-center">
          <Globe className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm text-slate-500">No domains yet.</p>
          <button
            onClick={() => setAddOpen(true)}
            className="mt-3 text-sm font-medium text-cyan-600 hover:text-cyan-700"
          >
            Add your first domain →
          </button>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {domains.map((d) => (
            <Link
              key={d.id}
              href={`/app/domains/${d.id}`}
              className="flex items-center gap-4 px-5 py-4 hover:bg-slate-50 transition-colors"
            >
              <Globe className="h-5 w-5 shrink-0 text-slate-400" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">{d.domain}</p>
                <div className="mt-1">
                  <HealthBar score={d.dns_health_score} />
                </div>
              </div>
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[d.status] ?? STATUS_STYLES.pending}`}
              >
                {d.status}
              </span>
              <ChevronRight className="h-4 w-4 shrink-0 text-slate-300" />
            </Link>
          ))}
        </div>
      )}

      {/* Refresh */}
      {!loading && domains.length > 0 && (
        <div className="text-right">
          <button
            onClick={fetchDomains}
            className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-600"
          >
            <RefreshCw className="h-3 w-3" />
            Refresh
          </button>
        </div>
      )}
    </div>
  );
}
