"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  Inbox,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Users,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
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

interface BillingData {
  subscription: {
    plan: {
      display_name: string;
      default_storage_per_mailbox_mb: number;
      max_storage_per_mailbox_mb: number;
    };
  } | null;
}

interface TeamBox {
  id: string;
  email: string;
  full_name: string;
  local_part: string;
  domain: string;
  domain_name: string;
  kind: "team_box";
  status: "active" | "disabled" | "suspended";
  quota_mb: number;
  storage_used_mb: number;
  member_count: number;
  mail_service_ready: boolean;
  mail_service_message: string;
  created_at: string;
}

function formatStorage(mb: number) {
  if (mb >= 1024) {
    const gb = mb / 1024;
    return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`;
  }
  return `${mb} MB`;
}

function fieldError(value: unknown) {
  if (!value) return "";
  if (Array.isArray(value)) return value.map(String).join(" ");
  return String(value);
}

export default function TeamBoxesPage() {
  const router = useRouter();
  const { user, tenant } = useAuth();
  const [teamBoxes, setTeamBoxes] = useState<TeamBox[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [myRole, setMyRole] = useState("");
  const [workspaceStatus, setWorkspaceStatus] = useState(tenant?.status || "");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [localPart, setLocalPart] = useState("");
  const [domainId, setDomainId] = useState("");
  const [displayName, setDisplayName] = useState("");
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
      const [teamBoxResponse, domainResponse, billingResponse] = await Promise.all([
        apiRequest("/api/team-boxes/"),
        apiRequest("/api/domains/"),
        apiRequest("/api/billing/"),
      ]);

      if (teamBoxResponse.ok) setTeamBoxes(await teamBoxResponse.json());
      else {
        const data = await teamBoxResponse.json().catch(() => null);
        setLoadError(data?.detail ?? "TeamBoxes could not be loaded.");
      }

      if (domainResponse.ok) {
        const rows = (await domainResponse.json()) as Domain[];
        const verified = rows.filter((domain) => domain.ownership_verified);
        setDomains(verified);
        setDomainId((current) =>
          verified.some((domain) => domain.id === current)
            ? current
            : verified[0]?.id || ""
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
      setLoadError("TeamBoxes could not be loaded.");
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

  const canAdmin = myRole === "owner" || myRole === "admin";
  const canCreate =
    canAdmin &&
    !!user?.email_verified &&
    workspaceStatus === "active" &&
    domains.length > 0;

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return teamBoxes;
    return teamBoxes.filter((teamBox) =>
      teamBox.email.toLowerCase().includes(needle) ||
      teamBox.full_name.toLowerCase().includes(needle) ||
      teamBox.status.toLowerCase().includes(needle)
    );
  }, [query, teamBoxes]);

  async function createTeamBox(event: React.FormEvent) {
    event.preventDefault();
    setAddErrors({});
    setAdding(true);
    try {
      const response = await apiRequest("/api/team-boxes/", {
        method: "POST",
        body: JSON.stringify({
          local_part: localPart.trim().toLowerCase(),
          domain_id: domainId,
          display_name: displayName.trim(),
          quota_mb: quotaMb,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setAddErrors(
          typeof data === "object" && data !== null
            ? data
            : { detail: "Failed to create TeamBox." }
        );
        return;
      }

      setLocalPart("");
      setDisplayName("");
      setQuotaMb(quotaDefaultMb);
      setAddOpen(false);
      await fetchAll();
      if (data?.id) router.push(`/app/team-boxes/${data.id}`);
    } catch {
      setAddErrors({ detail: "Failed to create TeamBox. Please try again." });
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="portal-page">
      <PortalPageHeading
        title="TeamBoxes"
        description="Shared mailboxes for teams, managed without shared passwords."
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
            Create TeamBox
          </PortalButton>
        }
      />

      {!user?.email_verified && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Verify your account email before provisioning a TeamBox.</span>
          </PortalNotice>
        </div>
      )}

      {user?.email_verified && workspaceStatus !== "active" && (
        <div className="mb-5">
          <PortalNotice tone="info">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>TeamBox provisioning is unavailable while this workspace is not active.</span>
          </PortalNotice>
        </div>
      )}

      {!loading && domains.length === 0 && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              A verified domain is required.{" "}
              <Link href="/app/domains" className="auth-text-button">Review domains</Link>
            </span>
          </PortalNotice>
        </div>
      )}

      {addOpen && (
        <PortalCard
          className="portal-form-card"
          title="Create a TeamBox"
          subtitle="TeamBoxes store mail like a mailbox, but members access them through their own PostBox identity. No shared password is created."
        >
          <form onSubmit={createTeamBox}>
            {fieldError(addErrors.detail) && (
              <div className="mb-4">
                <PortalNotice tone="danger">{fieldError(addErrors.detail)}</PortalNotice>
              </div>
            )}
            <div className="portal-form-grid">
              <div className="portal-field full">
                <label>TeamBox address</label>
                <div className="portal-address-composer">
                  <input
                    type="text"
                    required
                    pattern="[a-zA-Z0-9._+-]+"
                    value={localPart}
                    onChange={(event) => setLocalPart(event.target.value)}
                    placeholder="support"
                  />
                  <span>@</span>
                  <select value={domainId} onChange={(event) => setDomainId(event.target.value)} required>
                    {domains.map((domain) => (
                      <option key={domain.id} value={domain.id}>{domain.domain}</option>
                    ))}
                  </select>
                </div>
                {fieldError(addErrors.local_part) && <div className="portal-field-error">{fieldError(addErrors.local_part)}</div>}
                {fieldError(addErrors.domain_id) && <div className="portal-field-error">{fieldError(addErrors.domain_id)}</div>}
              </div>

              <div className="portal-field full">
                <label>Display name</label>
                <input
                  type="text"
                  required
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="Customer Support"
                />
                {fieldError(addErrors.display_name) && <div className="portal-field-error">{fieldError(addErrors.display_name)}</div>}
              </div>

              <div className="portal-field full">
                <label>
                  Storage quota — {formatStorage(quotaMb)}
                  {planName ? ` · ${planName} maximum ${formatStorage(quotaMaxMb)}` : ""}
                </label>
                <input
                  type="range"
                  min={Math.min(1024, quotaMaxMb)}
                  max={quotaMaxMb}
                  step={1024}
                  value={Math.min(quotaMb, quotaMaxMb)}
                  onChange={(event) => setQuotaMb(Number(event.target.value))}
                />
                {fieldError(addErrors.quota_mb) && <div className="portal-field-error">{fieldError(addErrors.quota_mb)}</div>}
              </div>
            </div>

            <div className="portal-detail-actions">
              <PortalButton
                type="submit"
                disabled={adding || !localPart.trim() || !displayName.trim() || !domainId}
              >
                {adding ? "Creating…" : "Create TeamBox"}
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
              placeholder="Search TeamBoxes…"
              aria-label="Search TeamBoxes"
            />
          </div>
          <button
            type="button"
            className="portal-icon-button"
            onClick={fetchAll}
            disabled={loading}
            aria-label="Refresh TeamBoxes"
          >
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
          </button>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : teamBoxes.length === 0 ? (
          <PortalEmptyState
            title="No TeamBoxes yet"
            description="Create a shared mailbox such as support@ or accounts@ and then choose who can access and send from it."
            action={canCreate ? (
              <PortalButton type="button" onClick={() => setAddOpen(true)}>
                <Plus className="h-4 w-4" />
                Create TeamBox
              </PortalButton>
            ) : undefined}
          />
        ) : filtered.length === 0 ? (
          <PortalEmptyState title="No matching TeamBoxes" description="Try a different search term." />
        ) : (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>TeamBox</th>
                  <th>Members</th>
                  <th>Status</th>
                  <th>Storage</th>
                  <th>Mail service</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((teamBox) => (
                  <tr
                    key={teamBox.id}
                    className="cursor-pointer"
                    onClick={() => router.push(`/app/team-boxes/${teamBox.id}`)}
                  >
                    <td>
                      <div className="portal-identity-cell">
                        <span className="portal-avatar"><Inbox className="h-4 w-4" /></span>
                        <span>
                          <strong>{teamBox.full_name}</strong>
                          <small>{teamBox.email}</small>
                        </span>
                      </div>
                    </td>
                    <td>
                      <span className="portal-destination-pill">
                        <Users className="h-3 w-3" />
                        {teamBox.member_count}
                      </span>
                    </td>
                    <td><PortalStatus value={teamBox.status} /></td>
                    <td>{formatStorage(teamBox.storage_used_mb)} / {formatStorage(teamBox.quota_mb)}</td>
                    <td>
                      <span className={"portal-service-state " + (teamBox.mail_service_ready ? "ready" : "waiting")}>
                        {teamBox.mail_service_ready
                          ? <CheckCircle2 className="h-3.5 w-3.5" />
                          : <AlertCircle className="h-3.5 w-3.5" />}
                        {teamBox.mail_service_ready ? "Ready" : "Needs attention"}
                      </span>
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
          <Inbox className="mt-0.5 h-4 w-4 shrink-0" />
          <span>TeamBoxes do not have a direct PostBox password. Members sign in with their own mailbox; PostBox access itself is enabled in the next collaboration phase.</span>
        </PortalNotice>
      </div>
    </div>
  );
}
