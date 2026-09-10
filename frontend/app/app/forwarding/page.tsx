"use client";

import { useEffect, useState } from "react";
import { api, ApiError, apiRequest } from "@/lib/api";
import { Send, Plus, Trash2, CheckCircle2, AlertCircle, ToggleLeft, ToggleRight } from "lucide-react";
import Link from "next/link";

interface Mailbox {
  id: string;
  email: string;
}

interface ForwardingRule {
  id: string;
  source_mailbox: string;
  source_mailbox_email: string;
  destination_email: string;
  keep_copy: boolean;
  status: "active" | "paused" | "disabled";
  mail_service_ready: boolean;
  created_at: string;
}

const STATUS_STYLES: Record<string, string> = {
  active:   "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  paused:   "bg-amber-50   text-amber-700   ring-amber-600/20",
  disabled: "bg-slate-100  text-slate-500   ring-slate-400/20",
};

export default function ForwardingPage() {
  const [rules, setRules] = useState<ForwardingRule[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);

  // Form
  const [mailboxId, setMailboxId] = useState("");
  const [destEmail, setDestEmail] = useState("");
  const [keepCopy, setKeepCopy] = useState(true);
  const [addErrors, setAddErrors] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState(false);

  // Per-row
  const [toggling, setToggling] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  async function fetchAll() {
    setLoading(true);
    try {
      const [rRes, mRes] = await Promise.all([
        apiRequest("/api/forwarding/"),
        apiRequest("/api/mailboxes/"),
      ]);
      if (rRes.ok) setRules(await rRes.json());
      if (mRes.ok) {
        const m = await mRes.json();
        setMailboxes(m);
        if (m.length > 0 && !mailboxId) setMailboxId(m[0].id);
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchAll(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setAddErrors({});
    setAdding(true);
    try {
      await api.post("/api/forwarding/", {
        source_mailbox_id: mailboxId,
        destination_email: destEmail.trim(),
        keep_copy: keepCopy,
      });
      setDestEmail("");
      setKeepCopy(true);
      setAddOpen(false);
      await fetchAll();
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const b = JSON.parse(err.message);
          setAddErrors(typeof b === "object" ? b : { detail: err.message });
        } catch {
          setAddErrors({ detail: "Failed to create forwarding rule." });
        }
      }
    } finally {
      setAdding(false);
    }
  }

  async function toggleStatus(rule: ForwardingRule) {
    setToggling(rule.id);
    try {
      const newStatus = rule.status === "active" ? "paused" : "active";
      const res = await apiRequest(`/api/forwarding/${rule.id}/status/`, {
        method: "PATCH",
        body: JSON.stringify({ status: newStatus }),
      });
      if (res.ok) {
        const updated = await res.json();
        setRules((prev) => prev.map((r) => (r.id === rule.id ? updated : r)));
      }
    } finally {
      setToggling(null);
    }
  }

  async function handleDelete(rule: ForwardingRule) {
    setDeleting(rule.id);
    try {
      await apiRequest(`/api/forwarding/${rule.id}/`, { method: "DELETE" });
      setRules((prev) => prev.filter((r) => r.id !== rule.id));
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Send className="h-5 w-5 text-slate-400" />
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Forwarding</h1>
            <p className="text-sm text-slate-500">Forward incoming mail from a mailbox to another address.</p>
          </div>
        </div>
        <button
          onClick={() => setAddOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          <Plus className="h-4 w-4" />
          Add rule
        </button>
      </div>

      {/* Add form */}
      {addOpen && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-4">
          <p className="text-sm font-medium text-slate-800">New forwarding rule</p>
          {addErrors.detail && <p className="text-sm text-red-600">{addErrors.detail}</p>}
          <form onSubmit={handleAdd} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Source mailbox</label>
              {mailboxes.length === 0 ? (
                <p className="text-xs text-amber-600">No mailboxes yet. <Link href="/app/mailboxes" className="underline">Create one first.</Link></p>
              ) : (
                <select
                  value={mailboxId}
                  onChange={(e) => setMailboxId(e.target.value)}
                  className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                >
                  {mailboxes.map((m) => <option key={m.id} value={m.id}>{m.email}</option>)}
                </select>
              )}
              {addErrors.source_mailbox_id && <p className="mt-1 text-xs text-red-600">{addErrors.source_mailbox_id}</p>}
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Forward to</label>
              <input
                type="email"
                placeholder="you@gmail.com"
                value={destEmail}
                onChange={(e) => setDestEmail(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              />
              {addErrors.destination_email && <p className="mt-1 text-xs text-red-600">{addErrors.destination_email}</p>}
            </div>

            <label className="flex items-center gap-2.5 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={keepCopy}
                onChange={(e) => setKeepCopy(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-cyan-600 focus:ring-cyan-500"
              />
              <span className="text-sm text-slate-700">Keep a copy in the original mailbox</span>
            </label>

            <div className="flex gap-3 pt-1">
              <button
                type="submit"
                disabled={adding || !mailboxId || !destEmail.trim()}
                className="rounded-md bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
              >
                {adding ? "Creating…" : "Create rule"}
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

      {/* No mailboxes */}
      {!loading && mailboxes.length === 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          Create a mailbox before setting up forwarding.{" "}
          <Link href="/app/mailboxes" className="font-medium underline">Add a mailbox →</Link>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">Loading forwarding rules…</div>
      ) : rules.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-16 text-center">
          <Send className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm text-slate-500">No forwarding rules yet.</p>
          {mailboxes.length > 0 && (
            <button onClick={() => setAddOpen(true)} className="mt-3 text-sm font-medium text-cyan-600 hover:text-cyan-700">
              Create your first rule →
            </button>
          )}
        </div>
      ) : (
        <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {rules.map((rule) => (
            <div key={rule.id} className="flex items-center gap-4 px-5 py-4">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">
                  {rule.source_mailbox_email}
                  <span className="mx-1.5 text-slate-300">→</span>
                  {rule.destination_email}
                </p>
                <p className="text-xs text-slate-400">
                  {rule.keep_copy ? "Copy kept in original mailbox" : "No copy kept"}
                </p>
              </div>

              {rule.mail_service_ready ? (
                <span title="Provisioned" className="text-emerald-500"><CheckCircle2 className="h-4 w-4" /></span>
              ) : (
                <span title="Not provisioned" className="text-amber-400"><AlertCircle className="h-4 w-4" /></span>
              )}

              <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[rule.status]}`}>
                {rule.status}
              </span>

              {/* Toggle active/pause */}
              <button
                onClick={() => toggleStatus(rule)}
                disabled={toggling === rule.id}
                title={rule.status === "active" ? "Pause" : "Activate"}
                className="text-slate-400 hover:text-slate-700 disabled:opacity-40"
              >
                {rule.status === "active"
                  ? <ToggleRight className="h-5 w-5 text-emerald-500" />
                  : <ToggleLeft className="h-5 w-5" />}
              </button>

              {/* Delete */}
              <button
                onClick={() => handleDelete(rule)}
                disabled={deleting === rule.id}
                title="Delete rule"
                className="text-slate-300 hover:text-red-500 disabled:opacity-40"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Info note */}
      {rules.length > 0 && (
        <p className="text-xs text-slate-400">
          Forwarding is handled at the mail server level. Changes take effect for new incoming messages.
        </p>
      )}
    </div>
  );
}
