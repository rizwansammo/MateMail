"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  CalendarDays,
  CheckCircle2,
  Copy,
  Eye,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalCopyButton,
  PortalEmptyState,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface APIKey {
  id: string;
  name: string;
  key_prefix: string;
  display: string;
  scopes: string[];
  is_read_only: boolean;
  created_by_email: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  is_active: boolean;
  key?: string;
}

const WRITE_SCOPES = [
  { value: "domains:write", label: "Manage domains", detail: "Add/remove domains and run domain checks." },
  { value: "mailboxes:write", label: "Manage mailboxes", detail: "Create/update/delete mailboxes and passwords." },
  { value: "routing:write", label: "Manage routing", detail: "Create/remove aliases and forwarding rules." },
  { value: "admin", label: "Workspace administration", detail: "Team, API keys, billing, backups, queue and quarantine." },
] as const;

const SCOPE_LABELS: Record<string, string> = {
  read: "Read workspace",
  ...Object.fromEntries(WRITE_SCOPES.map((scope) => [scope.value, scope.label])),
};

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
        ? selected.filter((scope) => scope !== value)
        : [...selected, value]
    );
  }

  return (
    <div className="portal-scope-grid">
      {WRITE_SCOPES.map((scope) => (
        <label key={scope.value} className="portal-scope-option" data-active={selected.includes(scope.value)}>
          <input
            type="checkbox"
            checked={selected.includes(scope.value)}
            onChange={() => toggle(scope.value)}
          />
          <span>
            <strong>{scope.label}</strong>
            <small>{scope.detail}</small>
          </span>
        </label>
      ))}
    </div>
  );
}

function ScopeSummary({ scopes }: { scopes: string[] }) {
  const writes = scopes.filter((scope) => scope !== "read");
  if (!writes.length) {
    return <span className="portal-role-badge"><ShieldCheck className="h-3.5 w-3.5" />Read-only</span>;
  }
  return (
    <div className="portal-scope-badges">
      {writes.map((scope) => (
        <span key={scope}>{SCOPE_LABELS[scope] ?? scope}</span>
      ))}
    </div>
  );
}

export default function APIKeysPage() {
  const [keys, setKeys] = useState<APIKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newScopes, setNewScopes] = useState<string[]>([]);
  const [newExpiry, setNewExpiry] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [newKey, setNewKey] = useState<APIKey | null>(null);

  const [editingId, setEditingId] = useState("");
  const [editName, setEditName] = useState("");
  const [editScopes, setEditScopes] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");

  const [confirmRevoke, setConfirmRevoke] = useState("");
  const [revoking, setRevoking] = useState("");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "danger">("success");

  const fetchKeys = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await apiRequest("/api/teams/apikeys/");
      const data = await response.json().catch(() => null);
      if (response.ok && Array.isArray(data)) {
        setKeys(data);
      } else if (response.status === 403) {
        setLoadError("Your workspace role does not permit API key management.");
      } else {
        setLoadError(data?.detail ?? "API keys could not be loaded.");
      }
    } catch {
      setLoadError("API keys could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchKeys(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchKeys]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return keys;
    return keys.filter((key) =>
      [
        key.name,
        key.display,
        key.created_by_email || "",
        ...key.scopes,
      ].some((value) => value.toLowerCase().includes(needle))
    );
  }, [keys, query]);

  async function createKey(event: React.FormEvent) {
    event.preventDefault();
    if (!newName.trim()) return;
    setCreateError("");
    setCreating(true);
    setMessage("");
    try {
      const body: Record<string, unknown> = {
        name: newName.trim(),
        scopes: ["read", ...newScopes],
      };
      if (newExpiry) {
        body.expires_at = new Date(newExpiry + "T23:59:59").toISOString();
      }
      const response = await apiRequest("/api/teams/apikeys/", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setNewKey(data);
        setNewName("");
        setNewScopes([]);
        setNewExpiry("");
        setCreateOpen(false);
        setMessageTone("success");
        setMessage("API key created. Copy the secret now; it will not be shown again.");
        await fetchKeys();
      } else {
        setCreateError(data?.detail ?? data?.scopes?.[0] ?? data?.expires_at?.[0] ?? "API key could not be created.");
      }
    } catch {
      setCreateError("API key could not be created.");
    } finally {
      setCreating(false);
    }
  }

  function startEdit(key: APIKey) {
    setEditingId(key.id);
    setEditName(key.name);
    setEditScopes(key.scopes.filter((scope) => scope !== "read"));
    setEditError("");
  }

  async function saveEdit(key: APIKey) {
    setSaving(true);
    setEditError("");
    setMessage("");
    try {
      const response = await apiRequest("/api/teams/apikeys/" + key.id + "/scopes/", {
        method: "PATCH",
        body: JSON.stringify({
          name: editName.trim(),
          scopes: ["read", ...editScopes],
        }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setKeys((current) => current.map((item) => item.id === key.id ? data : item));
        setEditingId("");
        setMessageTone("success");
        setMessage("API key details updated.");
      } else {
        setEditError(data?.detail ?? data?.scopes?.[0] ?? "API key could not be updated.");
      }
    } catch {
      setEditError("API key could not be updated.");
    } finally {
      setSaving(false);
    }
  }

  async function revokeKey(key: APIKey) {
    setRevoking(key.id);
    setMessage("");
    try {
      const response = await apiRequest("/api/teams/apikeys/" + key.id + "/", {
        method: "DELETE",
      });
      if (response.ok || response.status === 204) {
        setKeys((current) => current.map((item) => item.id === key.id ? { ...item, is_active: false } : item));
        setConfirmRevoke("");
        setMessageTone("success");
        setMessage("API key revoked.");
      } else {
        const data = await response.json().catch(() => null);
        setMessageTone("danger");
        setMessage(data?.detail ?? "API key could not be revoked.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("API key could not be revoked.");
    } finally {
      setRevoking("");
    }
  }

  return (
    <div className="portal-page">
      <Link href="/app/settings" className="portal-back-link">← Workspace settings</Link>

      <PortalPageHeading
        title="API keys"
        description="Create scoped credentials for programmatic MateMail API access."
        actions={
          <PortalButton
            type="button"
            onClick={() => {
              setCreateOpen(true);
              setNewKey(null);
              setCreateError("");
            }}
          >
            <Plus className="h-4 w-4" />
            New API key
          </PortalButton>
        }
      />

      {message && (
        <div className="mb-5"><PortalNotice tone={messageTone}>{message}</PortalNotice></div>
      )}

      {newKey?.key && (
        <PortalCard className="portal-secret-card" title="Copy your new API key now" subtitle="MateMail stores only a hash. The full key cannot be shown again.">
          <div className="portal-secret-value">
            <code>{newKey.key}</code>
            <PortalCopyButton value={newKey.key} label="Copy API key" />
          </div>
          <div className="mt-3"><ScopeSummary scopes={newKey.scopes} /></div>
        </PortalCard>
      )}

      {createOpen && (
        <PortalCard className="portal-form-card" title="Create API key" subtitle="Every key can read workspace data. Grant only the write scopes the integration genuinely needs.">
          <form onSubmit={createKey}>
            {createError && <div className="mb-4"><PortalNotice tone="danger">{createError}</PortalNotice></div>}
            <div className="portal-form-grid">
              <div className="portal-field">
                <label>Key name</label>
                <input
                  type="text"
                  required
                  maxLength={100}
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                  placeholder="CI pipeline"
                />
              </div>
              <div className="portal-field">
                <label>Expiry date (optional)</label>
                <input
                  type="date"
                  value={newExpiry}
                  min={new Date().toISOString().slice(0, 10)}
                  onChange={(event) => setNewExpiry(event.target.value)}
                />
                <div className="portal-field-hint">Expiry is enforced by the credential model when configured.</div>
              </div>
              <div className="full">
                <div className="portal-field">
                  <label>Write permissions</label>
                  <ScopePicker selected={newScopes} onChange={setNewScopes} />
                </div>
              </div>
            </div>
            <div className="portal-detail-actions">
              <PortalButton type="submit" disabled={creating || !newName.trim()}>
                <KeyRound className="h-4 w-4" />
                {creating ? "Creating…" : "Create key"}
              </PortalButton>
              <PortalButton
                type="button"
                variant="secondary"
                disabled={creating}
                onClick={() => {
                  setCreateOpen(false);
                  setCreateError("");
                }}
              >
                Cancel
              </PortalButton>
            </div>
          </form>
        </PortalCard>
      )}

      {loadError && (
        <div className="mb-5"><PortalNotice tone="danger">{loadError}</PortalNotice></div>
      )}

      <PortalCard className="portal-management-card" bodyClassName="!p-0">
        <div className="portal-management-toolbar">
          <div className="portal-management-search">
            <Search className="h-4 w-4" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search API keys…"
              aria-label="Search API keys"
            />
          </div>
          <button type="button" className="portal-icon-button" onClick={fetchKeys} disabled={loading} aria-label="Refresh API keys">
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
          </button>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-16 w-full" />
            <PortalSkeleton className="h-16 w-full" />
          </div>
        ) : !loadError && filtered.length === 0 ? (
          <PortalEmptyState
            title={keys.length ? "No matching API keys" : "No API keys yet"}
            description={keys.length ? "Try a different search term." : "Create a scoped credential when an external tool needs MateMail API access."}
            action={!keys.length ? <PortalButton type="button" onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" />New key</PortalButton> : undefined}
          />
        ) : !loadError ? (
          <div className="portal-key-list">
            {filtered.map((key) => (
              <div key={key.id} className="portal-key-row">
                <div className="portal-key-main">
                  <span className="portal-avatar"><KeyRound className="h-4 w-4" /></span>
                  <div>
                    <div className="portal-key-title">
                      <strong>{key.name}</strong>
                      <PortalStatus value={key.is_active ? "Active" : "Revoked"} />
                    </div>
                    <code>{key.display}</code>
                    <div className="portal-key-meta">
                      Created {new Date(key.created_at).toLocaleDateString()}
                      {key.created_by_email ? " by " + key.created_by_email : ""}
                      {" · "}
                      {key.last_used_at ? "Last used " + new Date(key.last_used_at).toLocaleString() : "Never used"}
                      {key.expires_at ? " · Expires " + new Date(key.expires_at).toLocaleString() : ""}
                    </div>
                    <div className="mt-2"><ScopeSummary scopes={key.scopes} /></div>
                  </div>
                </div>

                {key.is_active && (
                  <div className="portal-inline-actions">
                    <button type="button" className="portal-action-button" onClick={() => startEdit(key)} aria-label={"Edit " + key.name}>
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" className="portal-action-button danger" onClick={() => setConfirmRevoke(key.id)} aria-label={"Revoke " + key.name}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}

                {editingId === key.id && (
                  <div className="portal-key-editor">
                    {editError && <PortalNotice tone="danger">{editError}</PortalNotice>}
                    <div className="portal-field">
                      <label>Key name</label>
                      <input value={editName} maxLength={100} onChange={(event) => setEditName(event.target.value)} />
                    </div>
                    <div className="portal-field">
                      <label>Write permissions</label>
                      <ScopePicker selected={editScopes} onChange={setEditScopes} />
                    </div>
                    <div className="portal-detail-actions">
                      <PortalButton type="button" onClick={() => saveEdit(key)} disabled={saving || !editName.trim()}>
                        {saving ? "Saving…" : "Save changes"}
                      </PortalButton>
                      <PortalButton type="button" variant="secondary" onClick={() => setEditingId("")} disabled={saving}>Cancel</PortalButton>
                    </div>
                  </div>
                )}

                {confirmRevoke === key.id && (
                  <div className="portal-confirm-inline">
                    <PortalNotice tone="warn">
                      <div className="flex-1">Revoke <strong>{key.name}</strong>? Any system using this credential will lose access immediately.</div>
                      <div className="flex gap-2">
                        <PortalButton type="button" variant="secondary" onClick={() => setConfirmRevoke("")} disabled={revoking === key.id}>Cancel</PortalButton>
                        <PortalButton type="button" variant="danger" onClick={() => revokeKey(key)} disabled={revoking === key.id}>
                          {revoking === key.id ? "Revoking…" : "Revoke key"}
                        </PortalButton>
                      </div>
                    </PortalNotice>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : null}
      </PortalCard>

      <div className="mt-4">
        <PortalNotice tone="info">
          <Eye className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Use API keys as <code>Authorization: Bearer mm_…</code>. Keys cannot sign in to the Portal, change passwords, or access platform administration. Scope changes are audited.
          </span>
        </PortalNotice>
      </div>
    </div>
  );
}
