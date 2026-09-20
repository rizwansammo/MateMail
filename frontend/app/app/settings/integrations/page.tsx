"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiRequest } from "@/lib/api";

type Mailbox = { id: string; email: string; full_name: string; status: string };
type Permission = { key: string; label: string };
type Integration = {
  id: string;
  name: string;
  tenant_id: string;
  mailbox_email: string;
  permissions: Permission[];
  active: boolean;
};

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [mailboxId, setMailboxId] = useState("");
  const [useSignatures, setUseSignatures] = useState(true);
  const [secret, setSecret] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  async function load() {
    const [ir, mr] = await Promise.all([
      apiRequest("/api/integrations/"),
      apiRequest("/api/mailboxes/"),
    ]);
    if (ir.ok) setIntegrations(await ir.json());
    if (mr.ok) {
      const rows: Mailbox[] = await mr.json();
      const active = rows.filter((item) => item.status === "active");
      setMailboxes(active);
      setMailboxId((current) => current || active[0]?.id || "");
    }
  }

  useEffect(() => { void load(); }, []);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setError("");
    setSecret("");
    try {
      const res = await apiRequest("/api/integrations/", {
        method: "POST",
        body: JSON.stringify({
          name: "NetaMate SalesHub",
          mailbox_id: mailboxId,
          permissions: useSignatures
            ? ["send_email", "use_signatures"]
            : ["send_email"],
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(body.detail || JSON.stringify(body));
        return;
      }
      setSecret(body.integration_secret);
      setTenantId(body.tenant_id);
      await load();
    } finally {
      setCreating(false);
    }
  }

  async function revoke(id: string) {
    if (!window.confirm("Revoke this integration? Connected apps will stop working immediately.")) return;
    const res = await apiRequest("/api/integrations/" + id + "/", { method: "DELETE" });
    if (!res.ok) {
      setError("Unable to revoke the integration.");
      return;
    }
    await load();
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 p-6">
      <div>
        <Link href="/app/settings" className="text-xs font-semibold text-cyan-700 hover:underline">← Settings</Link>
        <h1 className="mt-2 text-xl font-semibold text-slate-900">Connected apps</h1>
        <p className="mt-1 text-sm text-slate-500">
          Create a secure connection for one exact mailbox. Mailbox passwords are never shared.
        </p>
      </div>

      <form onSubmit={create} className="space-y-4 border border-slate-200 bg-white p-5">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">NetaMate SalesHub</h2>
          <p className="mt-1 text-xs text-slate-500">
            SalesHub uses the Tenant ID and one-time secret to start a connection. A workspace admin must still approve it in MateMail.
          </p>
        </div>

        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-slate-600">Sales mailbox</span>
          <select className="w-full border border-slate-300 px-3 py-2 text-sm" value={mailboxId} onChange={(e) => setMailboxId(e.target.value)} required>
            {mailboxes.map((mailbox) => (
              <option key={mailbox.id} value={mailbox.id}>
                {mailbox.full_name ? mailbox.full_name + " — " : ""}{mailbox.email}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-slate-400">This integration is bound to this one mailbox.</p>
        </label>

        <div className="border border-slate-200 p-3 text-sm">
          <p className="font-semibold text-slate-800">Access</p>
          <label className="mt-2 flex items-center gap-2 text-slate-700">
            <input type="checkbox" checked readOnly />
            Send email from this mailbox
          </label>
          <label className="mt-2 flex items-center gap-2 text-slate-700">
            <input type="checkbox" checked={useSignatures} onChange={(e) => setUseSignatures(e.target.checked)} />
            Use this mailbox&apos;s signatures
          </label>
        </div>

        {error && <div className="text-sm text-red-600">{error}</div>}
        <button disabled={creating || !mailboxId} className="bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          {creating ? "Creating…" : "Create integration"}
        </button>
      </form>

      {secret && (
        <div className="border border-amber-300 bg-amber-50 p-5">
          <h2 className="font-semibold text-amber-950">Copy these now</h2>
          <p className="mt-1 text-xs text-amber-800">The integration secret is shown only once.</p>
          <div className="mt-4 space-y-3">
            <CopyRow label="Tenant ID" value={tenantId} />
            <CopyRow label="Integration Secret" value={secret} />
          </div>
        </div>
      )}

      <div className="border border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-4">
          <h2 className="text-sm font-semibold text-slate-900">Existing integrations</h2>
        </div>
        {integrations.length === 0 ? (
          <p className="p-4 text-sm text-slate-500">No integrations yet.</p>
        ) : integrations.map((item) => (
          <div key={item.id} className="border-b border-slate-100 p-4 last:border-0">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-semibold text-slate-900">{item.name}</p>
                <p className="mt-1 text-sm text-slate-600">{item.mailbox_email}</p>
                <p className="mt-2 text-xs text-slate-400">
                  {item.permissions.map((permission) => permission.label).join(" · ")}
                </p>
              </div>
              {item.active && (
                <button onClick={() => void revoke(item.id)} className="border border-red-200 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50">
                  Revoke
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-semibold text-amber-900">{label}</p>
      <div className="mt-1 flex gap-2">
        <input readOnly value={value} className="min-w-0 flex-1 border border-amber-300 bg-white px-3 py-2 font-mono text-xs" />
        <button type="button" onClick={() => void navigator.clipboard.writeText(value)} className="border border-amber-400 bg-white px-3 text-xs font-semibold text-amber-900">
          Copy
        </button>
      </div>
    </div>
  );
}
