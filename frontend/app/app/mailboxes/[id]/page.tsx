"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  AtSign,
  CheckCircle2,
  CornerUpRight,
  KeyRound,
  Mail,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalCopyButton,
  PortalNotice,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface Mailbox {
  id: string;
  email: string;
  full_name: string;
  local_part: string;
  domain: string;
  domain_name: string;
  status: "active" | "disabled" | "suspended";
  quota_mb: number;
  storage_used_mb: number;
  mail_service_ready: boolean;
  mail_service_message: string;
  last_login: string | null;
  created_at: string;
}

interface Alias {
  id: string;
  destination_mailbox: string;
  destination_email: string;
}

interface ForwardingRule {
  id: string;
  source_mailbox: string;
}

function initials(value: string) {
  const parts = value.split(/[\s@._-]+/).filter(Boolean).slice(0, 2);
  return parts.map((part) => part[0]?.toUpperCase()).join("") || "MB";
}

function formatStorage(mb: number) {
  if (mb >= 1024) {
    const gb = mb / 1024;
    return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`;
  }
  return `${mb} MB`;
}

export default function MailboxDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { user, tenant } = useAuth();

  const [mailbox, setMailbox] = useState<Mailbox | null>(null);
  const [aliases, setAliases] = useState<Alias[]>([]);
  const [forwarding, setForwarding] = useState<ForwardingRule[]>([]);
  const [myRole, setMyRole] = useState("");
  const [workspaceStatus, setWorkspaceStatus] = useState(tenant?.status || "");
  const [loading, setLoading] = useState(true);
  const [statusLoading, setStatusLoading] = useState(false);
  const [reprovOpen, setReprovOpen] = useState(false);
  const [reprovPassword, setReprovPassword] = useState("");
  const [reprovLoading, setReprovLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const [actionFailed, setActionFailed] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const fetchMailbox = useCallback(async () => {
    const [mailboxResponse, aliasResponse, forwardingResponse] = await Promise.all([
      apiRequest(`/api/mailboxes/${params.id}/`),
      apiRequest("/api/aliases/"),
      apiRequest("/api/forwarding/"),
    ]);
    if (mailboxResponse.ok) setMailbox(await mailboxResponse.json());
    if (aliasResponse.ok) setAliases(await aliasResponse.json());
    if (forwardingResponse.ok) setForwarding(await forwardingResponse.json());
  }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await Promise.all([
          fetchMailbox(),
          tenant?.id
            ? apiRequest(`/api/workspaces/${tenant.id}/stats/`)
                .then(async (response) => response.ok ? response.json() : null)
                .then((data) => {
                  if (cancelled) return;
                  if (data?.my_role) setMyRole(data.my_role);
                  if (data?.tenant_status) setWorkspaceStatus(data.tenant_status);
                })
            : Promise.resolve(),
        ]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchMailbox, tenant?.id]);

  const canAdmin = myRole === "owner" || myRole === "admin";
  const canProvision =
    canAdmin &&
    !!user?.email_verified &&
    workspaceStatus === "active";

  const linkedAliases = useMemo(
    () => mailbox ? aliases.filter((alias) => alias.destination_mailbox === mailbox.id) : [],
    [aliases, mailbox]
  );
  const linkedForwarding = useMemo(
    () => mailbox ? forwarding.filter((rule) => rule.source_mailbox === mailbox.id) : [],
    [forwarding, mailbox]
  );

  async function toggleStatus() {
    if (!mailbox || !canAdmin || mailbox.status === "suspended") return;
    setStatusLoading(true);
    setActionMessage("");
    setActionFailed(false);
    try {
      const response = await apiRequest(`/api/mailboxes/${params.id}/status/`, {
        method: "PATCH",
        body: JSON.stringify({
          status: mailbox.status === "active" ? "disabled" : "active",
        }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setMailbox(data);
        setActionMessage(
          data.status === "active"
            ? "Mailbox enabled successfully."
            : "Mailbox disabled successfully."
        );
      } else {
        setActionFailed(true);
        setActionMessage(data?.detail ?? "Mailbox status could not be changed.");
      }
    } catch {
      setActionFailed(true);
      setActionMessage("Mailbox status could not be changed.");
    } finally {
      setStatusLoading(false);
    }
  }

  async function reprovision(event: React.FormEvent) {
    event.preventDefault();
    if (!mailbox || !canProvision) return;
    setActionMessage("");
    setActionFailed(false);
    setReprovLoading(true);
    try {
      const response = await apiRequest(`/api/mailboxes/${params.id}/reprovision/`, {
        method: "POST",
        body: JSON.stringify({ password: reprovPassword }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setMailbox(data);
        setReprovOpen(false);
        setReprovPassword("");
        setActionMessage("Mailbox password and mail-service state were updated.");
      } else {
        setActionFailed(true);
        setActionMessage(data?.detail ?? "Re-provisioning failed.");
      }
    } catch {
      setActionFailed(true);
      setActionMessage("Re-provisioning failed.");
    } finally {
      setReprovLoading(false);
    }
  }

  async function deleteMailbox() {
    if (!mailbox || !canAdmin) return;
    setDeleting(true);
    setActionMessage("");
    setActionFailed(false);
    try {
      const response = await apiRequest(`/api/mailboxes/${params.id}/`, { method: "DELETE" });
      if (response.ok || response.status === 204) {
        router.push("/app/mailboxes");
        return;
      }
      const data = await response.json().catch(() => null);
      setActionFailed(true);
      setActionMessage(data?.detail ?? "Mailbox could not be removed.");
    } catch {
      setActionFailed(true);
      setActionMessage("Mailbox could not be removed.");
    } finally {
      setDeleting(false);
    }
  }

  if (loading) {
    return (
      <div className="portal-page astra-resource-page">
        <PortalSkeleton className="mb-5 h-24 w-full" />
        <PortalSkeleton className="h-[420px] w-full" />
      </div>
    );
  }

  if (!mailbox) {
    return (
      <div className="portal-page astra-resource-page">
        <PortalCard>
          <div className="portal-empty">
            <div>
              <Mail className="mx-auto h-7 w-7 text-[var(--portal-faint)]" />
              <h3>Mailbox not found</h3>
              <p>This mailbox is not available in the current workspace.</p>
              <div className="mt-4">
                <Link href="/app/mailboxes" className="portal-button secondary">Back to mailboxes</Link>
              </div>
            </div>
          </div>
        </PortalCard>
      </div>
    );
  }

  const usagePercent = mailbox.quota_mb
    ? Math.min(100, Math.round((mailbox.storage_used_mb / mailbox.quota_mb) * 100))
    : 0;
  const availableMb = Math.max(0, mailbox.quota_mb - mailbox.storage_used_mb);

  return (
    <div className="portal-page astra-resource-page">
      <Link href="/app/mailboxes" className="portal-back-link">
        <ArrowLeft className="h-3.5 w-3.5" />
        Mailboxes
      </Link>

      <div className="portal-mailbox-hero">
        <span className="portal-avatar">{initials(mailbox.full_name || mailbox.email)}</span>
        <div className="min-w-0 flex-1">
          <h1>{mailbox.full_name || mailbox.email}</h1>
          <p>
            {mailbox.email}
            <PortalCopyButton value={mailbox.email} label="Copy mailbox address" />
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <PortalStatus value={mailbox.status} />
            <span className={"portal-service-state " + (mailbox.mail_service_ready ? "ready" : "waiting")}>
              {mailbox.mail_service_ready
                ? <CheckCircle2 className="h-3.5 w-3.5" />
                : <AlertCircle className="h-3.5 w-3.5" />}
              {mailbox.mail_service_ready ? "Mail service ready" : "Mail service not ready"}
            </span>
          </div>
        </div>
      </div>

      {actionMessage && (
        <div className="mb-5">
          <PortalNotice tone={actionFailed ? "danger" : "success"}>{actionMessage}</PortalNotice>
        </div>
      )}

      <div className="portal-mailbox-grid">
        <div className="space-y-5">
          <PortalCard title="Mailbox details" subtitle="Current mailbox identity and service state.">
            <dl className="portal-detail-list">
              <div className="portal-detail-row">
                <dt>Email address</dt>
                <dd>{mailbox.email}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Domain</dt>
                <dd>{mailbox.domain_name}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Display name</dt>
                <dd>{mailbox.full_name}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Created</dt>
                <dd>{new Date(mailbox.created_at).toLocaleString()}</dd>
              </div>
              <div className="portal-detail-row">
                <dt>Last login</dt>
                <dd>{mailbox.last_login ? new Date(mailbox.last_login).toLocaleString() : "Never"}</dd>
              </div>
            </dl>
          </PortalCard>

          <PortalCard title="Mailbox storage" subtitle="Usage reported by the MateMail backend.">
            <div className="portal-storage">
              <div className="portal-storage-head">
                <strong>{formatStorage(mailbox.storage_used_mb)} used</strong>
                <span>{formatStorage(mailbox.quota_mb)} quota</span>
              </div>
              <div className="portal-storage-meter">
                <span style={{ width: `${usagePercent}%` }} />
              </div>
              <small>{formatStorage(availableMb)} available · {usagePercent}% used</small>
            </div>
          </PortalCard>

          <PortalCard title="Aliases & forwarding" subtitle="Routing that currently references this mailbox.">
            <div className="portal-routing-summary">
              <Link href="/app/aliases" className="portal-routing-link">
                <AtSign className="h-4 w-4" />
                <div>
                  <strong>{linkedAliases.length} linked {linkedAliases.length === 1 ? "alias" : "aliases"}</strong>
                  <small>Addresses delivering directly to this mailbox</small>
                </div>
              </Link>
              <Link href="/app/forwarding" className="portal-routing-link">
                <CornerUpRight className="h-4 w-4" />
                <div>
                  <strong>{linkedForwarding.length} forwarding {linkedForwarding.length === 1 ? "rule" : "rules"}</strong>
                  <small>Incoming messages routed onward from this mailbox</small>
                </div>
              </Link>
            </div>
          </PortalCard>
        </div>

        <aside className="space-y-5">
          <PortalCard title="Mail service" subtitle="Provisioning and credential management.">
            <div className="flex items-start gap-2">
              {mailbox.mail_service_ready ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[var(--portal-success)]" />
              ) : (
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--portal-warning)]" />
              )}
              <div>
                <strong className="text-[11px] text-[var(--portal-text-strong)]">
                  {mailbox.mail_service_ready ? "Provisioned and ready" : "Needs provisioning attention"}
                </strong>
                <p className="mt-1 text-[9px] leading-5 text-[var(--portal-muted)]">
                  {mailbox.mail_service_ready
                    ? "Use re-provisioning only when setting a new mailbox password."
                    : "Re-provision with a password to reconcile this mailbox with the Mail Engine."}
                </p>
              </div>
            </div>

            {mailbox.mail_service_message && (
              <div className="mt-4">
                <PortalNotice tone="danger">{mailbox.mail_service_message}</PortalNotice>
              </div>
            )}

            {canAdmin && (
              <div className="portal-detail-actions">
                <PortalButton
                  type="button"
                  variant="secondary"
                  onClick={() => setReprovOpen((open) => !open)}
                  disabled={!canProvision}
                >
                  <KeyRound className="h-4 w-4" />
                  {mailbox.mail_service_ready ? "Change password" : "Re-provision"}
                </PortalButton>
              </div>
            )}

            {reprovOpen && (
              <form onSubmit={reprovision} className="mt-4">
                <div className="portal-field">
                  <label>New mailbox password</label>
                  <input
                    type="password"
                    autoComplete="new-password"
                    minLength={10}
                    required
                    value={reprovPassword}
                    onChange={(event) => setReprovPassword(event.target.value)}
                    placeholder="At least 10 characters"
                  />
                  <div className="portal-field-hint">MateMail never stores this password.</div>
                </div>
                <div className="portal-detail-actions">
                  <PortalButton type="submit" disabled={reprovLoading || reprovPassword.length < 10}>
                    <RefreshCw className={"h-4 w-4 " + (reprovLoading ? "animate-spin" : "")} />
                    {reprovLoading ? "Saving…" : "Set password"}
                  </PortalButton>
                  <PortalButton
                    type="button"
                    variant="secondary"
                    disabled={reprovLoading}
                    onClick={() => {
                      setReprovOpen(false);
                      setReprovPassword("");
                    }}
                  >
                    Cancel
                  </PortalButton>
                </div>
              </form>
            )}
          </PortalCard>

          <PortalCard title="Mailbox access" subtitle="Enable or disable mail service for this address.">
            {mailbox.status === "suspended" ? (
              <PortalNotice tone="danger">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                <span>This mailbox is suspended by platform policy and cannot be toggled from the workspace.</span>
              </PortalNotice>
            ) : (
              <div className="portal-switch-row">
                <div>
                  <strong>{mailbox.status === "active" ? "Mailbox enabled" : "Mailbox disabled"}</strong>
                  <p>{mailbox.status === "active" ? "Sending and receiving are allowed." : "Activate to resume sending and receiving."}</p>
                </div>
                <button
                  type="button"
                  className="portal-toggle"
                  data-on={mailbox.status === "active"}
                  disabled={!canAdmin || statusLoading}
                  onClick={toggleStatus}
                  aria-label={mailbox.status === "active" ? "Disable mailbox" : "Enable mailbox"}
                />
              </div>
            )}
          </PortalCard>

          <PortalNotice tone="info">
            <Mail className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              Reading mailbox contents is separate from administration. The old administrator Webmail SSO was removed; mailbox users sign in to PostBox with their own credentials.
            </span>
          </PortalNotice>
        </aside>
      </div>

      {canAdmin && (
        <div className="portal-danger-zone">
          <strong>Delete mailbox</strong>
          <p>Removing a mailbox permanently removes the workspace record and queues Mail Engine cleanup first. Linked aliases and forwarding rules may also be affected by database relationships.</p>
          {!confirmDelete ? (
            <PortalButton type="button" variant="danger" onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-4 w-4" />
              Delete mailbox
            </PortalButton>
          ) : (
            <div className="flex flex-wrap gap-2">
              <PortalButton type="button" variant="secondary" disabled={deleting} onClick={() => setConfirmDelete(false)}>
                Cancel
              </PortalButton>
              <PortalButton type="button" variant="danger" disabled={deleting} onClick={deleteMailbox}>
                {deleting ? "Removing…" : "Confirm deletion"}
              </PortalButton>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
