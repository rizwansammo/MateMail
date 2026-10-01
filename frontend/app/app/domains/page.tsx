"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Globe2,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
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
  status: "pending" | "active" | "warning" | "failed" | "paused";
  dns_health_score: number;
  mail_service_ready: boolean;
  ownership_verified: boolean;
  added_at: string;
}

function pretty(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function DomainsPage() {
  const router = useRouter();
  const { user, tenant } = useAuth();
  const [domains, setDomains] = useState<Domain[]>([]);
  const [myRole, setMyRole] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [newDomain, setNewDomain] = useState("");
  const [addError, setAddError] = useState("");
  const [adding, setAdding] = useState(false);

  async function fetchDomains() {
    setLoading(true);
    setLoadError("");
    try {
      const res = await apiRequest("/api/domains/");
      if (res.ok) {
        setDomains(await res.json());
      } else {
        const data = await res.json().catch(() => null);
        setLoadError(data?.detail ?? "Domains could not be loaded.");
      }
    } catch {
      setLoadError("Domains could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchDomains();
    if (tenant?.id) {
      apiRequest(`/api/workspaces/${tenant.id}/stats/`)
        .then(async (res) => res.ok ? res.json() : null)
        .then((data) => {
          if (data?.my_role) setMyRole(data.my_role);
        })
        .catch(() => {});
    }
  }, [tenant?.id]);

  const filteredDomains = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return domains;
    return domains.filter((domain) =>
      domain.domain.toLowerCase().includes(needle) ||
      domain.status.toLowerCase().includes(needle)
    );
  }, [domains, query]);

  async function handleAdd(event: React.FormEvent) {
    event.preventDefault();
    setAddError("");
    setAdding(true);
    try {
      const res = await apiRequest("/api/domains/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: newDomain.trim() }),
      });
      const data = await res.json().catch(() => null);
      if (res.ok && data?.id) {
        setNewDomain("");
        setAddOpen(false);
        router.push(`/app/domains/${data.id}`);
        return;
      }
      const domainMessage = Array.isArray(data?.domain) ? data.domain[0] : data?.domain;
      setAddError(domainMessage ?? data?.detail ?? "Failed to add domain.");
    } catch {
      setAddError("Failed to add domain. Please try again.");
    } finally {
      setAdding(false);
    }
  }

  const canAdmin = myRole === "owner" || myRole === "admin";
  const canAttemptDomainCreate =
    !!user?.email_verified && tenant?.status === "active" && canAdmin;

  return (
    <div className="portal-page">
      <PortalPageHeading
        title="Domains"
        description="Connect, verify and monitor the domains that power your organization’s email."
        actions={
          <PortalButton
            type="button"
            onClick={() => {
              setAddOpen(true);
              setAddError("");
            }}
            disabled={!canAttemptDomainCreate}
          >
            <Plus className="h-4 w-4" />
            Add domain
          </PortalButton>
        }
      />

      {!user?.email_verified && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span><strong>Email verification required.</strong> Verify your account before managing email domains.</span>
          </PortalNotice>
        </div>
      )}

      {user?.email_verified && tenant?.status !== "active" && (
        <div className="mb-5">
          <PortalNotice tone="info">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <strong>Workspace status: {pretty(tenant?.status || "pending")}.</strong>{" "}
              Domain creation becomes available after workspace approval.
            </span>
          </PortalNotice>
        </div>
      )}

      {addOpen && (
        <PortalCard
          className="portal-add-domain"
          title="Connect a new domain"
          subtitle="Add a domain you already own. MateMail does not transfer or purchase the domain."
        >
          <form onSubmit={handleAdd}>
            <div className="portal-form-row">
              <div className="portal-form-field">
                <label htmlFor="portal-domain-name">Domain name</label>
                <input
                  id="portal-domain-name"
                  className="portal-input"
                  type="text"
                  autoFocus
                  required
                  value={newDomain}
                  onChange={(event) => {
                    setNewDomain(event.target.value);
                    setAddError("");
                  }}
                  placeholder="yourcompany.com"
                />
                <p className="mt-2 text-[10px] text-[var(--portal-muted)]">
                  Enter the root domain. Prefixes such as https:// and www. are normalized by the backend.
                </p>
                {addError && <div className="portal-form-error">{addError}</div>}
              </div>
              <PortalButton type="submit" disabled={adding || !newDomain.trim()}>
                {adding ? "Adding…" : "Add domain"}
              </PortalButton>
              <PortalButton
                type="button"
                variant="secondary"
                disabled={adding}
                onClick={() => {
                  setAddOpen(false);
                  setNewDomain("");
                  setAddError("");
                }}
              >
                Cancel
              </PortalButton>
            </div>
          </form>
        </PortalCard>
      )}

      {loadError && (
        <div className="mb-5">
          <PortalNotice tone="danger">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{loadError}</span>
          </PortalNotice>
        </div>
      )}

      <PortalCard bodyClassName="!p-0">
        <div className="portal-domain-toolbar">
          <div className="flex min-w-0 flex-1 items-center gap-2 text-[var(--portal-muted)]">
            <Search className="h-4 w-4" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search domains…"
              aria-label="Search domains"
            />
          </div>
          <button
            type="button"
            className="portal-icon-button"
            onClick={fetchDomains}
            disabled={loading}
            aria-label="Refresh domains"
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
        ) : domains.length === 0 ? (
          <PortalEmptyState
            title="Connect your first domain"
            description="Add a domain you own, prove ownership with a TXT record, then configure mail DNS."
            action={
              canAttemptDomainCreate ? (
                <PortalButton type="button" onClick={() => setAddOpen(true)}>
                  <Plus className="h-4 w-4" />
                  Add domain
                </PortalButton>
              ) : undefined
            }
          />
        ) : filteredDomains.length === 0 ? (
          <PortalEmptyState
            title="No matching domains"
            description="Try a different search term."
          />
        ) : (
          <div className="portal-domain-table-wrap">
            <table className="portal-domain-table">
              <thead>
                <tr>
                  <th>Domain</th>
                  <th>Ownership</th>
                  <th>DNS health</th>
                  <th>Mail service</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {filteredDomains.map((domain) => (
                  <tr
                    key={domain.id}
                    onClick={() => router.push(`/app/domains/${domain.id}`)}
                    className="cursor-pointer"
                  >
                    <td>
                      <div className="portal-domain-identity">
                        <span className="portal-domain-symbol"><Globe2 className="h-4 w-4" /></span>
                        <span>
                          <strong>{domain.domain}</strong>
                          <small>Added {new Date(domain.added_at).toLocaleDateString()}</small>
                        </span>
                      </div>
                    </td>
                    <td>
                      <PortalStatus value={domain.ownership_verified ? "Verified" : "Pending"} />
                    </td>
                    <td>
                      <div className="portal-health-inline">
                        <span className="portal-health-inline-bar">
                          <span style={{ width: `${Math.max(0, Math.min(100, domain.dns_health_score))}%` }} />
                        </span>
                        <span>{domain.dns_health_score}/100</span>
                      </div>
                    </td>
                    <td>
                      <PortalStatus value={domain.mail_service_ready ? "Ready" : "Not ready"} />
                    </td>
                    <td><PortalStatus value={pretty(domain.status)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </PortalCard>
    </div>
  );
}
