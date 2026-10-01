"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  Building2,
  CheckCircle2,
  CreditCard,
  KeyRound,
  Link2,
  LockKeyhole,
  RefreshCw,
  Settings2,
  ShieldAlert,
  Users,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalCopyButton,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface WorkspaceDetail {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
  domain_count: number;
  mailbox_count: number;
  member_count: number;
  my_role: "owner" | "admin" | "support" | "read_only";
  approved_at: string | null;
  review_reason: string;
  outbound_disabled: boolean;
  created_at: string;
}

function pretty(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function SettingsPage() {
  const { tenant, refreshTenant } = useAuth();
  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [nameEdit, setNameEdit] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState("");
  const [renameSuccess, setRenameSuccess] = useState(false);

  const fetchWorkspace = useCallback(async (showLoading = true) => {
    if (!tenant?.id) return;
    if (showLoading) setLoading(true);
    setLoadError("");
    try {
      const response = await apiRequest("/api/workspaces/" + tenant.id + "/");
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setWorkspace(data);
        setNameEdit(data.name);
      } else {
        setLoadError(data?.detail ?? "Workspace settings could not be loaded.");
      }
    } catch {
      setLoadError("Workspace settings could not be loaded.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [tenant?.id]);

  useEffect(() => {
    if (!tenant?.id) return;
    const timer = window.setTimeout(() => {
      void fetchWorkspace(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [fetchWorkspace, tenant?.id]);

  async function renameWorkspace(event: React.FormEvent) {
    event.preventDefault();
    if (!tenant?.id || !workspace || !nameEdit.trim()) return;
    setRenameError("");
    setRenameSuccess(false);
    setRenaming(true);
    try {
      const response = await apiRequest("/api/workspaces/" + tenant.id + "/", {
        method: "PATCH",
        body: JSON.stringify({ name: nameEdit.trim() }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setWorkspace(data);
        setNameEdit(data.name);
        setRenameSuccess(true);
        try {
          await refreshTenant();
        } catch {
          // The persisted rename succeeded even if refreshing the auth tenant
          // brief fails. A later session refresh will pick up the new name.
        }
      } else {
        setRenameError(data?.detail ?? "Workspace name could not be changed.");
      }
    } catch {
      setRenameError("Workspace name could not be changed.");
    } finally {
      setRenaming(false);
    }
  }

  if (loading) {
    return (
      <div className="portal-page">
        <PortalSkeleton className="mb-5 h-20 w-full" />
        <PortalSkeleton className="h-[430px] w-full" />
      </div>
    );
  }

  if (!workspace || loadError) {
    return (
      <div className="portal-page">
        <PortalPageHeading title="Workspace settings" description="Manage the configuration stored for this organization." />
        <PortalNotice tone="danger">{loadError || "Workspace settings could not be loaded."}</PortalNotice>
      </div>
    );
  }

  const canEdit = workspace.my_role === "owner" || workspace.my_role === "admin";

  return (
    <div className="portal-page">
      <PortalPageHeading
        title="Workspace settings"
        description="Keep your organization identity and access entry points in one place."
        actions={
          <PortalButton type="button" variant="secondary" onClick={() => fetchWorkspace(true)}>
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
            Refresh
          </PortalButton>
        }
      />

      {workspace.status === "pending_approval" && (
        <div className="mb-5">
          <PortalNotice tone="info">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>This workspace is awaiting MateMail approval. You can configure the workspace, but live mail provisioning remains gated.</span>
          </PortalNotice>
        </div>
      )}

      {workspace.status === "rejected" && (
        <div className="mb-5">
          <PortalNotice tone="danger">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <strong>Workspace approval was not granted.</strong>
              {workspace.review_reason ? " " + workspace.review_reason : ""}
            </span>
          </PortalNotice>
        </div>
      )}

      {workspace.status === "suspended" && (
        <div className="mb-5">
          <PortalNotice tone="danger">
            <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" />
            <span>This workspace is suspended. Live mail operations are disabled until the account status is resolved.</span>
          </PortalNotice>
        </div>
      )}

      {workspace.outbound_disabled && workspace.status !== "suspended" && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Outbound sending is currently disabled for this workspace. Administration and inbound service may remain available.</span>
          </PortalNotice>
        </div>
      )}

      <div className="portal-settings-layout">
        <div>
          <div className="portal-settings-intro">
            <h2>Organization details</h2>
            <p>These are the real workspace identity fields persisted by MateMail.</p>
          </div>

          <PortalCard>
            <div className="portal-org-identity">
              <span className="portal-workspace-monogram">{workspace.name.slice(0, 2).toUpperCase()}</span>
              <div>
                <strong>{workspace.name}</strong>
                <span>Organization workspace</span>
              </div>
              <PortalStatus value={pretty(workspace.status)} />
            </div>

            <form onSubmit={renameWorkspace}>
              <div className="portal-field">
                <label>Workspace name</label>
                <input
                  type="text"
                  required
                  maxLength={255}
                  disabled={!canEdit}
                  value={nameEdit}
                  onChange={(event) => {
                    setNameEdit(event.target.value);
                    setRenameSuccess(false);
                    setRenameError("");
                  }}
                />
                <div className="portal-field-hint">
                  {canEdit
                    ? "Owners and admins can rename the workspace."
                    : "Your role can view this setting but cannot change it."}
                </div>
                {renameError && <div className="portal-field-error">{renameError}</div>}
              </div>

              <div className="portal-field mt-4">
                <label>Workspace handle</label>
                <div className="portal-code-field">
                  <code>{workspace.slug}</code>
                  <PortalCopyButton value={workspace.slug} label="Copy workspace handle" />
                </div>
                <div className="portal-field-hint">The workspace handle is generated by MateMail and is not editable from the current backend.</div>
              </div>

              {canEdit && (
                <div className="portal-detail-actions">
                  <PortalButton
                    type="submit"
                    disabled={
                      renaming ||
                      !nameEdit.trim() ||
                      nameEdit.trim() === workspace.name
                    }
                  >
                    {renaming ? "Saving…" : "Save workspace name"}
                  </PortalButton>
                </div>
              )}

              {renameSuccess && (
                <div className="mt-4">
                  <PortalNotice tone="success">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>Workspace name updated.</span>
                  </PortalNotice>
                </div>
              )}
            </form>
          </PortalCard>
        </div>

        <div>
          <div className="portal-settings-intro">
            <h2>Workspace facts</h2>
            <p>Identifiers, access role and current platform state.</p>
          </div>

          <PortalCard>
            <dl className="portal-detail-list">
              <div className="portal-detail-row">
                <dt>Workspace ID</dt>
                <dd className="flex items-center gap-2">
                  <code>{workspace.id}</code>
                  <PortalCopyButton value={workspace.id} label="Copy workspace ID" />
                </dd>
              </div>
              <div className="portal-detail-row">
                <dt>Your role</dt>
                <dd>{pretty(workspace.my_role)}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Plan key</dt>
                <dd>{pretty(workspace.plan)}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Domains</dt>
                <dd>{workspace.domain_count}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Mailboxes</dt>
                <dd>{workspace.mailbox_count}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Members</dt>
                <dd>{workspace.member_count}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Approved</dt>
                <dd>{workspace.approved_at ? new Date(workspace.approved_at).toLocaleString() : "Not yet"}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Outbound sending</dt>
                <dd>{workspace.outbound_disabled ? "Disabled" : "Not separately restricted"}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Created</dt>
                <dd>{new Date(workspace.created_at).toLocaleString()}</dd>
              </div>
            </dl>
          </PortalCard>
        </div>
      </div>

      <PortalCard
        className="mt-5"
        title="People & connections"
        subtitle="Open the existing MateMail areas that control workspace access and commercial limits."
      >
        <div className="portal-settings-links">
          <Link href="/app/team" className="portal-settings-link">
            <Users className="h-5 w-5" />
            <div><strong>Team permissions</strong><span>Members, invitations and workspace roles</span></div>
          </Link>
          <Link href="/app/settings/api-keys" className="portal-settings-link">
            <KeyRound className="h-5 w-5" />
            <div><strong>API keys</strong><span>Programmatic access and scoped credentials</span></div>
          </Link>
          <Link href="/app/settings/integrations" className="portal-settings-link">
            <Link2 className="h-5 w-5" />
            <div><strong>Connected Apps</strong><span>Trusted applications linked to exact mailboxes</span></div>
          </Link>
          <Link href="/app/billing" className="portal-settings-link">
            <CreditCard className="h-5 w-5" />
            <div><strong>Billing & usage</strong><span>Plan allowances, usage and policy limits</span></div>
          </Link>
        </div>
      </PortalCard>

      <div className="mt-4">
        <PortalNotice tone="info">
          <Settings2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Prototype-only website, country, timezone, mail-default and notification controls are not shown because MateMail does not currently persist those workspace settings.</span>
        </PortalNotice>
      </div>

      {!canEdit && (
        <div className="mt-4">
          <PortalNotice tone="info">
            <Building2 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Your <strong>{pretty(workspace.my_role)}</strong> role has read access to workspace configuration. Only Owner and Admin can rename the workspace.</span>
          </PortalNotice>
        </div>
      )}
    </div>
  );
}
