"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import {
  Key,
  Plus,
  Trash2,
  Copy,
  CheckCircle2,
  Eye,
  Pencil,
  ShieldCheck,
} from "lucide-react";

interface APIKey {
  id: string;
  name: string;
  key_prefix: string;
  display: string;
  scopes: string[];
  is_read_only: boolean;
  created_by_email: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  is_active: boolean;
  key?: string; // only present on creation
}

/**
 * The scope model, mirroring apps/security/scopes.py.
 *
 * `read` is not offered as a choice: every key holds it, and presenting it as
 * something to tick implies it could be withheld.
 */
const WRITE_SCOPES: { value: string; label: string; detail: string }[] = [
  {
    value: "domains:write",
    label: "Manage domains",
    detail: "Add and remove domains, run DNS and ownership checks",
  },
  {
    value: "mailboxes:write",
    label: "Manage mailboxes",
    detail: "Create, update and delete mailboxes and their passwords",
  },
  {
    value: "routing:write",
    label: "Manage aliases and forwarding",
    detail: "Create and remove aliases and forwarding rules",
  },
  {
    value: "admin",
    label: "Workspace administration",
    detail: "Team, API keys, billing, backups, queue and quarantine",
  },
];

const SCOPE_LABELS: Record<string, string> = {
  read: "Read",
  ...Object.fromEntries(WRITE_SCOPES.map((s) => [s.value, s.label])),
};

function ScopeBadges({ scopes }: { scopes: string[] }) {
  const writes = scopes.filter((s) => s !== "read");
  if (writes.length === 0) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200">
        <ShieldCheck className="h-3 w-3" />
        Read only
      </span>
    );
  }
  return (
    <span className="flex flex-wrap gap-1">
      {writes.map((s) => (
        <span
          key={s}
          className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-inset ring-amber-200"
        >
          {SCOPE_LABELS[s] ?? s}
        </span>
      ))}
    </span>
  );
}

function ScopePicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  function toggle(value: string) {
    onChange(
      selected.includes(value)
        ? selected.filter((s) => s !== value)
        : [...selected, value]
    );
  }
  return (
    <fieldset className="space-y-2">
      <legend className="mb-1 text-xs font-medium text-slate-700">
        Permissions
      </legend>
      <p className="mb-2 text-xs text-slate-500">
        Every key can read this workspace. Grant write access only where the
        integration needs it — a key is not tied to your own permissions.
      </p>
      {WRITE_SCOPES.map((scope) => (
        <label
          key={scope.value}
          className="flex cursor-pointer items-start gap-2.5 rounded-md border border-slate-200 p-2.5 hover:bg-slate-50"
        >
          <input
            type="checkbox"
            checked={selected.includes(scope.value)}
            onChange={() => toggle(scope.value)}
            className="mt-0.5 h-4 w-4 rounded border-slate-300 text-cyan-600 focus:ring-cyan-500"
          />
          <span className="min-w-0">
            <span className="block text-sm font-medium text-slate-800">
              {scope.label}
            </span>
            <span className="block text-xs text-slate-500">{scope.detail}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

export default function APIKeysPage() {
  const [keys, setKeys] = useState<APIKey[]>([]);
  const [loading, setLoading] = useState(true);

  // Create form
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newScopes, setNewScopes] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  // Newly created key — shown once
  const [newKey, setNewKey] = useState<APIKey | null>(null);
  const [copied, setCopied] = useState(false);

  // Scope editing
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editScopes, setEditScopes] = useState<string[]>([]);
  const [savingScopes, setSavingScopes] = useState(false);
  const [editError, setEditError] = useState("");

  // Revoke state
  const [revoking, setRevoking] = useState<string | null>(null);

  async function fetchKeys() {
    setLoading(true);
    try {
      const res = await apiRequest("/api/teams/apikeys/");
      if (res.ok) setKeys(await res.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchKeys(); }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreateError("");
    setCreating(true);
    try {
      const res = await apiRequest("/api/teams/apikeys/", {
        method: "POST",
        body: JSON.stringify({
          name: newName.trim(),
          scopes: ["read", ...newScopes],
        }),
      });
      if (res.ok) {
        const created: APIKey = await res.json();
        setNewKey(created);
        setNewName("");
        setNewScopes([]);
        setCreateOpen(false);
        await fetchKeys();
      } else {
        const body = await res.json().catch(() => ({}));
        setCreateError(
          body.detail ?? body.scopes?.[0] ?? "Failed to create API key."
        );
      }
    } finally {
      setCreating(false);
    }
  }

  async function handleSaveScopes(keyId: string) {
    setEditError("");
    setSavingScopes(true);
    try {
      const res = await apiRequest(`/api/teams/apikeys/${keyId}/scopes/`, {
        method: "PATCH",
        body: JSON.stringify({ scopes: ["read", ...editScopes] }),
      });
      if (res.ok) {
        setEditingId(null);
        await fetchKeys();
      } else {
        const body = await res.json().catch(() => ({}));
        setEditError(body.detail ?? "Failed to update permissions.");
      }
    } finally {
      setSavingScopes(false);
    }
  }

  async function handleRevoke(keyId: string) {
    setRevoking(keyId);
    try {
      await apiRequest(`/api/teams/apikeys/${keyId}/`, { method: "DELETE" });
      await fetchKeys();
    } finally {
      setRevoking(null);
    }
  }

  function handleCopy() {
    if (!newKey?.key) return;
    navigator.clipboard.writeText(newKey.key).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Key className="h-5 w-5 text-slate-400" />
          <div>
            <h1 className="text-xl font-semibold text-slate-900">API Keys</h1>
            <p className="text-sm text-slate-500">
              Authenticate programmatic requests with Bearer mm_… tokens.
            </p>
          </div>
        </div>
        <button
          onClick={() => { setCreateOpen(true); setNewKey(null); }}
          className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          <Plus className="h-4 w-4" />
          New key
        </button>
      </div>

      {/* New key revealed once */}
      {newKey?.key && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 space-y-3">
          <div className="flex items-start gap-2">
            <Eye className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
            <div>
              <p className="text-sm font-semibold text-emerald-800">
                Your new API key — copy it now
              </p>
              <p className="mt-0.5 text-xs text-emerald-700">
                This key will not be shown again. Store it securely.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 rounded-md border border-emerald-200 bg-white px-3 py-2 font-mono text-xs text-slate-800 break-all">
              {newKey.key}
            </code>
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 rounded-md border border-emerald-300 bg-white px-3 py-2 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
            >
              {copied ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p className="text-xs text-emerald-700">
            Permissions: {newKey.scopes.map((s) => SCOPE_LABELS[s] ?? s).join(", ")}
          </p>
        </div>
      )}

      {/* Create form */}
      {createOpen && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-4">
          <p className="text-sm font-medium text-slate-800">Create API key</p>
          {createError && <p className="text-sm text-red-600">{createError}</p>}
          <form onSubmit={handleCreate} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Key name</label>
              <input
                type="text"
                placeholder="e.g. Postman, CI pipeline, Integration"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                maxLength={100}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              />
            </div>

            <ScopePicker selected={newScopes} onChange={setNewScopes} />

            <div className="flex gap-3">
              <button
                type="submit"
                disabled={creating || !newName.trim()}
                className="rounded-md bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
              >
                {creating ? "Creating…" : "Create"}
              </button>
              <button
                type="button"
                onClick={() => { setCreateOpen(false); setCreateError(""); setNewScopes([]); }}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Key list */}
      {loading ? (
        <div className="py-12 text-center text-sm text-slate-400">Loading…</div>
      ) : keys.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 py-10 text-center">
          <Key className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-2 text-sm text-slate-400">No API keys yet. Create one above.</p>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {keys.map((k) => (
            <div key={k.id} className="px-5 py-4">
              <div className="flex items-center gap-4">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-slate-900">{k.name}</p>
                    {!k.is_active && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600 ring-1 ring-inset ring-red-200">
                        Revoked
                      </span>
                    )}
                    <ScopeBadges scopes={k.scopes} />
                  </div>
                  <p className="font-mono text-xs text-slate-400">{k.display}</p>
                  <p className="text-xs text-slate-400">
                    Created {new Date(k.created_at).toLocaleDateString()} by {k.created_by_email}
                    {k.last_used_at && (
                      <> · Last used {new Date(k.last_used_at).toLocaleDateString()}</>
                    )}
                    {!k.last_used_at && <> · Never used</>}
                    {k.expires_at && (
                      <> · Expires {new Date(k.expires_at).toLocaleDateString()}</>
                    )}
                  </p>
                </div>
                {k.is_active && (
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      onClick={() => {
                        setEditingId(editingId === k.id ? null : k.id);
                        setEditScopes(k.scopes.filter((s) => s !== "read"));
                        setEditError("");
                      }}
                      title="Change permissions"
                      className="rounded p-1.5 text-slate-300 hover:bg-slate-100 hover:text-slate-600"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleRevoke(k.id)}
                      disabled={revoking === k.id}
                      title="Revoke key"
                      className="rounded p-1.5 text-slate-300 hover:bg-red-50 hover:text-red-500 disabled:opacity-40"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                )}
              </div>

              {editingId === k.id && (
                <div className="mt-4 space-y-3 rounded-md border border-slate-200 bg-slate-50 p-4">
                  {editError && <p className="text-sm text-red-600">{editError}</p>}
                  <ScopePicker selected={editScopes} onChange={setEditScopes} />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleSaveScopes(k.id)}
                      disabled={savingScopes}
                      className="rounded-md bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
                    >
                      {savingScopes ? "Saving…" : "Save permissions"}
                    </button>
                    <button
                      onClick={() => setEditingId(null)}
                      className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Usage note */}
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 space-y-1">
        <p className="text-xs font-semibold text-slate-600">How to use</p>
        <p className="text-xs text-slate-500">
          Add the key to any API request as an HTTP header:
        </p>
        <code className="block rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-slate-700">
          Authorization: Bearer mm_…
        </code>
        <p className="pt-1 text-xs text-slate-400">
          A key holds only the permissions listed against it — never your own.
          Keys cannot sign in, change passwords, or reach platform
          administration. Revoke immediately if one is compromised.
        </p>
      </div>
    </div>
  );
}
