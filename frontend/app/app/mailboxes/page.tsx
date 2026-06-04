"use client";

import { useEffect, useState } from "react";
import { Inbox, CheckCircle2, AlertCircle, Plus, RefreshCw, ChevronRight } from "lucide-react";
import Link from "next/link";
import { api, ApiError, apiRequest } from "@/lib/api";

interface Domain {
  id: string;
  domain: string;
}

interface Mailbox {
  id: string;
  email: string;
  full_name: string;
  local_part: string;
  domain: string;
  domain_name: string;
  status: "active" | "disabled" | "suspended";
  quota_mb: number;
  mail_engine_provisioned: boolean;
  mail_engine_error: string;
  last_login: string | null;
  created_at: string;
}

const STATUS_STYLES: Record<string, string> = {
  active:    "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  disabled:  "bg-slate-100  text-slate-500   ring-slate-400/20",
  suspended: "bg-red-50     text-red-700     ring-red-600/20",
};

export default function MailboxesPage() {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);

  // Add form state
  const [localPart, setLocalPart] = useState("");
  const [domainId, setDomainId] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [quotaMb, setQuotaMb] = useState(10240);
  const [addErrors, setAddErrors] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState(false);

  async function fetchAll() {
    setLoading(true);
    try {
      const [mbRes, dmRes] = await Promise.all([
        apiRequest("/api/mailboxes/"),
        apiRequest("/api/domains/"),
      ]);
      if (mbRes.ok) setMailboxes(await mbRes.json());
      if (dmRes.ok) {
        const d = await dmRes.json();
        setDomains(d);
        if (d.length > 0 && !domainId) setDomainId(d[0].id);
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchAll(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedDomain = domains.find((d) => d.id === domainId);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setAddErrors({});
    setAdding(true);
    try {
      await api.post("/api/mailboxes/", {
        local_part: localPart.trim().toLowerCase(),
        domain_id: domainId,
        full_name: fullName.trim(),
        quota_mb: quotaMb,
        password,
      });
      setLocalPart(""); setFullName(""); setPassword(""); setQuotaMb(10240);
      setAddOpen(false);
      await fetchAll();
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const body = JSON.parse(err.message);
          setAddErrors(typeof body === "object" ? body : { detail: err.message });
        } catch {
          setAddErrors({ detail: "Failed to create mailbox." });
        }
      }
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Mailboxes</h1>
          <p className="mt-0.5 text-sm text-slate-500">Email accounts for this workspace.</p>
        </div>
        <button
          onClick={() => setAddOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          <Plus className="h-4 w-4" />
          Add mailbox
        </button>
      </div>

      {/* Add mailbox form */}
      {addOpen && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-4">
          <p className="text-sm font-medium text-slate-800">New mailbox</p>

          {addErrors.detail && (
            <p className="text-sm text-red-600">{addErrors.detail}</p>
          )}

          <form onSubmit={handleAdd} className="space-y-4">
            {/* Email address */}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Email address</label>
              <div className="flex">
                <input
                  type="text"
                  placeholder="you"
                  value={localPart}
                  onChange={(e) => setLocalPart(e.target.value)}
                  className="min-w-0 flex-1 rounded-l-md border border-r-0 border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                />
                <span className="flex items-center rounded-r-md border border-slate-300 bg-slate-50 px-3 text-sm text-slate-500">
                  @
                </span>
                <select
                  value={domainId}
                  onChange={(e) => setDomainId(e.target.value)}
                  className="ml-1 rounded-md border border-slate-300 px-2 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                >
                  {domains.map((d) => (
                    <option key={d.id} value={d.id}>{d.domain}</option>
                  ))}
                </select>
              </div>
              {addErrors.local_part && <p className="mt-1 text-xs text-red-600">{addErrors.local_part}</p>}
            </div>

            {/* Display name */}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Display name</label>
              <input
                type="text"
                placeholder="Jane Smith"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              />
            </div>

            {/* Password */}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Password</label>
              <input
                type="password"
                placeholder="Min 10 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              />
            </div>

            {/* Quota */}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">
                Quota — {Math.round(quotaMb / 1024)} GB
              </label>
              <input
                type="range"
                min={1024}
                max={51200}
                step={1024}
                value={quotaMb}
                onChange={(e) => setQuotaMb(Number(e.target.value))}
                className="w-full"
              />
            </div>

            <div className="flex gap-3 pt-1">
              <button
                type="submit"
                disabled={adding || !localPart.trim() || !fullName.trim() || password.length < 10 || !domainId}
                className="rounded-md bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
              >
                {adding ? "Creating…" : "Create mailbox"}
              </button>
              <button
                type="button"
                onClick={() => { setAddOpen(false); setAddErrors({}); }}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* No domains warning */}
      {!loading && domains.length === 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          You need to add a domain before creating mailboxes.{" "}
          <Link href="/app/domains" className="font-medium underline">Add a domain →</Link>
        </div>
      )}

      {/* Mailbox list */}
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">Loading mailboxes…</div>
      ) : mailboxes.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-16 text-center">
          <Inbox className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm text-slate-500">No mailboxes yet.</p>
          {domains.length > 0 && (
            <button
              onClick={() => setAddOpen(true)}
              className="mt-3 text-sm font-medium text-cyan-600 hover:text-cyan-700"
            >
              Create your first mailbox →
            </button>
          )}
        </div>
      ) : (
        <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {mailboxes.map((mb) => (
            <Link
              key={mb.id}
              href={`/app/mailboxes/${mb.id}`}
              className="flex items-center gap-4 px-5 py-4 hover:bg-slate-50 transition-colors"
            >
              <Inbox className="h-5 w-5 shrink-0 text-slate-400" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">{mb.email}</p>
                <p className="truncate text-xs text-slate-400">{mb.full_name} · {Math.round(mb.quota_mb / 1024)} GB quota</p>
              </div>

              {mb.mail_engine_provisioned ? (
                <span title="Provisioned in mail engine" className="text-emerald-500">
                  <CheckCircle2 className="h-4 w-4" />
                </span>
              ) : (
                <span
                  title={mb.mail_engine_error || "Not yet provisioned in mail engine"}
                  className="text-amber-400"
                >
                  <AlertCircle className="h-4 w-4" />
                </span>
              )}

              <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[mb.status] ?? STATUS_STYLES.active}`}>
                {mb.status}
              </span>
              <ChevronRight className="h-4 w-4 text-slate-300 shrink-0" />
            </Link>
          ))}
        </div>
      )}

      {/* Refresh */}
      {!loading && mailboxes.length > 0 && (
        <div className="text-right">
          <button
            onClick={fetchAll}
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
