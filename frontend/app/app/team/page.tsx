"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  Clock3,
  Mail,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  UserMinus,
  Users,
  X,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import { AstraResourceDialog } from "@/components/workspace/astra-resource-dialog";
import {
  PortalButton,
  PortalCard,
  PortalEmptyState,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface Member {
  id: string;
  email: string;
  full_name: string;
  role: "owner" | "admin" | "support" | "read_only";
  status: string;
  created_at: string;
}

interface Invite {
  id: string;
  email: string;
  role: "admin" | "support" | "read_only";
  invited_by_email: string | null;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  is_revoked: boolean;
  is_pending: boolean;
}

interface WorkspaceStats {
  my_role: Member["role"];
}

const ROLES: Array<{ value: "admin" | "support" | "read_only"; label: string; desc: string }> = [
  { value: "admin", label: "Admin", desc: "Manage workspace resources and team operations." },
  { value: "support", label: "Support", desc: "Read workspace data and run approved diagnostics." },
  { value: "read_only", label: "Read-only", desc: "View workspace data without making changes." },
];

function pretty(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function initials(name: string, email: string) {
  const source = name.trim() || email;
  const parts = source.split(/[\s@._-]+/).filter(Boolean).slice(0, 2);
  return parts.map((part) => part[0]?.toUpperCase()).join("") || "TM";
}

function TeamPageContent() {
  const routeParams = useSearchParams();
  const requestedSearch = routeParams.get("q") || "";
  const router = useRouter();
  const { tenant, user, logout } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [myRole, setMyRole] = useState<Member["role"] | "">("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState(requestedSearch);
  const [tab, setTab] = useState<"members" | "invites">("members");

  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"admin" | "support" | "read_only">("admin");
  const [inviteError, setInviteError] = useState("");
  const [inviting, setInviting] = useState(false);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "warn" | "danger">("success");

  const [roleChanging, setRoleChanging] = useState("");
  const [confirmRemove, setConfirmRemove] = useState("");
  const [removing, setRemoving] = useState("");
  const [confirmRevoke, setConfirmRevoke] = useState("");
  const [revoking, setRevoking] = useState("");

  const loadTeam = useCallback(async (showLoading = true) => {
    if (!tenant?.id) return;
    if (showLoading) setLoading(true);
    setLoadError("");

    try {
      const [memberResponse, statsResponse] = await Promise.all([
        apiRequest(`/api/workspaces/${tenant.id}/members/`),
        apiRequest(`/api/workspaces/${tenant.id}/stats/`),
      ]);

      const memberRows = memberResponse.ok ? await memberResponse.json() as Member[] : [];
      const stats = statsResponse.ok ? await statsResponse.json() as WorkspaceStats : null;

      if (!memberResponse.ok) {
        const data = await memberResponse.json().catch(() => null);
        setLoadError(data?.detail ?? "Team members could not be loaded.");
      } else {
        setMembers(memberRows.filter((member) => member.status === "active"));
      }

      const resolvedRole = stats?.my_role || memberRows.find((member) => member.email === user?.email)?.role || "";
      setMyRole(resolvedRole);

      if (resolvedRole === "owner" || resolvedRole === "admin") {
        const inviteResponse = await apiRequest("/api/teams/invites/");
        if (inviteResponse.ok) {
          setInvites(await inviteResponse.json());
        } else {
          setInvites([]);
        }
      } else {
        setInvites([]);
      }
    } catch {
      setLoadError("Team members could not be loaded.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [tenant?.id, user?.email]);

  useEffect(() => {
    if (!tenant?.id) return;
    const timer = window.setTimeout(() => {
      void loadTeam(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadTeam, tenant?.id]);

  const canManageInvites = myRole === "owner" || myRole === "admin";
  const canChangeRoles = myRole === "owner";

  useEffect(() => {
    // Sync incoming query after mount (no render-cascade state update).
    const id = window.setTimeout(() => setQuery(requestedSearch), 0);
    return () => window.clearTimeout(id);
  }, [requestedSearch]);

  const filteredMembers = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return members;
    return members.filter((member) =>
      member.email.toLowerCase().includes(needle) ||
      member.full_name.toLowerCase().includes(needle) ||
      member.role.toLowerCase().includes(needle)
    );
  }, [members, query]);

  const filteredInvites = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return invites;
    return invites.filter((invite) =>
      invite.email.toLowerCase().includes(needle) ||
      invite.role.toLowerCase().includes(needle) ||
      (invite.invited_by_email || "").toLowerCase().includes(needle)
    );
  }, [invites, query]);

  async function sendInvite(event: React.FormEvent) {
    event.preventDefault();
    if (!canManageInvites) return;

    setInviting(true);
    setInviteError("");
    setMessage("");
    try {
      const response = await apiRequest("/api/teams/invites/", {
        method: "POST",
        body: JSON.stringify({
          email: inviteEmail.trim().toLowerCase(),
          role: inviteRole,
        }),
      });
      const data = await response.json().catch(() => null);

      if (response.ok && data?.id) {
        setInviteEmail("");
        setInviteRole("admin");
        setInviteOpen(false);
        if (data.email_delivered === false) {
          setMessageTone("warn");
          setMessage(data.detail ?? "The invitation was created, but the email could not be delivered.");
        } else {
          setMessageTone("success");
          setMessage("Invitation sent successfully.");
        }
        setTab("invites");
        await loadTeam(false);
      } else {
        setInviteError(data?.detail ?? "Invitation could not be sent.");
      }
    } catch {
      setInviteError("Invitation could not be sent.");
    } finally {
      setInviting(false);
    }
  }

  async function changeRole(member: Member, role: "admin" | "support" | "read_only") {
    if (!tenant?.id || !canChangeRoles || member.role === "owner") return;
    setRoleChanging(member.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/workspaces/${tenant.id}/members/${member.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ role }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setMembers((current) => current.map((item) => item.id === member.id ? data : item));
        setMessageTone("success");
        setMessage(`${member.email} is now ${pretty(role)}.`);
      } else {
        setMessageTone("danger");
        setMessage(data?.detail ?? "Member role could not be changed.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Member role could not be changed.");
    } finally {
      setRoleChanging("");
    }
  }

  async function removeMember(member: Member) {
    if (!tenant?.id) return;
    setRemoving(member.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/workspaces/${tenant.id}/members/${member.id}/`, {
        method: "DELETE",
      });
      if (response.ok || response.status === 204) {
        setConfirmRemove("");
        setMessageTone("success");
        if (member.email === user?.email) {
          await logout();
          router.push("/login");
          return;
        }
        setMessage("Member removed from the workspace.");
        await loadTeam(false);
      } else {
        const data = await response.json().catch(() => null);
        setMessageTone("danger");
        setMessage(data?.detail ?? "Member could not be removed.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Member could not be removed.");
    } finally {
      setRemoving("");
    }
  }

  async function revokeInvite(invite: Invite) {
    if (!canManageInvites) return;
    setRevoking(invite.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/teams/invites/${invite.id}/`, { method: "DELETE" });
      if (response.ok || response.status === 204) {
        setInvites((current) => current.filter((item) => item.id !== invite.id));
        setConfirmRevoke("");
        setMessageTone("success");
        setMessage("Invitation revoked.");
      } else {
        const data = await response.json().catch(() => null);
        setMessageTone("danger");
        setMessage(data?.detail ?? "Invitation could not be revoked.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Invitation could not be revoked.");
    } finally {
      setRevoking("");
    }
  }

  return (
    <div className="portal-page astra-resource-page astra-users-page">
      <PortalPageHeading
        title="Users & access"
        description="Manage workspace members, invitations and administrative roles."
        actions={
          canManageInvites ? (
            <PortalButton
              type="button"
              onClick={() => {
                setInviteOpen(true);
                setInviteError("");
              }}
            >
              <Plus className="h-4 w-4" />
              Invite member
            </PortalButton>
          ) : undefined
        }
      />

      <div className="astra-resource-summary" aria-label="Users and access statistics">
        <div><span>Active members</span><strong>{loading ? "—" : members.length}</strong></div>
        <div><span>Pending invitations</span><strong>{loading || !canManageInvites ? "—" : invites.filter(invite => invite.is_pending).length}</strong></div>
        <div><span>Workspace admins</span><strong>{loading ? "—" : members.filter(member => member.role === "admin" || member.role === "owner").length}</strong></div>
      </div>

      {message && (
        <div className="mb-5">
          <PortalNotice tone={messageTone}>{message}</PortalNotice>
        </div>
      )}

      <AstraResourceDialog
        open={inviteOpen && canManageInvites}
        busy={inviting}
        title="Invite member"
        description="Invite someone using a verified organization domain. Invitations expire after 7 days."
        onDismiss={() => { setInviteOpen(false); setInviteError(""); setInviteEmail(""); }}
      >
          <form onSubmit={sendInvite}>
            {inviteError && (
              <div className="mb-4"><PortalNotice tone="danger">{inviteError}</PortalNotice></div>
            )}

            <div className="portal-form-grid">
              <div className="portal-field">
                <label htmlFor="astra-user-invite-email">Email address</label>
                <input
                  id="astra-user-invite-email"
                  type="email"
                  autoComplete="off"
                  required
                  value={inviteEmail}
                  onChange={(event) => setInviteEmail(event.target.value)}
                  placeholder="colleague@example.com"
                />
              </div>
              <div className="portal-field">
                <label htmlFor="astra-user-invite-role">Workspace role</label>
                <select
                  id="astra-user-invite-role"
                  value={inviteRole}
                  onChange={(event) => setInviteRole(event.target.value as typeof inviteRole)}
                >
                  {ROLES.map((role) => (
                    <option key={role.value} value={role.value}>{role.label} — {role.desc}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="astra-user-invite-context">
              <ShieldCheck className="h-4 w-4 shrink-0" aria-hidden="true" />
              <p>Hub access only. This invitation does not create or assign a mailbox. Mailbox provisioning remains a separate administrative action.</p>
            </div>
            <div className="portal-detail-actions">
              <PortalButton type="submit" disabled={inviting || !inviteEmail.trim() || !canManageInvites}>
                <Mail className="h-4 w-4" />
                {inviting ? "Sending…" : "Send invitation"}
              </PortalButton>
              <PortalButton
                type="button"
                variant="secondary"
                disabled={inviting}
                onClick={() => {
                  setInviteOpen(false);
                  setInviteError("");
                  setInviteEmail("");
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

      <div className="portal-team-tabs">
        <button type="button" data-active={tab === "members"} onClick={() => setTab("members")}>
          Members <span>{members.length}</span>
        </button>
        {canManageInvites && (
          <button type="button" data-active={tab === "invites"} onClick={() => setTab("invites")}>
            Invitations <span>{invites.length}</span>
          </button>
        )}
      </div>

      <PortalCard className="portal-management-card" bodyClassName="!p-0">
        <div className="portal-management-toolbar">
          <div className="portal-management-search">
            <Search className="h-4 w-4" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={tab === "members" ? "Search members…" : "Search invitations…"}
              aria-label={tab === "members" ? "Search members" : "Search invitations"}
            />
          </div>
          <button
            type="button"
            className="portal-icon-button"
            onClick={() => loadTeam(true)}
            disabled={loading}
            aria-label="Refresh team"
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
        ) : tab === "members" ? (
          filteredMembers.length ? (
            <div className="portal-management-table-wrap">
              <table className="portal-management-table">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Joined</th>
                    <th className="text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredMembers.map((member) => {
                    const isSelf = member.email === user?.email;
                    const isOwnerRow = member.role === "owner";
                    const canRemove = !isOwnerRow && (myRole === "owner" || isSelf);
                    return (
                      <tr key={member.id}>
                        <td>
                          <div className="portal-identity-cell">
                            <span className="portal-avatar">{initials(member.full_name, member.email)}</span>
                            <span>
                              <strong>
                                {member.full_name || member.email}
                                {isSelf && <em className="portal-you-badge">You</em>}
                              </strong>
                              <small>{member.email}</small>
                            </span>
                          </div>
                        </td>
                        <td>
                          {canChangeRoles && !isSelf && !isOwnerRow ? (
                            <select
                              className="portal-input !h-[32px] !min-h-[32px] !w-[125px] !py-0 text-[10px]"
                              value={member.role}
                              disabled={roleChanging === member.id}
                              onChange={(event) => changeRole(member, event.target.value as "admin" | "support" | "read_only")}
                              aria-label={`Role for ${member.email}`}
                            >
                              {ROLES.map((role) => (
                                <option key={role.value} value={role.value}>{role.label}</option>
                              ))}
                            </select>
                          ) : (
                            <span className="portal-role-badge">
                              {member.role === "owner" && <ShieldCheck className="h-3.5 w-3.5" />}
                              {pretty(member.role)}
                            </span>
                          )}
                        </td>
                        <td><PortalStatus value={pretty(member.status)} /></td>
                        <td>{new Date(member.created_at).toLocaleDateString()}</td>
                        <td>
                          <div className="portal-inline-actions">
                            {canRemove && (
                              <button
                                type="button"
                                className="portal-action-button danger"
                                onClick={() => setConfirmRemove(member.id)}
                                disabled={removing === member.id}
                                aria-label={isSelf ? "Leave workspace" : `Remove ${member.email}`}
                              >
                                {isSelf ? <UserMinus className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
                              </button>
                            )}
                          </div>
                          {confirmRemove === member.id && (
                            <div className="portal-confirm-inline">
                              <PortalNotice tone="warn">
                                <div className="flex-1">
                                  {isSelf
                                    ? "Leave this workspace? You may lose access immediately."
                                    : <>Remove <strong>{member.email}</strong> from this workspace?</>}
                                </div>
                                <div className="flex gap-2">
                                  <PortalButton type="button" variant="secondary" onClick={() => setConfirmRemove("")} disabled={removing === member.id}>Cancel</PortalButton>
                                  <PortalButton type="button" variant="danger" onClick={() => removeMember(member)} disabled={removing === member.id}>
                                    {removing === member.id ? "Removing…" : isSelf ? "Leave" : "Remove"}
                                  </PortalButton>
                                </div>
                              </PortalNotice>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <PortalEmptyState title="No matching members" description="Try a different search term." />
          )
        ) : filteredInvites.length ? (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Invitation</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Invited by</th>
                  <th>Expires</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredInvites.map((invite) => (
                  <tr key={invite.id}>
                    <td>
                      <div className="portal-identity-cell">
                        <span className="portal-avatar"><Mail className="h-4 w-4" /></span>
                        <span><strong>{invite.email}</strong><small>Workspace invitation</small></span>
                      </div>
                    </td>
                    <td><span className="portal-role-badge">{pretty(invite.role)}</span></td>
                    <td><PortalStatus value={invite.is_pending ? "Pending" : "Expired"} /></td>
                    <td>{invite.invited_by_email || "Former member"}</td>
                    <td>{new Date(invite.expires_at).toLocaleDateString()}</td>
                    <td>
                      <div className="portal-inline-actions">
                        <button
                          type="button"
                          className="portal-action-button danger"
                          onClick={() => setConfirmRevoke(invite.id)}
                          disabled={revoking === invite.id}
                          aria-label={`Revoke invitation for ${invite.email}`}
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      {confirmRevoke === invite.id && (
                        <div className="portal-confirm-inline">
                          <PortalNotice tone="warn">
                            <div className="flex-1">Revoke the invitation for <strong>{invite.email}</strong>?</div>
                            <div className="flex gap-2">
                              <PortalButton type="button" variant="secondary" onClick={() => setConfirmRevoke("")} disabled={revoking === invite.id}>Cancel</PortalButton>
                              <PortalButton type="button" variant="danger" onClick={() => revokeInvite(invite)} disabled={revoking === invite.id}>
                                {revoking === invite.id ? "Revoking…" : "Revoke"}
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
        ) : (
          <PortalEmptyState
            title="No invitations"
            description="New invitations will appear here until they are accepted, revoked, or expire."
            action={canManageInvites ? <PortalButton type="button" onClick={() => setInviteOpen(true)}><Plus className="h-4 w-4" />Invite member</PortalButton> : undefined}
          />
        )}
      </PortalCard>

      <PortalCard className="mt-5" title="Roles, at a glance" subtitle="Role-based access is enforced by MateMail for each organization.">
        <div className="portal-role-guide">
          <div><strong>Owner</strong><span>Full workspace control. Only the owner can change another member’s role.</span></div>
          <div><strong>Admin</strong><span>Manage domains, mailboxes, routing, invitations and other administrative resources.</span></div>
          <div><strong>Support</strong><span>Read workspace data and perform specifically allowed diagnostics such as DNS re-checks.</span></div>
          <div><strong>Read-only</strong><span>Read workspace data without making changes.</span></div>
        </div>
      </PortalCard>

      {canManageInvites && (
        <div className="mt-4">
          <PortalNotice tone="info">
            <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Invite resending is not currently exposed by the backend. Existing invitations can be revoked; a replacement can then be created if needed.</span>
          </PortalNotice>
        </div>
      )}

      {!canManageInvites && myRole && (
        <div className="mt-4">
          <PortalNotice tone="info">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Your <strong>{pretty(myRole)}</strong> role can view members but cannot manage invitations.</span>
          </PortalNotice>
        </div>
      )}
    </div>
  );
}

/** Suspense boundary required by Next.js useSearchParams during static builds. */
export default function TeamPage() {
  return <Suspense fallback={null}><TeamPageContent /></Suspense>;
}
