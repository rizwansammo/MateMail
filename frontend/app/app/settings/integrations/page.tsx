"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiRequest } from "@/lib/api";

type Mailbox = { id: string; email: string; full_name: string; status: string };
type Permission = { key: string; label: string };
type Integration = {
  id: string;
  name: string;
  purpose: string;
  purpose_label: string;
  tenant_id: string;
  mailbox_email: string;
  permissions: Permission[];
  active: boolean;
};

const PURPOSES = [
  { value: "sales_crm", label: "Sales / CRM" },
  { value: "helpdesk", label: "Helpdesk / Ticketing" },
  { value: "custom", label: "Custom application" },
];

const SCOPES = [
  {
    key: "mailbox.read",
    label: "See mailbox details",
    description: "See the approved mailbox address and display name.",
  },
  {
    key: "mail.send",
    label: "Send email",
    description: "Send email only from this approved mailbox.",
  },
  {
    key: "mail.read",
    label: "Read email",
    description: "Read messages and folders in this mailbox.",
  },
  {
    key: "mail.modify",
    label: "Update read status",
    description: "Mark messages as read or unread. Does not allow deletion.",
  },
  {
    key: "signatures.read",
    label: "Use signatures",
    description: "See and use signatures saved for this mailbox.",
  },
];

const PRESETS: Record<string, string[]> = {
  sales_crm: ["mailbox.read", "mail.send", "signatures.read"],
  helpdesk: ["mailbox.read", "mail.read", "mail.modify", "mail.send"],
  custom: ["mailbox.read"],
};

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [mailboxId, setMailboxId] = useState("");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("sales_crm");
  const [permissions, setPermissions] = useState<string[]>(PRESETS.sales_crm);
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

  function choosePurpose(value: string) {
    setPurpose(value);
    setPermissions(PRESETS[value] ?? PRESETS.custom);
  }

  function togglePermission(key: string) {
    setPermissions((current) =>
      current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key]
    );
  }

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setError("");
    setSecret("");
    try {
      const res = await apiRequest("/api/integrations/", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          purpose,
          mailbox_id: mailboxId,
          permissions,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(body.detail || JSON.stringify(body));
        return;
      }
      setSecret(body.integration_secret);
      setTenantId(body.tenant_id);
      setName("");
      await load();
    } finally {
      setCreating(false);
    }
  }

  async function revoke(id: string) {
    if (!window.confirm("Revoke this integration? The connected app will lose access immediately.")) return;
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
          Connect CRM, helpdesk, ticketing or other trusted applications to one exact mailbox without sharing its password.
        </p>
      </div>

      <form onSubmit={create} className="space-y-4 border border-slate-200 bg-white p-5">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Create connected app</h2>
          <p className="mt-1 text-xs text-slate-500">
            MateMail creates a Tenant ID and one-time Integration Secret. The external app must still open MateMail and receive administrator approval.
          </p>
        </div>

        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-slate-600">Application name</span>
          <input
            className="w-full border border-slate-300 px-3 py-2 text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Example: NetaMate SalesHub, MateDesk, Acme Helpdesk"
            maxLength={100}
            required
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-slate-600">Application purpose</span>
          <select
            className="w-full border border-slate-300 px-3 py-2 text-sm"
            value={purpose}
            onChange={(e) => choosePurpose(e.target.value)}
          >
            {PURPOSES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          <p className="mt-1 text-xs text-slate-400">Purpose only chooses a safe starting set of permissions. You can adjust them below.</p>
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-slate-600">Mailbox</span>
          <select className="w-full border border-slate-300 px-3 py-2 text-sm" value={mailboxId} onChange={(e) => setMailboxId(e.target.value)} required>
            {mailboxes.map((mailbox) => (
              <option key={mailbox.id} value={mailbox.id}>
                {mailbox.full_name ? mailbox.full_name + " — " : ""}{mailbox.email}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-slate-400">This connected app can never switch to another mailbox with the same credential.</p>
        </label>

        <div className="border border-slate-200 p-3 text-sm">
          <p className="font-semibold text-slate-800">Access</p>
          <div className="mt-2 space-y-3">
            {SCOPES.map((scope) => (
              <label key={scope.key} className="flex items-start gap-2 text-slate-700">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={permissions.includes(scope.key)}
                  onChange={() => togglePermission(scope.key)}
                />
                <span>
                  <span className="block font-medium">{scope.label}</span>
                  <span className="block text-xs text-slate-400">{scope.description}</span>
                </span>
              </label>
            ))}
          </div>
          <p className="mt-3 text-xs text-slate-400">
            Connected apps never receive domain management, mailbox password, tenant administration, delete-message or PostBox master access.
          </p>
        </div>

        {error && <div className="text-sm text-red-600">{error}</div>}
        <button disabled={creating || !mailboxId || !name.trim() || permissions.length === 0} className="bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          {creating ? "Creating…" : "Create connected app"}
        </button>
      </form>

      {secret && (
        <div className="border border-amber-300 bg-amber-50 p-5">
          <h2 className="font-semibold text-amber-950">Copy these now</h2>
          <p className="mt-1 text-xs text-amber-800">The Integration Secret is shown only once. It starts authorization but does not bypass MateMail approval or 2FA.</p>
          <div className="mt-4 space-y-3">
            <CopyRow label="Tenant ID" value={tenantId} />
            <CopyRow label="Integration Secret" value={secret} />
          </div>
        </div>
      )}

      <div className="border border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-4">
          <h2 className="text-sm font-semibold text-slate-900">Existing connected apps</h2>
        </div>
        {integrations.length === 0 ? (
          <p className="p-4 text-sm text-slate-500">No connected apps yet.</p>
        ) : integrations.map((item) => (
          <div key={item.id} className="border-b border-slate-100 p-4 last:border-0">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-semibold text-slate-900">{item.name}</p>
                <p className="mt-0.5 text-xs text-slate-400">{item.purpose_label}</p>
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
