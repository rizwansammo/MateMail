"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  HardDrive,
  Mail,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { AstraResourceDialog } from "@/components/workspace/astra-resource-dialog";
import { api, ApiError, apiRequest } from "@/lib/api";
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

interface BillingData {
  subscription: {
    plan: {
      display_name: string;
      default_storage_per_mailbox_mb: number;
      max_storage_per_mailbox_mb: number;
    };
  } | null;
}

interface WorkspaceStats {
  my_role: string;
  tenant_status: string;
}

interface Mailbox {
  id: string;
  email: string;
  full_name: string;
  local_part: string;
  domain: string;
  domain_name: string;
  status: "active" | "disabled" | "suspended";
  kind: "personal" | "team_box";
  quota_mb: number;
  storage_used_mb: number;
  mail_service_ready: boolean;
  mail_service_message: string;
  last_login: string | null;
  created_at: string;
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

function errorText(value: unknown) {
  if (!value) return "";
  if (Array.isArray(value)) return value.map(String).join(" ");
  return String(value);
}

export default function MailboxesPage() {
  const router = useRouter();
  const { user, tenant } = useAuth();
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [myRole, setMyRole] = useState("");
  const [workspaceStatus, setWorkspaceStatus] = useState(tenant?.status || "");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [addOpen, setAddOpen] = useState(false);

  const [localPart, setLocalPart] = useState("");
  const [domainId, setDomainId] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [quotaMb, setQuotaMb] = useState(1024);
  const [quotaDefaultMb, setQuotaDefaultMb] = useState(1024);
  const [quotaMaxMb, setQuotaMaxMb] = useState(1024);
  const [planName, setPlanName] = useState("");
  const [addErrors, setAddErrors] = useState<Record<string, unknown>>({});
  const [adding, setAdding] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const requests = [
        apiRequest("/api/mailboxes/"),
        apiRequest("/api/domains/"),
        apiRequest("/api/billing/"),
      ] as const;
      const [mailboxResponse, domainResponse, billingResponse] = await Promise.all(requests);

      if (mailboxResponse.ok) {
        const rows = (await mailboxResponse.json()) as Mailbox[];
        setMailboxes(rows.filter((mailbox) => mailbox.kind === "personal"));
      } else {
        const data = await mailboxResponse.json().catch(() => null);
        setLoadError(data?.detail ?? "Mailboxes could not be loaded.");
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

      if (billingResponse.ok) {
        const billing = (await billingResponse.json()) as BillingData;
        const plan = billing.subscription?.plan;
        if (plan) {
          const defaultMb = Number(plan.default_storage_per_mailbox_mb) || 1024;
          const maxMb = Number(plan.max_storage_per_mailbox_mb) || defaultMb;
          setQuotaDefaultMb(defaultMb);
          setQuotaMaxMb(maxMb);
          setQuotaMb((current) => Math.min(current || defaultMb, maxMb));
          setPlanName(plan.display_name);
        }
      }
    } catch {
      setLoadError("Mailboxes could not be loaded.");
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

  const filteredMailboxes = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return mailboxes;
    return mailboxes.filter((mailbox) =>
      mailbox.email.toLowerCase().includes(needle) ||
      mailbox.full_name.toLowerCase().includes(needle) ||
      mailbox.status.toLowerCase().includes(needle)
    );
  }, [mailboxes, query]);

  const canAdmin = myRole === "owner" || myRole === "admin";
  const canCreate =
    canAdmin &&
    !!user?.email_verified &&
    workspaceStatus === "active" &&
    domains.length > 0;

  async function handleAdd(event: React.FormEvent) {
    event.preventDefault();
    setAddErrors({});
    setAdding(true);
    try {
      await api.post("/api/mailboxes/", {
        local_part: localPart.trim().toLowerCase(),
        domain_id: domainId,
        full_name: fullName.trim(),
        quota_mb: quotaMb,
        password,
      });
      setLocalPart("");
      setFullName("");
      setPassword("");
      setQuotaMb(quotaDefaultMb);
      setAddOpen(false);
      window.dispatchEvent(new Event("matemail:workspace-onboarding-updated"));
      await fetchAll();
    } catch (caught) {
      if (caught instanceof ApiError) {
        try {
          const body = JSON.parse(caught.message);
          setAddErrors(typeof body === "object" && body !== null ? body : { detail: caught.message });
        } catch {
          setAddErrors({ detail: caught.message || "Failed to create mailbox." });
        }
      } else {
        setAddErrors({ detail: "Failed to create mailbox. Please try again." });
      }
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="portal-page astra-resource-page">
      <PortalPageHeading
        title="Mailboxes"
        description="Manage your team’s email identities, access and storage."
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
            Create mailbox
          </PortalButton>
        }
      />

      <div className="astra-resource-summary" aria-label="Mailbox statistics">
        <div><span>Personal mailboxes</span><strong>{loading ? "—" : mailboxes.length}</strong></div>
        <div><span>Active accounts</span><strong>{loading ? "—" : mailboxes.filter((item) => item.status === "active").length}</strong></div>
        <div><span>Mail service ready</span><strong>{loading ? "—" : mailboxes.filter((item) => item.mail_service_ready).length}</strong></div>
      </div>

      {!user?.email_verified && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span><strong>Email verification required.</strong> Verify your account before provisioning mailboxes.</span>
          </PortalNotice>
        </div>
      )}

      {user?.email_verified && workspaceStatus !== "active" && (
        <div className="mb-5">
          <PortalNotice tone="info">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span><strong>Workspace approval is still pending or unavailable.</strong> Mailbox provisioning remains blocked by backend policy.</span>
          </PortalNotice>
        </div>
      )}

      {!loading && domains.length === 0 && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <strong>A verified domain is required.</strong>{" "}
              <Link href="/app/domains" className="auth-text-button">Review domains</Link>
            </span>
          </PortalNotice>
        </div>
      )}

      <AstraResourceDialog
        open={addOpen}
        busy={adding}
        title="Create a mailbox"
        description="Set up a dedicated email identity. Passwords are sent to the Mail Engine and are not stored by MateMail."
        onDismiss={() => { setAddOpen(false); setAddErrors({}); setPassword(""); }}
      >
          <form onSubmit={handleAdd}>
            {errorText(addErrors.detail) && (
              <div className="mb-4">
                <PortalNotice tone="danger">{errorText(addErrors.detail)}</PortalNotice>
              </div>
            )}

            <div className="portal-form-grid">
              <div className="portal-field full">
                <label htmlFor="astra-mailbox-local-part">Email address</label>
                <div className="portal-address-composer">
                  <input
                    id="astra-mailbox-local-part"
                    type="text"
                    required
                    pattern="[a-zA-Z0-9._+-]+"
                    value={localPart}
                    onChange={(event) => setLocalPart(event.target.value)}
                    placeholder="amelia"
                  />
                  <span>@</span>
                  <select value={domainId} onChange={(event) => setDomainId(event.target.value)} required>
                    {domains.map((domain) => (
                      <option key={domain.id} value={domain.id}>{domain.domain}</option>
                    ))}
                  </select>
                </div>
                {errorText(addErrors.local_part) && <div className="portal-field-error">{errorText(addErrors.local_part)}</div>}
                {errorText(addErrors.domain_id) && <div className="portal-field-error">{errorText(addErrors.domain_id)}</div>}
              </div>

              <div className="portal-field">
                <label htmlFor="astra-mailbox-display-name">Display name</label>
                <input
                  id="astra-mailbox-display-name"
                  type="text"
                  required
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  placeholder="Amelia Chen"
                />
                {errorText(addErrors.full_name) && <div className="portal-field-error">{errorText(addErrors.full_name)}</div>}
              </div>

              <div className="portal-field">
                <label htmlFor="astra-mailbox-password">Temporary password</label>
                <input
                  id="astra-mailbox-password"
                  type="password"
                  autoComplete="new-password"
                  minLength={10}
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="At least 10 characters"
                />
                <div className="portal-field-hint">The backend validates the password and sends it directly to the Mail Engine.</div>
                {errorText(addErrors.password) && <div className="portal-field-error">{errorText(addErrors.password)}</div>}
              </div>

              <div className="portal-field full">
                <label htmlFor="astra-mailbox-quota">
                  Storage quota — {formatStorage(quotaMb)}
                  {planName ? ` · ${planName} maximum ${formatStorage(quotaMaxMb)}` : ""}
                </label>
                <input
                  id="astra-mailbox-quota"
                  type="range"
                  min={Math.min(1024, quotaMaxMb)}
                  max={quotaMaxMb}
                  step={1024}
                  value={Math.min(quotaMb, quotaMaxMb)}
                  onChange={(event) => setQuotaMb(Number(event.target.value))}
                />
                <div className="portal-field-hint">
                  The backend enforces the plan ceiling; the requested value cannot exceed the subscription limit.
                </div>
                {errorText(addErrors.quota_mb) && <div className="portal-field-error">{errorText(addErrors.quota_mb)}</div>}
              </div>
            </div>

            <div className="portal-detail-actions">
              <PortalButton
                type="submit"
                disabled={
                  adding ||
                  !localPart.trim() ||
                  !fullName.trim() ||
                  password.length < 10 ||
                  !domainId
                }
              >
                {adding ? "Creating…" : "Create mailbox"}
              </PortalButton>
              <PortalButton
                type="button"
                variant="secondary"
                disabled={adding}
                onClick={() => {
                  setAddOpen(false);
                  setAddErrors({});
                  setPassword("");
                }}
              >
                Cancel
              </PortalButton>
            </div>
          </form>
      </AstraResourceDialog>

      {loadError && (
        <div className="mb-5">
          <PortalNotice tone="danger">{loadError}</PortalNotice>
        </div>
      )}

      <PortalCard className="portal-management-card" bodyClassName="!p-0">
        <div className="portal-management-toolbar">
          <div className="portal-management-search">
            <Search className="h-4 w-4" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search mailboxes…"
              aria-label="Search mailboxes"
            />
          </div>
          <button
            type="button"
            className="portal-icon-button"
            onClick={fetchAll}
            disabled={loading}
            aria-label="Refresh mailboxes"
          >
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
          </button>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : mailboxes.length === 0 ? (
          <PortalEmptyState
            title="Your first mailbox starts here"
            description="Create a mailbox on a verified domain to begin sending and receiving mail."
            action={
              canCreate ? (
                <PortalButton type="button" onClick={() => setAddOpen(true)}>
                  <Plus className="h-4 w-4" />
                  Create mailbox
                </PortalButton>
              ) : undefined
            }
          />
        ) : filteredMailboxes.length === 0 ? (
          <PortalEmptyState title="No matching mailboxes" description="Try a different search term." />
        ) : (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Mailbox</th>
                  <th>Status</th>
                  <th>Storage</th>
                  <th>Mail service</th>
                  <th>Last login</th>
                </tr>
              </thead>
              <tbody>
                {filteredMailboxes.map((mailbox) => {
                  const usage = mailbox.quota_mb
                    ? Math.min(100, Math.round((mailbox.storage_used_mb / mailbox.quota_mb) * 100))
                    : 0;
                  return (
                    <tr key={mailbox.id} className="cursor-pointer" onClick={() => router.push(`/app/mailboxes/${mailbox.id}`)}>
                      <td>
                        <div className="portal-identity-cell">
                          <span className="portal-avatar">{initials(mailbox.full_name || mailbox.email)}</span>
                          <span>
                            <strong>{mailbox.full_name || mailbox.email}</strong>
                            <small>{mailbox.email}</small>
                          </span>
                        </div>
                      </td>
                      <td><PortalStatus value={mailbox.status} /></td>
                      <td>
                        <div className="min-w-[140px]">
                          <div className="portal-storage-head">
                            <span>{formatStorage(mailbox.storage_used_mb)}</span>
                            <span>{formatStorage(mailbox.quota_mb)}</span>
                          </div>
                          <div className="portal-storage-meter"><span style={{ width: `${usage}%` }} /></div>
                        </div>
                      </td>
                      <td>
                        <span className={"portal-service-state " + (mailbox.mail_service_ready ? "ready" : "waiting")}>
                          {mailbox.mail_service_ready
                            ? <CheckCircle2 className="h-3.5 w-3.5" />
                            : <AlertCircle className="h-3.5 w-3.5" />}
                          {mailbox.mail_service_ready ? "Ready" : "Needs attention"}
                        </span>
                      </td>
                      <td>{mailbox.last_login ? new Date(mailbox.last_login).toLocaleDateString() : "Never"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </PortalCard>

      <div className="mt-4">
        <PortalNotice tone="info">
          <HardDrive className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Mailbox storage quotas are enforced by your plan. Changing an existing mailbox’s name or quota is not available in Hub yet.</span>
        </PortalNotice>
      </div>
    </div>
  );
}
