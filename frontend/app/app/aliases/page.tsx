"use client";

import { useEffect, useState } from "react";
import { api, ApiError, apiRequest } from "@/lib/api";
import { Layers, Plus, Trash2, CheckCircle2, AlertCircle, ToggleLeft, ToggleRight } from "lucide-react";
import Link from "next/link";

interface Domain {
  id: string;
  domain: string;
}

interface Mailbox {
  id: string;
  email: string;
}

interface Alias {
  id: string;
  source_address: string;
  domain: string;
  domain_name: string;
  destination_mailbox: string | null;
  destination_address: string;
  destination_email: string;
  status: "active" | "disabled";
  mail_service_ready: boolean;
  created_at: string;
}

const STATUS_STYLES: Record<string, string> = {
  active:   "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  disabled: "bg-slate-100  text-slate-500   ring-slate-400/20",
};

type DestType = "mailbox" | "external";

export default function AliasesPage() {
  const [aliases, setAliases] = useState<Alias[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);

  // Form state
  const [localPart, setLocalPart] = useState("");
  const [domainId, setDomainId] = useState("");
  const [destType, setDestType] = useState<DestType>("mailbox");
  const [destMailboxId, setDestMailboxId] = useState("");
  const [destAddress, setDestAddress] = useState("");
  const [addErrors, setAddErrors] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState(false);

  // Per-row state
  const [toggling, setToggling] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  async function fetchAll() {
    setLoading(true);
    try {
      const [aRes, dRes, mRes] = await Promise.all([
        apiRequest("/api/aliases/"),
        apiRequest("/api/domains/"),
        apiRequest("/api/mailboxes/"),
      ]);
      if (aRes.ok) setAliases(await aRes.json());
      if (dRes.ok) {
        const d = await dRes.json();
        setDomains(d);
        if (d.length > 0 && !domainId) { setDomainId(d[0].id); }
      }
      if (mRes.ok) {
        const m = await mRes.json();
        setMailboxes(m);
        if (m.length > 0 && !destMailboxId) setDestMailboxId(m[0].id);
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
      const body: Record<string, unknown> = {
        source_local_part: localPart.trim().toLowerCase(),
        domain_id: domainId,
      };
      if (destType === "mailbox") {
        body.destination_mailbox_id = destMailboxId;
      } else {
        body.destination_address = destAddress.trim();
      }
      await api.post("/api/aliases/", body);
      setLocalPart(""); setDestAddress("");
      setAddOpen(false);
      await fetchAll();
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const b = JSON.parse(err.message);
          setAddErrors(typeof b === "object" ? b : { detail: err.message });
        } catch {
          setAddErrors({ detail: "Failed to create alias." });
        }
      }
    } finally {
      setAdding(false);
    }
  }

  async function toggleStatus(alias: Alias) {
    setToggling(alias.id);
    try {
      const newStatus = alias.status === "active" ? "disabled" : "active";
      const res = await apiRequest(`/api/aliases/${alias.id}/status/`, {
        method: "PATCH",
        body: JSON.stringify({ status: newStatus }),
      });
      if (res.ok) {
        const updated = await res.json();
        setAliases((prev) => prev.map((a) => (a.id === alias.id ? updated : a)));
      }
    } finally {
      setToggling(null);
    }
  }

  async function handleDelete(alias: Alias) {
    setDeleting(alias.id);
    try {
      await apiRequest(`/api/aliases/${alias.id}/`, { method: "DELETE" });
      setAliases((prev) => prev.filter((a) => a.id !== alias.id));
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Layers className="h-5 w-5 text-slate-400" />
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Aliases</h1>
            <p className="text-sm text-slate-500">Email addresses that redirect to mailboxes or external addresses.</p>
          </div>
        </div>
        <button
          onClick={() => setAddOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          <Plus className="h-4 w-4" />
          Add alias
        </button>
      </div>

      {/* Add form */}
      {addOpen && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-4">
          <p className="text-sm font-medium text-slate-800">New alias</p>
          {addErrors.detail && <p className="text-sm text-red-600">{addErrors.detail}</p>}
          <form onSubmit={handleAdd} className="space-y-4">
            {/* Source address */}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Alias address</label>
              <div className="flex">
                <input
                  type="text"
                  placeholder="sales"
                  value={localPart}
                  onChange={(e) => setLocalPart(e.target.value)}
                  className="min-w-0 flex-1 rounded-l-md border border-r-0 border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                />
                <span className="flex items-center border border-r-0 border-slate-300 bg-slate-50 px-2 text-sm text-slate-500">@</span>
                <select
                  value={domainId}
                  onChange={(e) => setDomainId(e.target.value)}
                  className="rounded-r-md border border-slate-300 px-2 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                >
                  {domains.map((d) => <option key={d.id} value={d.id}>{d.domain}</option>)}
                </select>
              </div>
              {addErrors.source_local_part && <p className="mt-1 text-xs text-red-600">{addErrors.source_local_part}</p>}
            </div>

            {/* Destination type */}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Deliver to</label>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => setDestType("mailbox")}
                  className={`flex-1 rounded-md border px-3 py-2 text-sm font-medium transition ${destType === "mailbox" ? "border-cyan-500 bg-cyan-50 text-cyan-700" : "border-slate-300 text-slate-600 hover:bg-slate-50"}`}
                >
                  Internal mailbox
                </button>
                <button
                  type="button"
                  onClick={() => setDestType("external")}
                  className={`flex-1 rounded-md border px-3 py-2 text-sm font-medium transition ${destType === "external" ? "border-cyan-500 bg-cyan-50 text-cyan-700" : "border-slate-300 text-slate-600 hover:bg-slate-50"}`}
                >
                  External address
                </button>
              </div>
            </div>

            {destType === "mailbox" ? (
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-700">Destination mailbox</label>
                {mailboxes.length === 0 ? (
                  <p className="text-xs text-amber-600">No mailboxes yet. <Link href="/app/mailboxes" className="underline">Create one first.</Link></p>
                ) : (
                  <select
                    value={destMailboxId}
                    onChange={(e) => setDestMailboxId(e.target.value)}
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                  >
                    {mailboxes.map((m) => <option key={m.id} value={m.id}>{m.email}</option>)}
                  </select>
                )}
              </div>
            ) : (
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-700">Destination email</label>
                <input
                  type="email"
                  placeholder="someone@gmail.com"
                  value={destAddress}
                  onChange={(e) => setDestAddress(e.target.value)}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                />
                {addErrors.destination_address && <p className="mt-1 text-xs text-red-600">{addErrors.destination_address}</p>}
              </div>
            )}

            <div className="flex gap-3 pt-1">
              <button
                type="submit"
                disabled={adding || !localPart.trim() || !domainId || (destType === "external" && !destAddress.trim()) || (destType === "mailbox" && !destMailboxId)}
                className="rounded-md bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
              >
                {adding ? "Creating…" : "Create alias"}
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

      {/* No domains */}
      {!loading && domains.length === 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          Add a domain before creating aliases.{" "}
          <Link href="/app/domains" className="font-medium underline">Add a domain →</Link>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">Loading aliases…</div>
      ) : aliases.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-16 text-center">
          <Layers className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm text-slate-500">No aliases yet.</p>
          {domains.length > 0 && (
            <button onClick={() => setAddOpen(true)} className="mt-3 text-sm font-medium text-cyan-600 hover:text-cyan-700">
              Create your first alias →
            </button>
          )}
        </div>
      ) : (
        <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {aliases.map((alias) => (
            <div key={alias.id} className="flex items-center gap-4 px-5 py-4">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">{alias.source_address}</p>
                <p className="truncate text-xs text-slate-400">→ {alias.destination_email}</p>
              </div>

              {alias.mail_service_ready ? (
                <span title="Provisioned" className="text-emerald-500"><CheckCircle2 className="h-4 w-4" /></span>
              ) : (
                <span title="Not provisioned" className="text-amber-400"><AlertCircle className="h-4 w-4" /></span>
              )}

              <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[alias.status]}`}>
                {alias.status}
              </span>

              {/* Toggle */}
              <button
                onClick={() => toggleStatus(alias)}
                disabled={toggling === alias.id}
                title={alias.status === "active" ? "Disable" : "Enable"}
                className="text-slate-400 hover:text-slate-700 disabled:opacity-40"
              >
                {alias.status === "active"
                  ? <ToggleRight className="h-5 w-5 text-emerald-500" />
                  : <ToggleLeft className="h-5 w-5" />}
              </button>

              {/* Delete */}
              <button
                onClick={() => handleDelete(alias)}
                disabled={deleting === alias.id}
                title="Delete alias"
                className="text-slate-300 hover:text-red-500 disabled:opacity-40"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
