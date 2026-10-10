"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  CornerUpRight,
  Mail,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { AstraResourceDialog } from "@/components/workspace/astra-resource-dialog";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalEmptyState,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

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

function fieldError(value: unknown) {
  if (!value) return "";
  if (Array.isArray(value)) return value.map(String).join(" ");
  return String(value);
}

function ForwardingPageContent() {
  const routeParams = useSearchParams();
  const requestedSearch = routeParams.get("q") || "";
  const { user, tenant } = useAuth();
  const [rules, setRules] = useState<ForwardingRule[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [myRole, setMyRole] = useState("");
  const [workspaceStatus, setWorkspaceStatus] = useState(tenant?.status || "");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "warn" | "danger">("success");
  const [addOpen, setAddOpen] = useState(false);

  const [mailboxId, setMailboxId] = useState("");
  const [destinationEmail, setDestinationEmail] = useState("");
  const [keepCopy, setKeepCopy] = useState(true);
  const [addErrors, setAddErrors] = useState<Record<string, unknown>>({});
  const [adding, setAdding] = useState(false);
  const [changingStatus, setChangingStatus] = useState("");
  const [deleteTarget, setDeleteTarget] = useState("");
  const [deleting, setDeleting] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [ruleResponse, mailboxResponse] = await Promise.all([
        apiRequest("/api/forwarding/"),
        apiRequest("/api/mailboxes/"),
      ]);
      if (ruleResponse.ok) setRules(await ruleResponse.json());
      else {
        const data = await ruleResponse.json().catch(() => null);
        setLoadError(data?.detail ?? "Forwarding rules could not be loaded.");
      }

      if (mailboxResponse.ok) {
        const rows = (await mailboxResponse.json()) as Mailbox[];
        setMailboxes(rows);
        setMailboxId((current) =>
          rows.some((mailbox) => mailbox.id === current)
            ? current
            : rows[0]?.id || ""
        );
      }
    } catch {
      setLoadError("Forwarding rules could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    if (tenant?.id) {
      apiRequest(`/api/workspaces/${tenant.id}/stats/`)
        .then(async (response) => response.ok ? response.json() : null)
        .then((data) => {
          if (data?.my_role) setMyRole(data.my_role);
          if (data?.tenant_status) setWorkspaceStatus(data.tenant_status);
        })
        .catch(() => {});
    }
  }, [fetchAll, tenant?.id]);

  useEffect(() => {
    // Direct links from Global Search remain shareable and survive a reload.
    // The effect also handles Next.js soft navigation to a new query.
    setQuery(requestedSearch);
  }, [requestedSearch]);

  const filteredRules = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return rules;
    return rules.filter((rule) =>
      rule.source_mailbox_email.toLowerCase().includes(needle) ||
      rule.destination_email.toLowerCase().includes(needle) ||
      rule.status.toLowerCase().includes(needle)
    );
  }, [query, rules]);

  const canAdmin = myRole === "owner" || myRole === "admin";
  const canCreate = canAdmin && !!user?.email_verified && workspaceStatus === "active" && mailboxes.length > 0;

  async function createRule(event: React.FormEvent) {
    event.preventDefault();
    setAddErrors({});
    setMessage("");
    setAdding(true);
    try {
      const response = await apiRequest("/api/forwarding/", {
        method: "POST",
        body: JSON.stringify({
          source_mailbox_id: mailboxId,
          destination_email: destinationEmail.trim(),
          keep_copy: keepCopy,
        }),
      });
      const data = await response.json().catch(() => null);

      if (response.ok && data?.id) {
        setDestinationEmail("");
        setKeepCopy(true);
        setAddOpen(false);
        if (response.status === 202 || data.detail) {
          setMessageTone("warn");
          setMessage(data.detail ?? "Forwarding rule was saved, but Mail Engine activation is still pending.");
        } else {
          setMessageTone("success");
          setMessage("Forwarding rule created successfully.");
        }
        await fetchAll();
      } else {
        setAddErrors(typeof data === "object" && data !== null ? data : { detail: "Failed to create forwarding rule." });
      }
    } catch {
      setAddErrors({ detail: "Failed to create forwarding rule." });
    } finally {
      setAdding(false);
    }
  }

  async function changeStatus(rule: ForwardingRule, nextStatus: ForwardingRule["status"]) {
    if (!canAdmin || rule.status === nextStatus) return;
    setChangingStatus(rule.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/forwarding/${rule.id}/status/`, {
        method: "PATCH",
        body: JSON.stringify({ status: nextStatus }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setRules((current) => current.map((item) => item.id === rule.id ? data : item));
        setMessageTone("success");
        setMessage(`Forwarding rule is now ${data.status}.`);
      } else {
        setMessageTone("danger");
        setMessage(data?.detail ?? "Forwarding status could not be changed.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Forwarding status could not be changed.");
    } finally {
      setChangingStatus("");
    }
  }

  async function deleteRule(rule: ForwardingRule) {
    if (!canAdmin) return;
    setDeleting(rule.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/forwarding/${rule.id}/`, { method: "DELETE" });
      if (response.ok || response.status === 204) {
        setRules((current) => current.filter((item) => item.id !== rule.id));
        setDeleteTarget("");
        setMessageTone("success");
        setMessage("Forwarding rule removed successfully.");
      } else {
        const data = await response.json().catch(() => null);
        setMessageTone("danger");
        setMessage(data?.detail ?? "Forwarding rule could not be removed.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Forwarding rule could not be removed.");
    } finally {
      setDeleting("");
    }
  }

  return (
    <div className="portal-page astra-resource-page astra-routing-page">
      <PortalPageHeading
        title="Forwarding"
        description="Route incoming mail from a mailbox to the right destination."
        actions={
          <PortalButton
            type="button"
            disabled={!canCreate}
            onClick={() => {
              setAddOpen(true);
              setAddErrors({});
            }}
          >
            <Plus className="h-4 w-4" />
            Add forwarding rule
          </PortalButton>
        }
      />

      <div className="astra-resource-summary" aria-label="Forwarding statistics"><div><span>Forwarding rules</span><strong>{loading ? "—" : rules.length}</strong></div><div><span>Active routes</span><strong>{loading ? "—" : rules.filter((item) => item.status === "active").length}</strong></div><div><span>Keep local copy</span><strong>{loading ? "—" : rules.filter((item) => item.keep_copy).length}</strong></div></div>

      {!user?.email_verified && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Verify your account email before creating a new forwarding route.</span>
          </PortalNotice>
        </div>
      )}

      {workspaceStatus !== "active" && (
        <div className="mb-5">
          <PortalNotice tone="info">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Creating new forwarding routes is unavailable while this workspace is not active.</span>
          </PortalNotice>
        </div>
      )}

      {!loading && mailboxes.length === 0 && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              Create a mailbox before setting up forwarding.{" "}
              <Link href="/app/mailboxes" className="auth-text-button">Manage mailboxes</Link>
            </span>
          </PortalNotice>
        </div>
      )}

      {message && (
        <div className="mb-5"><PortalNotice tone={messageTone}>{message}</PortalNotice></div>
      )}

      <AstraResourceDialog
        open={addOpen}
        busy={adding}
        title="Add a forwarding rule"
        description="Forward incoming mail to a destination address; keep a local copy if needed."
        onDismiss={() => { setAddOpen(false); setAddErrors({}); }}
      >
          <form onSubmit={createRule}>
            {fieldError(addErrors.detail) && (
              <div className="mb-4"><PortalNotice tone="danger">{fieldError(addErrors.detail)}</PortalNotice></div>
            )}

            <div className="portal-form-grid">
              <div className="portal-field">
                <label htmlFor="astra-forwarding-source">Source mailbox</label>
                <select id="astra-forwarding-source" value={mailboxId} onChange={(event) => setMailboxId(event.target.value)} required>
                  {mailboxes.map((mailbox) => (
                    <option key={mailbox.id} value={mailbox.id}>{mailbox.email}</option>
                  ))}
                </select>
                {fieldError(addErrors.source_mailbox_id) && <div className="portal-field-error">{fieldError(addErrors.source_mailbox_id)}</div>}
              </div>

              <div className="portal-field">
                <label htmlFor="astra-forwarding-destination">Forward to</label>
                <input
                  id="astra-forwarding-destination"
                  type="email"
                  required
                  value={destinationEmail}
                  onChange={(event) => setDestinationEmail(event.target.value)}
                  placeholder="teammate@example.com"
                />
                {fieldError(addErrors.destination_email) && <div className="portal-field-error">{fieldError(addErrors.destination_email)}</div>}
              </div>

              <div className="full">
                <div className="portal-switch-row">
                  <div>
                    <strong>Keep a local copy</strong>
                    <p>Preserve forwarded messages in the source mailbox.</p>
                  </div>
                  <button
                    type="button"
                    className="portal-toggle"
                    data-on={keepCopy}
                    onClick={() => setKeepCopy((value) => !value)}
                    aria-label="Keep a local copy"
                  />
                </div>
              </div>
            </div>

            <div className="portal-detail-actions">
              <PortalButton type="submit" disabled={adding || !mailboxId || !destinationEmail.trim()}>
                {adding ? "Creating…" : "Create rule"}
              </PortalButton>
              <PortalButton
                type="button"
                variant="secondary"
                disabled={adding}
                onClick={() => {
                  setAddOpen(false);
                  setAddErrors({});
                }}
              >
                Cancel
              </PortalButton>
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
              placeholder="Search forwarding rules…"
              aria-label="Search forwarding rules"
            />
          </div>
          <button type="button" className="portal-icon-button" onClick={fetchAll} disabled={loading} aria-label="Refresh forwarding rules">
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
          </button>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : rules.length === 0 ? (
          <PortalEmptyState
            title="No forwarding rules yet"
            description="Route incoming messages from a mailbox to another address."
            action={canCreate ? <PortalButton type="button" onClick={() => setAddOpen(true)}><Plus className="h-4 w-4" />Add rule</PortalButton> : undefined}
          />
        ) : filteredRules.length === 0 ? (
          <PortalEmptyState title="No matching forwarding rules" description="Try a different search term." />
        ) : (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Source mailbox</th>
                  <th>Forward to</th>
                  <th>Local copy</th>
                  <th>Status</th>
                  <th>Mail service</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRules.map((rule) => (
                  <tr key={rule.id}>
                    <td>
                      <div className="portal-route-address">
                        <span className="portal-domain-symbol"><CornerUpRight className="h-4 w-4" /></span>
                        <code>{rule.source_mailbox_email}</code>
                      </div>
                    </td>
                    <td>
                      <span className="portal-destination-pill">
                        <Mail className="h-3 w-3" />
                        <span>{rule.destination_email}</span>
                      </span>
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1.5 text-[10px] text-[var(--portal-muted)]">
                        {rule.keep_copy && <Check className="h-3.5 w-3.5 text-[var(--portal-success)]" />}
                        {rule.keep_copy ? "Kept in mailbox" : "Not kept"}
                      </span>
                    </td>
                    <td><PortalStatus value={rule.status} /></td>
                    <td>
                      <span className={"portal-service-state " + (rule.mail_service_ready ? "ready" : "waiting")}>
                        {rule.mail_service_ready
                          ? <CheckCircle2 className="h-3.5 w-3.5" />
                          : <AlertCircle className="h-3.5 w-3.5" />}
                        {rule.mail_service_ready ? "Ready" : "Not ready"}
                      </span>
                    </td>
                    <td>
                      <div className="portal-inline-actions">
                        <select
                          className="portal-input !h-[31px] !min-h-[31px] !w-[102px] !py-0 text-[9px]"
                          value={rule.status}
                          onChange={(event) => changeStatus(rule, event.target.value as ForwardingRule["status"])}
                          disabled={!canAdmin || changingStatus === rule.id}
                          aria-label={`Status for forwarding from ${rule.source_mailbox_email}`}
                        >
                          <option value="active">Active</option>
                          <option value="paused">Paused</option>
                          <option value="disabled">Disabled</option>
                        </select>
                        <button
                          type="button"
                          className="portal-action-button danger"
                          onClick={() => setDeleteTarget(rule.id)}
                          disabled={!canAdmin || deleting === rule.id}
                          aria-label={`Delete forwarding from ${rule.source_mailbox_email}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      {deleteTarget === rule.id && (
                        <div className="portal-confirm-inline">
                          <PortalNotice tone="warn">
                            <div className="flex-1">
                              Remove forwarding from <strong>{rule.source_mailbox_email}</strong> to <strong>{rule.destination_email}</strong>?
                            </div>
                            <div className="flex gap-2">
                              <PortalButton type="button" variant="secondary" onClick={() => setDeleteTarget("")} disabled={deleting === rule.id}>Cancel</PortalButton>
                              <PortalButton type="button" variant="danger" onClick={() => deleteRule(rule)} disabled={deleting === rule.id}>
                                {deleting === rule.id ? "Removing…" : "Remove"}
                              </PortalButton>
                            </div>
                          </PortalNotice>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </PortalCard>

      <div className="mt-4">
        <PortalNotice tone="info">
          <CornerUpRight className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Forwarding is resolved at the mail-service layer. <strong>Active</strong> routes mail, <strong>Paused</strong> temporarily removes the route while preserving the rule, and <strong>Disabled</strong> keeps it off. The current backend does not provide an edit endpoint for destination or local-copy changes.
          </span>
        </PortalNotice>
      </div>
    </div>
  );
}

/** Suspense boundary required by Next.js useSearchParams during static builds. */
export default function ForwardingPage() {
  return <Suspense fallback={null}><ForwardingPageContent /></Suspense>;
}
