"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AppWindow,
  CheckCircle2,
  KeyRound,
  Link2,
  Mail,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import { useAuth } from "@/contexts/auth-context";
import { AstraResourceDialog } from "@/components/workspace/astra-resource-dialog";
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

type Mailbox = {
  id: string;
  email: string;
  full_name: string;
  status: string;
  mail_service_ready?: boolean;
};

type Permission = { key: string; label: string };

type Integration = {
  id: string;
  name: string;
  purpose: string;
  purpose_label: string;
  tenant_id: string;
  organization: string;
  mailbox_id: string;
  mailbox_email: string;
  mailbox_name: string;
  permissions: Permission[];
  created_at: string;
  last_used_at: string | null;
  active: boolean;
};

const PURPOSES = [
  { value: "sales_crm", label: "Sales / CRM" },
  { value: "helpdesk", label: "Helpdesk / Ticketing" },
  { value: "custom", label: "Custom application" },
];

const SCOPES = [
  { key: "mailbox.read", label: "See mailbox details", description: "See the approved mailbox address and display name." },
  { key: "mail.send", label: "Send email", description: "Send email only from this approved mailbox." },
  { key: "mail.read", label: "Read email", description: "Read messages and folders in this mailbox." },
  { key: "mail.modify", label: "Update read status", description: "Mark messages as read or unread. Does not allow deletion." },
  { key: "signatures.read", label: "Use signatures", description: "See and use signatures saved for this mailbox." },
];

const PRESETS: Record<string, string[]> = {
  sales_crm: ["mailbox.read", "mail.send", "signatures.read"],
  helpdesk: ["mailbox.read", "mail.read", "mail.modify", "mail.send"],
  custom: ["mailbox.read"],
};

export default function IntegrationsPage() {
  const { tenant } = useAuth();
  const canManageCredentials = tenant?.role === "owner" || tenant?.role === "admin";
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");

  const [createOpen, setCreateOpen] = useState(false);
  const [mailboxId, setMailboxId] = useState("");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("sales_crm");
  const [permissions, setPermissions] = useState<string[]>(PRESETS.sales_crm);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const [secret, setSecret] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [confirmRevoke, setConfirmRevoke] = useState("");
  const [revoking, setRevoking] = useState("");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "danger">("success");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [integrationResponse, mailboxResponse] = await Promise.all([
        apiRequest("/api/integrations/"),
        apiRequest("/api/mailboxes/"),
      ]);

      const integrationData = await integrationResponse.json().catch(() => null);
      if (integrationResponse.ok && Array.isArray(integrationData)) {
        setIntegrations(integrationData);
      } else if (integrationResponse.status === 403) {
        setLoadError("Your workspace role does not permit Connected Apps management.");
      } else {
        setLoadError(integrationData?.detail ?? "Connected Apps could not be loaded.");
      }

      if (mailboxResponse.ok) {
        const rows = await mailboxResponse.json() as Mailbox[];
        const active = rows.filter((mailbox) =>
          mailbox.status === "active" && mailbox.mail_service_ready !== false
        );
        setMailboxes(active);
        setMailboxId((current) =>
          active.some((mailbox) => mailbox.id === current)
            ? current
            : active[0]?.id || ""
        );
      }
    } catch {
      setLoadError("Connected Apps could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return integrations;
    return integrations.filter((integration) =>
      [
        integration.name,
        integration.purpose_label,
        integration.mailbox_email,
        ...integration.permissions.map((permission) => permission.label),
      ].some((value) => value.toLowerCase().includes(needle))
    );
  }, [integrations, query]);

  function choosePurpose(value: string) {
    setPurpose(value);
    setPermissions(PRESETS[value] ?? PRESETS.custom);
  }

  function togglePermission(key: string) {
    setPermissions((current) =>
      current.includes(key)
        ? current.filter((permission) => permission !== key)
        : [...current, key]
    );
  }

  async function createIntegration(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || !mailboxId || !permissions.length) return;
    setCreating(true);
    setCreateError("");
    setSecret("");
    setTenantId("");
    setMessage("");

    try {
      const response = await apiRequest("/api/integrations/", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          purpose,
          mailbox_id: mailboxId,
          permissions,
        }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setSecret(data.integration_secret || "");
        setTenantId(data.tenant_id || "");
        setName("");
        setPurpose("sales_crm");
        setPermissions(PRESETS.sales_crm);
        setCreateOpen(false);
        setMessageTone("success");
        setMessage("Connected App created. Copy the one-time Integration Secret now.");
        await load();
      } else {
        setCreateError(data?.detail ?? (data ? JSON.stringify(data) : "Connected App could not be created."));
      }
    } catch {
      setCreateError("Connected App could not be created.");
    } finally {
      setCreating(false);
    }
  }

  async function revokeIntegration(integration: Integration) {
    setRevoking(integration.id);
    setMessage("");
    try {
      const response = await apiRequest("/api/integrations/" + integration.id + "/", {
        method: "DELETE",
      });
      if (response.ok || response.status === 204) {
        setIntegrations((current) =>
          current.map((item) => item.id === integration.id ? { ...item, active: false } : item)
        );
        setConfirmRevoke("");
        setMessageTone("success");
        setMessage("Connected App revoked. Existing access tokens for it are no longer valid.");
      } else {
        const data = await response.json().catch(() => null);
        setMessageTone("danger");
        setMessage(data?.detail ?? "Connected App could not be revoked.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Connected App could not be revoked.");
    } finally {
      setRevoking("");
    }
  }

  return (
    <div className="portal-page astra-resource-page astra-advanced-page astra-settings-page astra-credential-page">
      <Link href="/app/settings" className="portal-back-link">← Workspace settings</Link>

      <PortalPageHeading
        title="Connected Apps"
        description="Give trusted CRM, helpdesk and business applications scoped access to one exact mailbox—without sharing its password."
        actions={
          <PortalButton
            type="button"
            disabled={!mailboxes.length || !canManageCredentials}
            onClick={() => {
              setCreateOpen(true);
              setCreateError("");
              setSecret("");
            }}
          >
            <Plus className="h-4 w-4" />
            Create connected app
          </PortalButton>
        }
      />

      {message && (
        <div className="mb-5"><PortalNotice tone={messageTone}>{message}</PortalNotice></div>
      )}

      {!loading && !mailboxes.length && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <Mail className="mt-0.5 h-4 w-4 shrink-0" />
            <span>An active, provisioned mailbox is required before a Connected App can be issued.</span>
          </PortalNotice>
        </div>
      )}

      {secret && (
        <PortalCard className="portal-secret-card" title="Copy these credentials now" subtitle="The Integration Secret is shown once. It begins the authorization flow but does not bypass administrator approval, password verification or 2FA.">
          <div className="portal-secret-stack">
            <div>
              <span>Tenant ID</span>
              <div className="portal-secret-value">
                <code>{tenantId}</code>
                <PortalCopyButton value={tenantId} label="Copy tenant ID" />
              </div>
            </div>
            <div>
              <span>Integration Secret</span>
              <div className="portal-secret-value">
                <code>{secret}</code>
                <PortalCopyButton value={secret} label="Copy integration secret" />
              </div>
            </div>
          </div>
        </PortalCard>
      )}

      <AstraResourceDialog
        open={createOpen && canManageCredentials}
        busy={creating}
        title="Create connected app"
        description="Issue a scoped credential bound permanently to one organization mailbox."
        onDismiss={() => { setCreateOpen(false); setCreateError(""); }}
      >
          <form onSubmit={createIntegration}>
            {createError && <div className="mb-4"><PortalNotice tone="danger">{createError}</PortalNotice></div>}

            <div className="portal-form-grid">
              <div className="portal-field">
                <label htmlFor="astra-connected-app-name">Application name</label>
                <input
                  id="astra-connected-app-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={100}
                  required
                  placeholder="MateCRM"
                />
              </div>

              <div className="portal-field">
                <label htmlFor="astra-connected-app-purpose">Purpose</label>
                <select id="astra-connected-app-purpose" value={purpose} onChange={(event) => choosePurpose(event.target.value)}>
                  {PURPOSES.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </select>
                <div className="portal-field-hint">Purpose chooses a safe starting permission set; you can adjust it below.</div>
              </div>

              <div className="portal-field full">
                <label htmlFor="astra-connected-app-mailbox">Mailbox</label>
                <select id="astra-connected-app-mailbox" value={mailboxId} onChange={(event) => setMailboxId(event.target.value)} required>
                  {mailboxes.map((mailbox) => (
                    <option key={mailbox.id} value={mailbox.id}>
                      {mailbox.full_name ? mailbox.full_name + " — " : ""}{mailbox.email}
                    </option>
                  ))}
                </select>
                <div className="portal-field-hint">This Connected App can never switch to another mailbox with the same credential.</div>
              </div>

              <div className="portal-field full">
                <label>Mailbox permissions</label>
                <div className="portal-integration-permissions">
                  {SCOPES.map((scope) => (
                    <label key={scope.key} className="portal-scope-option" data-active={permissions.includes(scope.key)}>
                      <input
                        type="checkbox"
                        checked={permissions.includes(scope.key)}
                        onChange={() => togglePermission(scope.key)}
                      />
                      <span>
                        <strong>{scope.label}</strong>
                        <small>{scope.description}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <div className="mt-4">
              <PortalNotice tone="info">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                <span>Connected Apps never receive domain administration, mailbox passwords, workspace administration, message deletion or PostBox master access.</span>
              </PortalNotice>
            </div>

            <div className="portal-detail-actions">
              <PortalButton type="submit" disabled={creating || !mailboxId || !name.trim() || !permissions.length}>
                <Link2 className="h-4 w-4" />
                {creating ? "Creating…" : "Create connected app"}
              </PortalButton>
              <PortalButton type="button" variant="secondary" disabled={creating} onClick={() => setCreateOpen(false)}>Cancel</PortalButton>
            </div>
          </form>
      </AstraResourceDialog>

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
              placeholder="Search connected apps…"
              aria-label="Search connected apps"
            />
          </div>
          <button type="button" className="portal-icon-button" onClick={load} disabled={loading} aria-label="Refresh Connected Apps">
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
            title={integrations.length ? "No matching Connected Apps" : "No Connected Apps yet"}
            description={integrations.length ? "Try a different search term." : "Create a mailbox-scoped connection when a trusted application needs mail access."}
          />
        ) : !loadError ? (
          <div className="portal-key-list">
            {filtered.map((integration) => (
              <div key={integration.id} className="portal-key-row">
                <div className="portal-key-main">
                  <span className="portal-avatar"><AppWindow className="h-4 w-4" /></span>
                  <div>
                    <div className="portal-key-title">
                      <strong>{integration.name}</strong>
                      <PortalStatus value={integration.active ? "Active" : "Revoked"} />
                    </div>
                    <div className="portal-key-meta">{integration.purpose_label}</div>
                    <div className="portal-destination-pill mt-2">
                      <Mail className="h-3 w-3" />
                      <span>{integration.mailbox_email}</span>
                    </div>
                    <div className="portal-scope-badges mt-2">
                      {integration.permissions.map((permission) => (
                        <span key={permission.key}>{permission.label}</span>
                      ))}
                    </div>
                    <div className="portal-key-meta mt-2">
                      Created {new Date(integration.created_at).toLocaleString()}
                      {" · "}
                      {integration.last_used_at ? "Last used " + new Date(integration.last_used_at).toLocaleString() : "Never used"}
                    </div>
                  </div>
                </div>

                {integration.active && (
                  <div className="portal-inline-actions">
                    <button
                      type="button"
                      className="portal-action-button danger"
                      onClick={() => setConfirmRevoke(integration.id)}
                      disabled={revoking === integration.id}
                      aria-label={"Revoke " + integration.name}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}

                {confirmRevoke === integration.id && (
                  <div className="portal-confirm-inline">
                    <PortalNotice tone="warn">
                      <div className="flex-1">Revoke <strong>{integration.name}</strong>? The application will immediately lose access to <strong>{integration.mailbox_email}</strong>.</div>
                      <div className="flex gap-2">
                        <PortalButton type="button" variant="secondary" onClick={() => setConfirmRevoke("")} disabled={revoking === integration.id}>Cancel</PortalButton>
                        <PortalButton type="button" variant="danger" onClick={() => revokeIntegration(integration)} disabled={revoking === integration.id}>
                          {revoking === integration.id ? "Revoking…" : "Revoke"}
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
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Connected Apps use a separate approval flow from general API keys. The one-time Integration Secret identifies the app; operational access is issued only after MateMail authorization completes.</span>
        </PortalNotice>
      </div>
    </div>
  );
}
