"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  AlertCircle,
  AtSign,
  CheckCircle2,
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

interface Domain {
  id: string;
  domain: string;
  ownership_verified: boolean;
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
  destination_mailbox: string;
  destination_email: string;
  status: "active" | "disabled";
  mail_service_ready: boolean;
  created_at: string;
}

function fieldError(value: unknown) {
  if (!value) return "";
  if (Array.isArray(value)) return value.map(String).join(" ");
  return String(value);
}

function AliasesPageContent() {
  const routeParams = useSearchParams();
  const requestedSearch = routeParams.get("q") || "";
  const { user, tenant } = useAuth();
  const [aliases, setAliases] = useState<Alias[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [myRole, setMyRole] = useState("");
  const [workspaceStatus, setWorkspaceStatus] = useState(tenant?.status || "");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "warn" | "danger">("success");
  const [addOpen, setAddOpen] = useState(false);

  const [localPart, setLocalPart] = useState("");
  const [domainId, setDomainId] = useState("");
  const [destinationMailboxId, setDestinationMailboxId] = useState("");
  const [addErrors, setAddErrors] = useState<Record<string, unknown>>({});
  const [adding, setAdding] = useState(false);
  const [toggling, setToggling] = useState("");
  const [deleteTarget, setDeleteTarget] = useState("");
  const [deleting, setDeleting] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [aliasResponse, domainResponse, mailboxResponse] = await Promise.all([
        apiRequest("/api/aliases/"),
        apiRequest("/api/domains/"),
        apiRequest("/api/mailboxes/"),
      ]);

      if (aliasResponse.ok) setAliases(await aliasResponse.json());
      else {
        const data = await aliasResponse.json().catch(() => null);
        setLoadError(data?.detail ?? "Aliases could not be loaded.");
      }

      if (domainResponse.ok) {
        const allDomains = (await domainResponse.json()) as Domain[];
        const verifiedDomains = allDomains.filter((domain) => domain.ownership_verified);
        setDomains(verifiedDomains);
        setDomainId((current) =>
          verifiedDomains.some((domain) => domain.id === current)
            ? current
            : verifiedDomains[0]?.id || ""
        );
      }

      if (mailboxResponse.ok) {
        const rows = (await mailboxResponse.json()) as Mailbox[];
        setMailboxes(rows);
        setDestinationMailboxId((current) =>
          rows.some((mailbox) => mailbox.id === current)
            ? current
            : rows[0]?.id || ""
        );
      }
    } catch {
      setLoadError("Aliases could not be loaded.");
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
    // Sync incoming query after mount (no render-cascade state update).
    const id = window.setTimeout(() => setQuery(requestedSearch), 0);
    return () => window.clearTimeout(id);
  }, [requestedSearch]);

  const filteredAliases = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return aliases;
    return aliases.filter((alias) =>
      alias.source_address.toLowerCase().includes(needle) ||
      alias.destination_email.toLowerCase().includes(needle) ||
      alias.status.toLowerCase().includes(needle)
    );
  }, [aliases, query]);

  const canAdmin = myRole === "owner" || myRole === "admin";
  const canCreate = canAdmin && !!user?.email_verified && workspaceStatus === "active" && domains.length > 0 && mailboxes.length > 0;

  async function createAlias(event: React.FormEvent) {
    event.preventDefault();
    setAddErrors({});
    setMessage("");
    setAdding(true);
    try {
      const body: Record<string, unknown> = {
        source_local_part: localPart.trim().toLowerCase(),
        domain_id: domainId,
        destination_mailbox_id: destinationMailboxId,
      };

      const response = await apiRequest("/api/aliases/", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => null);

      if (response.ok && data?.id) {
        setLocalPart("");
        setAddOpen(false);
        if (response.status === 202 || data.detail) {
          setMessageTone("warn");
          setMessage(data.detail ?? "Alias was created, but Mail Engine activation is still pending.");
        } else {
          setMessageTone("success");
          setMessage("Alias created successfully.");
        }
        await fetchAll();
      } else {
        setAddErrors(typeof data === "object" && data !== null ? data : { detail: "Failed to create alias." });
      }
    } catch {
      setAddErrors({ detail: "Failed to create alias." });
    } finally {
      setAdding(false);
    }
  }

  async function toggleStatus(alias: Alias) {
    if (!canAdmin) return;
    setToggling(alias.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/aliases/${alias.id}/status/`, {
        method: "PATCH",
        body: JSON.stringify({ status: alias.status === "active" ? "disabled" : "active" }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setAliases((current) => current.map((item) => item.id === alias.id ? data : item));
        setMessageTone("success");
        setMessage(`Alias ${data.status === "active" ? "enabled" : "disabled"} successfully.`);
      } else {
        setMessageTone("danger");
        setMessage(data?.detail ?? "Alias status could not be changed.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Alias status could not be changed.");
    } finally {
      setToggling("");
    }
  }

  async function deleteAlias(alias: Alias) {
    if (!canAdmin) return;
    setDeleting(alias.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/aliases/${alias.id}/`, { method: "DELETE" });
      if (response.ok || response.status === 204) {
        setAliases((current) => current.filter((item) => item.id !== alias.id));
        setDeleteTarget("");
        setMessageTone("success");
        setMessage("Alias removed successfully.");
      } else {
        const data = await response.json().catch(() => null);
        setMessageTone("danger");
        setMessage(data?.detail ?? "Alias could not be removed.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Alias could not be removed.");
    } finally {
      setDeleting("");
    }
  }

  return (
    <div className="portal-page astra-resource-page astra-routing-page">
      <PortalPageHeading
        title="Aliases"
        description="More ways to reach your team without creating another mailbox."
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
            Create alias
          </PortalButton>
        }
      />

      <div className="astra-resource-summary" aria-label="Alias statistics"><div><span>Aliases</span><strong>{loading ? "—" : aliases.length}</strong></div><div><span>Active aliases</span><strong>{loading ? "—" : aliases.filter((item) => item.status === "active").length}</strong></div><div><span>Engine ready</span><strong>{loading ? "—" : aliases.filter((item) => item.mail_service_ready).length}</strong></div></div>

      {!user?.email_verified && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Verify your account email before provisioning new aliases.</span>
          </PortalNotice>
        </div>
      )}

      {workspaceStatus !== "active" && (
        <div className="mb-5">
          <PortalNotice tone="info">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Alias creation is unavailable while this workspace is not active.</span>
          </PortalNotice>
        </div>
      )}

      {!loading && domains.length === 0 && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              A verified domain is required before creating aliases.{" "}
              <Link href="/app/domains" className="auth-text-button">Review domains</Link>
            </span>
          </PortalNotice>
        </div>
      )}

      {!loading && domains.length > 0 && mailboxes.length === 0 && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              Create a mailbox before adding an alias.{" "}
              <Link href="/app/mailboxes" className="auth-text-button">Create mailbox</Link>
            </span>
          </PortalNotice>
        </div>
      )}

      {message && (
        <div className="mb-5">
          <PortalNotice tone={messageTone}>{message}</PortalNotice>
        </div>
      )}

      <AstraResourceDialog
        open={addOpen}
        busy={adding}
        title="Create an alias"
        description="An alias delivers to one existing MateMail mailbox. It does not create a new mailbox."
        onDismiss={() => { setAddOpen(false); setAddErrors({}); }}
      >
          <form onSubmit={createAlias}>
            {fieldError(addErrors.detail) && (
              <div className="mb-4"><PortalNotice tone="danger">{fieldError(addErrors.detail)}</PortalNotice></div>
            )}

            <div className="portal-form-grid">
              <div className="portal-field full">
                <label htmlFor="astra-alias-local">Alias address</label>
                <div className="portal-address-composer">
                  <input
                    id="astra-alias-local"
                    type="text"
                    required
                    pattern="[a-zA-Z0-9._+-]+"
                    value={localPart}
                    onChange={(event) => setLocalPart(event.target.value)}
                    placeholder="sales"
                  />
                  <span>@</span>
                  <select aria-label="Alias domain" value={domainId} onChange={(event) => setDomainId(event.target.value)} required>
                    {domains.map((domain) => (
                      <option key={domain.id} value={domain.id}>{domain.domain}</option>
                    ))}
                  </select>
                </div>
                {fieldError(addErrors.source_local_part) && <div className="portal-field-error">{fieldError(addErrors.source_local_part)}</div>}
                {fieldError(addErrors.domain_id) && <div className="portal-field-error">{fieldError(addErrors.domain_id)}</div>}
              </div>

              <div className="portal-field full">
                <label htmlFor="astra-alias-mailbox">Mailbox</label>
                {mailboxes.length ? (
                  <select
                    id="astra-alias-mailbox"
                    value={destinationMailboxId}
                    onChange={(event) => setDestinationMailboxId(event.target.value)}
                    required
                  >
                    {mailboxes.map((mailbox) => (
                      <option key={mailbox.id} value={mailbox.id}>{mailbox.email}</option>
                    ))}
                  </select>
                ) : (
                  <PortalNotice tone="warn">
                    No mailboxes are available.{" "}
                    <Link href="/app/mailboxes" className="auth-text-button">Create a mailbox</Link>
                  </PortalNotice>
                )}
                {fieldError(addErrors.destination_mailbox_id) && <div className="portal-field-error">{fieldError(addErrors.destination_mailbox_id)}</div>}
              </div>
            </div>

            <div className="portal-detail-actions">
              <PortalButton
                type="submit"
                disabled={
                  adding ||
                  !localPart.trim() ||
                  !domainId ||
                  !destinationMailboxId
                }
              >
                {adding ? "Creating…" : "Create alias"}
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
              placeholder="Search aliases…"
              aria-label="Search aliases"
            />
          </div>
          <button type="button" className="portal-icon-button" onClick={fetchAll} disabled={loading} aria-label="Refresh aliases">
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
          </button>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : aliases.length === 0 ? (
          <PortalEmptyState
            title="No aliases yet"
            description="Create an additional address for an existing mailbox."
            action={canCreate ? <PortalButton type="button" onClick={() => setAddOpen(true)}><Plus className="h-4 w-4" />Create alias</PortalButton> : undefined}
          />
        ) : filteredAliases.length === 0 ? (
          <PortalEmptyState title="No matching aliases" description="Try a different search term." />
        ) : (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Alias address</th>
                  <th>Delivers to</th>
                  <th>Status</th>
                  <th>Mail service</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredAliases.map((alias) => (
                  <tr key={alias.id}>
                    <td>
                      <div className="portal-route-address">
                        <span className="portal-domain-symbol"><AtSign className="h-4 w-4" /></span>
                        <code>{alias.source_address}</code>
                      </div>
                    </td>
                    <td>
                      <span className="portal-destination-pill">
                        <Mail className="h-3 w-3" />
                        <span>{alias.destination_email}</span>
                      </span>
                    </td>
                    <td><PortalStatus value={alias.status} /></td>
                    <td>
                      <span className={"portal-service-state " + (alias.mail_service_ready ? "ready" : "waiting")}>
                        {alias.mail_service_ready
                          ? <CheckCircle2 className="h-3.5 w-3.5" />
                          : <AlertCircle className="h-3.5 w-3.5" />}
                        {alias.mail_service_ready ? "Ready" : "Not ready"}
                      </span>
                    </td>
                    <td>
                      <div className="portal-inline-actions">
                        <button
                          type="button"
                          className="portal-toggle"
                          data-on={alias.status === "active"}
                          onClick={() => toggleStatus(alias)}
                          disabled={!canAdmin || toggling === alias.id}
                          aria-label={alias.status === "active" ? `Disable ${alias.source_address}` : `Enable ${alias.source_address}`}
                        />
                        <button
                          type="button"
                          className="portal-action-button danger"
                          onClick={() => setDeleteTarget(alias.id)}
                          disabled={!canAdmin || deleting === alias.id}
                          aria-label={`Delete ${alias.source_address}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      {deleteTarget === alias.id && (
                        <div className="portal-confirm-inline">
                          <PortalNotice tone="warn">
                            <div className="flex-1">Remove <strong>{alias.source_address}</strong>?</div>
                            <div className="flex gap-2">
                              <PortalButton type="button" variant="secondary" onClick={() => setDeleteTarget("")} disabled={deleting === alias.id}>Cancel</PortalButton>
                              <PortalButton type="button" variant="danger" onClick={() => deleteAlias(alias)} disabled={deleting === alias.id}>
                                {deleting === alias.id ? "Removing…" : "Remove"}
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
          <AtSign className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Aliases do not have their own login or storage. Each alias belongs to one MateMail mailbox. Use Forwarding when mail should be delivered to an external address.</span>
        </PortalNotice>
      </div>
    </div>
  );
}

/** Suspense boundary required by Next.js useSearchParams during static builds. */
export default function AliasesPage() {
  return <Suspense fallback={null}><AliasesPageContent /></Suspense>;
}
