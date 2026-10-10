"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  ListPlus,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Users,
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
  full_name: string;
  kind: "personal" | "team_box";
  status: string;
}

interface ForwardGroup {
  id: string;
  address: string;
  display_name: string;
  sender_policy: "anyone" | "organization" | "members" | "selected";
  status: "active" | "disabled";
  member_count: number;
  sender_count: number;
  mail_service_ready: boolean;
  mail_service_message: string;
}

const policyLabels: Record<ForwardGroup["sender_policy"], string> = {
  anyone: "Anyone",
  organization: "Organization only",
  members: "Members only",
  selected: "Selected senders",
};

function fieldError(value: unknown) {
  if (!value) return "";
  if (Array.isArray(value)) return value.map(String).join(" ");
  return String(value);
}

export default function ForwardGroupsPage() {
  const router = useRouter();
  const { user, tenant } = useAuth();
  const [groups, setGroups] = useState<ForwardGroup[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [myRole, setMyRole] = useState("");
  const [workspaceStatus, setWorkspaceStatus] = useState(tenant?.status || "");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [localPart, setLocalPart] = useState("");
  const [domainId, setDomainId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [senderPolicy, setSenderPolicy] = useState<ForwardGroup["sender_policy"]>("anyone");
  const [selectedSenderIds, setSelectedSenderIds] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [addErrors, setAddErrors] = useState<Record<string, unknown>>({});

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [groupResponse, domainResponse, mailboxResponse] = await Promise.all([
        apiRequest("/api/forward-groups/"),
        apiRequest("/api/domains/"),
        apiRequest("/api/mailboxes/"),
      ]);

      if (groupResponse.ok) setGroups(await groupResponse.json());
      else {
        const data = await groupResponse.json().catch(() => null);
        setLoadError(data?.detail ?? "Forward Groups could not be loaded.");
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

      if (mailboxResponse.ok) {
        const rows = (await mailboxResponse.json()) as Mailbox[];
        setMailboxes(rows.filter((mailbox) => mailbox.status === "active"));
      }
    } catch {
      setLoadError("Forward Groups could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchAll(), 0);
    if (tenant?.id) {
      apiRequest(`/api/workspaces/${tenant.id}/stats/`)
        .then(async (response) => response.ok ? response.json() : null)
        .then((data) => {
          if (data?.my_role) setMyRole(data.my_role);
          if (data?.tenant_status) setWorkspaceStatus(data.tenant_status);
        })
        .catch(() => {});
    }
    return () => window.clearTimeout(timer);
  }, [fetchAll, tenant?.id]);

  const canAdmin = myRole === "owner" || myRole === "admin";
  const canCreate =
    canAdmin &&
    !!user?.email_verified &&
    workspaceStatus === "active" &&
    domains.length > 0 &&
    mailboxes.length > 0;

  const personalMailboxes = useMemo(
    () => mailboxes.filter((mailbox) => mailbox.kind === "personal"),
    [mailboxes]
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return groups;
    return groups.filter((group) =>
      group.address.toLowerCase().includes(needle) ||
      group.display_name.toLowerCase().includes(needle) ||
      policyLabels[group.sender_policy].toLowerCase().includes(needle)
    );
  }, [groups, query]);

  function toggleId(list: string[], id: string) {
    return list.includes(id)
      ? list.filter((value) => value !== id)
      : [...list, id];
  }

  async function createGroup(event: React.FormEvent) {
    event.preventDefault();
    setAddErrors({});
    setAdding(true);
    try {
      const response = await apiRequest("/api/forward-groups/", {
        method: "POST",
        body: JSON.stringify({
          local_part: localPart.trim().toLowerCase(),
          domain_id: domainId,
          display_name: displayName.trim(),
          member_mailbox_ids: memberIds,
          sender_policy: senderPolicy,
          allowed_sender_mailbox_ids:
            senderPolicy === "selected" ? selectedSenderIds : [],
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok && response.status !== 202) {
        setAddErrors(
          typeof data === "object" && data !== null
            ? data
            : { detail: "Forward Group could not be created." }
        );
        return;
      }

      setLocalPart("");
      setDisplayName("");
      setMemberIds([]);
      setSenderPolicy("anyone");
      setSelectedSenderIds([]);
      setAddOpen(false);
      await fetchAll();
      if (data?.id) router.push(`/app/forward-groups/${data.id}`);
    } catch {
      setAddErrors({ detail: "Forward Group could not be created." });
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="portal-page astra-resource-page astra-collaboration-page">
      <PortalPageHeading
        title="Forward Groups"
        description="Distribution addresses that deliver one message to multiple member mailboxes."
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
            Create Forward Group
          </PortalButton>
        }
      />

      <div className="mb-5">
        <PortalNotice tone="info">
          <ListPlus className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            A Forward Group is not a mailbox: it has no login, Inbox, Sent folder or storage.
            Mail sent to the group is distributed to its members.
          </span>
        </PortalNotice>
      </div>

      <div className="astra-resource-summary" aria-label="Forward Groups statistics">
        <div><span>Forward Groups</span><strong>{loading ? "—" : groups.length}</strong></div>
        <div><span>Active groups</span><strong>{loading ? "—" : groups.filter((item) => item.status === "active").length}</strong></div>
        <div><span>Distribution members</span><strong>{loading ? "—" : groups.reduce((sum, item) => sum + item.member_count, 0)}</strong></div>
      </div>

      {!user?.email_verified && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Verify your account email before provisioning a Forward Group.</span>
          </PortalNotice>
        </div>
      )}

      <AstraResourceDialog
        open={addOpen}
        busy={adding}
        title="Create a Forward Group"
        description="Choose an address, delivery members, and who is allowed to send to the group."
        onDismiss={() => { setAddOpen(false); setAddErrors({}); }}
      >
          <form onSubmit={createGroup}>
            {fieldError(addErrors.detail) && (
              <div className="mb-4">
                <PortalNotice tone="danger">{fieldError(addErrors.detail)}</PortalNotice>
              </div>
            )}

            <div className="portal-form-grid">
              <div className="portal-field full">
                <label htmlFor="astra-fg-local">Group address</label>
                <div className="portal-address-composer">
                  <input
                    id="astra-fg-local"
                    type="text"
                    required
                    pattern="[a-zA-Z0-9._+-]+"
                    value={localPart}
                    onChange={(event) => setLocalPart(event.target.value)}
                    placeholder="engineering"
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
                <label htmlFor="astra-fg-name">Display name</label>
                <input
                  id="astra-fg-name"
                  type="text"
                  required
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="Engineering"
                />
              </div>

              <div className="portal-field full">
                <label>Members</label>
                <div className="grid gap-2 md:grid-cols-2">
                  {mailboxes.map((mailbox) => (
                    <label
                      key={mailbox.id}
                      className="portal-choice"
                      data-active={memberIds.includes(mailbox.id)}
                    >
                      <input
                        type="checkbox"
                        checked={memberIds.includes(mailbox.id)}
                        onChange={() => setMemberIds((current) => toggleId(current, mailbox.id))}
                      />
                      <span>
                        <strong>{mailbox.full_name || mailbox.email}</strong>
                        <small className="block">
                          {mailbox.email}{mailbox.kind === "team_box" ? " · TeamBox" : ""}
                        </small>
                      </span>
                    </label>
                  ))}
                </div>
                {fieldError(addErrors.member_mailbox_ids) && <div className="portal-field-error">{fieldError(addErrors.member_mailbox_ids)}</div>}
              </div>

              <div className="portal-field full">
                <label htmlFor="astra-fg-policy">Who can send to this group?</label>
                <select
                  id="astra-fg-policy"
                  value={senderPolicy}
                  onChange={(event) => setSenderPolicy(event.target.value as ForwardGroup["sender_policy"])}
                >
                  <option value="anyone">Anyone</option>
                  <option value="organization">Organization only</option>
                  <option value="members">Members only</option>
                  <option value="selected">Selected senders</option>
                </select>
              </div>

              {senderPolicy === "selected" && (
                <div className="portal-field full">
                  <label>Selected senders</label>
                  <div className="grid gap-2 md:grid-cols-2">
                    {personalMailboxes.map((mailbox) => (
                      <label
                        key={mailbox.id}
                        className="portal-choice"
                        data-active={selectedSenderIds.includes(mailbox.id)}
                      >
                        <input
                          type="checkbox"
                          checked={selectedSenderIds.includes(mailbox.id)}
                          onChange={() =>
                            setSelectedSenderIds((current) => toggleId(current, mailbox.id))
                          }
                        />
                        <span>
                          <strong>{mailbox.full_name || mailbox.email}</strong>
                          <small className="block">{mailbox.email}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="portal-detail-actions">
              <PortalButton
                type="submit"
                disabled={
                  adding ||
                  !localPart.trim() ||
                  !displayName.trim() ||
                  !domainId ||
                  memberIds.length === 0
                }
              >
                {adding ? "Creating…" : "Create Forward Group"}
              </PortalButton>
              <PortalButton
                type="button"
                variant="secondary"
                disabled={adding}
                onClick={() => setAddOpen(false)}
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
              placeholder="Search Forward Groups…"
              aria-label="Search Forward Groups"
            />
          </div>
          <button
            type="button"
            className="portal-icon-button"
            onClick={fetchAll}
            disabled={loading}
            aria-label="Refresh Forward Groups"
          >
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
          </button>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : groups.length === 0 ? (
          <PortalEmptyState
            title="No Forward Groups yet"
            description="Create a distribution address such as engineering@ or everyone@."
          />
        ) : filtered.length === 0 ? (
          <PortalEmptyState title="No matching Forward Groups" description="Try another search term." />
        ) : (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Forward Group</th>
                  <th>Members</th>
                  <th>Sender policy</th>
                  <th>Status</th>
                  <th>Mail service</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((group) => (
                  <tr
                    key={group.id}
                    className="cursor-pointer"
                    tabIndex={0}
                    aria-label={`Open Forward Group ${group.address}`}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        router.push(`/app/forward-groups/${group.id}`);
                      }
                    }}
                    onClick={() => router.push(`/app/forward-groups/${group.id}`)}
                  >
                    <td>
                      <div className="portal-identity-cell">
                        <span className="portal-avatar"><Users className="h-4 w-4" /></span>
                        <span>
                          <strong>{group.display_name}</strong>
                          <small>{group.address}</small>
                        </span>
                      </div>
                    </td>
                    <td>{group.member_count}</td>
                    <td>{policyLabels[group.sender_policy]}</td>
                    <td><PortalStatus value={group.status} /></td>
                    <td>
                      <span className={"portal-service-state " + (group.mail_service_ready ? "ready" : "waiting")}>
                        {group.mail_service_ready
                          ? <CheckCircle2 className="h-3.5 w-3.5" />
                          : <AlertCircle className="h-3.5 w-3.5" />}
                        {group.mail_service_ready ? "Ready" : "Needs attention"}
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
          Existing <strong>Forwarding</strong> remains a separate mailbox rule.
          Forward Groups are distribution recipients and never become a mailbox or send-as identity.
        </PortalNotice>
      </div>
    </div>
  );
}
