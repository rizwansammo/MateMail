"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Inbox,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalNotice,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface TeamBoxMember {
  id: string;
  mailbox_id: string;
  email: string;
  full_name: string;
  can_read: boolean;
  can_manage: boolean;
  can_send_as: boolean;
  can_send_on_behalf: boolean;
  active: boolean;
}

interface TeamBox {
  id: string;
  email: string;
  full_name: string;
  status: "active" | "disabled" | "suspended";
  quota_mb: number;
  storage_used_mb: number;
  mail_service_ready: boolean;
  mail_service_message: string;
  members: TeamBoxMember[];
}

interface Mailbox {
  id: string;
  email: string;
  full_name: string;
  kind: "personal" | "team_box";
  status: string;
}

type PermissionKey =
  | "can_read"
  | "can_manage"
  | "can_send_as"
  | "can_send_on_behalf";

const permissionLabels: { key: PermissionKey; label: string; hint: string }[] = [
  { key: "can_read", label: "Read", hint: "Open and read TeamBox mail." },
  { key: "can_manage", label: "Manage", hint: "Organize messages and folders. Requires Read." },
  { key: "can_send_as", label: "Send As", hint: "Send with the TeamBox address as the sender." },
  { key: "can_send_on_behalf", label: "Send on behalf", hint: "Send on behalf of the TeamBox identity." },
];

function formatStorage(mb: number) {
  if (mb >= 1024) {
    const gb = mb / 1024;
    return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`;
  }
  return `${mb} MB`;
}

export default function TeamBoxDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { tenant } = useAuth();
  const [teamBox, setTeamBox] = useState<TeamBox | null>(null);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [myRole, setMyRole] = useState("");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [memberMailboxId, setMemberMailboxId] = useState("");
  const [memberPermissions, setMemberPermissions] = useState<Record<PermissionKey, boolean>>({
    can_read: true,
    can_manage: true,
    can_send_as: true,
    can_send_on_behalf: false,
  });
  const [addingMember, setAddingMember] = useState(false);
  const [busyMember, setBusyMember] = useState("");
  const [statusBusy, setStatusBusy] = useState(false);
  const [syncBusy, setSyncBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const fetchAll = useCallback(async () => {
    const [teamBoxResponse, mailboxResponse] = await Promise.all([
      apiRequest(`/api/team-boxes/${params.id}/`),
      apiRequest("/api/mailboxes/"),
    ]);
    if (teamBoxResponse.ok) setTeamBox(await teamBoxResponse.json());
    if (mailboxResponse.ok) setMailboxes(await mailboxResponse.json());
  }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await Promise.all([
          fetchAll(),
          tenant?.id
            ? apiRequest(`/api/workspaces/${tenant.id}/stats/`)
                .then(async (response) => response.ok ? response.json() : null)
                .then((data) => {
                  if (!cancelled && data?.my_role) setMyRole(data.my_role);
                })
            : Promise.resolve(),
        ]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [fetchAll, tenant?.id]);

  const canAdmin = myRole === "owner" || myRole === "admin";
  const members = teamBox?.members || [];
  const availableMailboxes = useMemo(
    () => mailboxes.filter(
      (mailbox) =>
        mailbox.kind === "personal" &&
        mailbox.status === "active" &&
        !members.some((member) => member.mailbox_id === mailbox.id)
    ),
    [mailboxes, members]
  );

  useEffect(() => {
    setMemberMailboxId((current) =>
      availableMailboxes.some((mailbox) => mailbox.id === current)
        ? current
        : availableMailboxes[0]?.id || ""
    );
  }, [availableMailboxes]);

  function setNotice(text: string, isFailed = false) {
    setMessage(text);
    setFailed(isFailed);
  }

  async function addMember(event: React.FormEvent) {
    event.preventDefault();
    if (!memberMailboxId) return;
    setAddingMember(true);
    setNotice("");
    try {
      const response = await apiRequest(`/api/team-boxes/${params.id}/members/`, {
        method: "POST",
        body: JSON.stringify({
          mailbox_id: memberMailboxId,
          ...memberPermissions,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ? String(data.detail) : "Member could not be added.", true);
        return;
      }
      setNotice("TeamBox member added.");
      await fetchAll();
    } catch {
      setNotice("Member could not be added.", true);
    } finally {
      setAddingMember(false);
    }
  }

  async function updatePermission(member: TeamBoxMember, key: PermissionKey, value: boolean) {
    const next = { ...member, [key]: value };
    if (key === "can_read" && !value && next.can_manage) {
      next.can_manage = false;
    }
    if (!next.can_read && !next.can_manage && !next.can_send_as && !next.can_send_on_behalf) {
      setNotice("A member must keep at least one permission.", true);
      return;
    }

    setBusyMember(member.id);
    setNotice("");
    try {
      const response = await apiRequest(
        `/api/team-boxes/${params.id}/members/${member.id}/`,
        {
          method: "PATCH",
          body: JSON.stringify({
            can_read: next.can_read,
            can_manage: next.can_manage,
            can_send_as: next.can_send_as,
            can_send_on_behalf: next.can_send_on_behalf,
          }),
        }
      );
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ? String(data.detail) : "Permissions could not be updated.", true);
        return;
      }
      setTeamBox((current) => current ? {
        ...current,
        members: current.members.map((row) => row.id === member.id ? data : row),
      } : current);
      setNotice("Permissions updated.");
    } catch {
      setNotice("Permissions could not be updated.", true);
    } finally {
      setBusyMember("");
    }
  }

  async function removeMember(member: TeamBoxMember) {
    setBusyMember(member.id);
    setNotice("");
    try {
      const response = await apiRequest(
        `/api/team-boxes/${params.id}/members/${member.id}/`,
        { method: "DELETE" }
      );
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        setNotice(data?.detail ?? "Member could not be removed.", true);
        return;
      }
      setNotice("TeamBox member removed.");
      await fetchAll();
    } catch {
      setNotice("Member could not be removed.", true);
    } finally {
      setBusyMember("");
    }
  }

  async function toggleStatus() {
    if (!teamBox) return;
    setStatusBusy(true);
    setNotice("");
    try {
      const response = await apiRequest(`/api/team-boxes/${teamBox.id}/status/`, {
        method: "PATCH",
        body: JSON.stringify({ status: teamBox.status === "active" ? "disabled" : "active" }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ?? "TeamBox status could not be changed.", true);
        return;
      }
      setTeamBox((current) => current ? { ...current, ...data, members: current.members } : current);
      setNotice(data.status === "active" ? "TeamBox enabled." : "TeamBox disabled.");
    } finally {
      setStatusBusy(false);
    }
  }

  async function retrySync() {
    setSyncBusy(true);
    setNotice("");
    try {
      const response = await apiRequest(`/api/team-boxes/${params.id}/reprovision/`, {
        method: "POST",
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ?? "Mail service sync failed.", true);
        return;
      }
      setNotice("TeamBox mail service synced.");
      await fetchAll();
    } catch {
      setNotice("Mail service sync failed.", true);
    } finally {
      setSyncBusy(false);
    }
  }

  async function deleteTeamBox() {
    setDeleting(true);
    setNotice("");
    try {
      const response = await apiRequest(`/api/team-boxes/${params.id}/`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        setNotice(data?.detail ?? "TeamBox could not be removed.", true);
        return;
      }
      router.push("/app/team-boxes");
    } catch {
      setNotice("TeamBox could not be removed.", true);
    } finally {
      setDeleting(false);
    }
  }

  if (loading) {
    return (
      <div className="portal-page">
        <PortalSkeleton className="mb-4 h-20 w-full" />
        <PortalSkeleton className="h-72 w-full" />
      </div>
    );
  }

  if (!teamBox) {
    return (
      <div className="portal-page">
        <PortalNotice tone="danger">TeamBox not found.</PortalNotice>
      </div>
    );
  }

  return (
    <div className="portal-page">
      <div className="mb-4">
        <Link href="/app/team-boxes" className="auth-text-button inline-flex items-center gap-2">
          <ArrowLeft className="h-4 w-4" />
          TeamBoxes
        </Link>
      </div>

      <PortalCard className="mb-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="portal-identity-cell">
            <span className="portal-avatar"><Inbox className="h-4 w-4" /></span>
            <span>
              <strong>{teamBox.full_name}</strong>
              <small>{teamBox.email}</small>
            </span>
          </div>
          <div className="portal-inline-actions">
            <PortalStatus value={teamBox.status} />
            {canAdmin && (
              <>
                <PortalButton type="button" variant="secondary" onClick={toggleStatus} disabled={statusBusy}>
                  {teamBox.status === "active" ? "Disable" : "Enable"}
                </PortalButton>
                <PortalButton type="button" variant="secondary" onClick={retrySync} disabled={syncBusy}>
                  <RefreshCw className={"h-4 w-4 " + (syncBusy ? "animate-spin" : "")} />
                  Retry mail service
                </PortalButton>
                <PortalButton type="button" variant="danger" onClick={() => setDeleteOpen(true)}>
                  <Trash2 className="h-4 w-4" />
                  Delete
                </PortalButton>
              </>
            )}
          </div>
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-3">
          <div className="portal-detail-stat">
            <span>Members</span>
            <strong>{members.length}</strong>
          </div>
          <div className="portal-detail-stat">
            <span>Storage</span>
            <strong>{formatStorage(teamBox.storage_used_mb)} / {formatStorage(teamBox.quota_mb)}</strong>
          </div>
          <div className="portal-detail-stat">
            <span>Mail service</span>
            <strong className="inline-flex items-center gap-2">
              {teamBox.mail_service_ready
                ? <CheckCircle2 className="h-4 w-4" />
                : <AlertCircle className="h-4 w-4" />}
              {teamBox.mail_service_ready ? "Ready" : "Needs attention"}
            </strong>
          </div>
        </div>

        {!teamBox.mail_service_ready && teamBox.mail_service_message && (
          <div className="mt-4">
            <PortalNotice tone="warn">{teamBox.mail_service_message}</PortalNotice>
          </div>
        )}
      </PortalCard>

      {message && (
        <div className="mb-5">
          <PortalNotice tone={failed ? "danger" : "success"}>{message}</PortalNotice>
        </div>
      )}

      {deleteOpen && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <div className="flex-1">
              Delete <strong>{teamBox.email}</strong>? Stored mail follows the platform mailbox-retention policy. Any Alias pointing to this TeamBox must be removed first.
            </div>
            <div className="flex gap-2">
              <PortalButton type="button" variant="secondary" onClick={() => setDeleteOpen(false)} disabled={deleting}>
                Cancel
              </PortalButton>
              <PortalButton type="button" variant="danger" onClick={deleteTeamBox} disabled={deleting}>
                {deleting ? "Deleting…" : "Delete TeamBox"}
              </PortalButton>
            </div>
          </PortalNotice>
        </div>
      )}

      <PortalCard
        className="mb-5"
        title="Members & permissions"
        subtitle="Members always authenticate with their own personal mailbox. TeamBox access never requires password sharing."
      >
        {canAdmin && (
          <form onSubmit={addMember} className="mb-6">
            <div className="portal-form-grid">
              <div className="portal-field full">
                <label>Add personal mailbox</label>
                {availableMailboxes.length ? (
                  <select
                    value={memberMailboxId}
                    onChange={(event) => setMemberMailboxId(event.target.value)}
                    required
                  >
                    {availableMailboxes.map((mailbox) => (
                      <option key={mailbox.id} value={mailbox.id}>
                        {mailbox.full_name ? `${mailbox.full_name} — ${mailbox.email}` : mailbox.email}
                      </option>
                    ))}
                  </select>
                ) : (
                  <PortalNotice tone="info">All active personal mailboxes are already members, or no personal mailbox is available.</PortalNotice>
                )}
              </div>

              <div className="portal-field full">
                <label>Initial permissions</label>
                <div className="grid gap-3 md:grid-cols-2">
                  {permissionLabels.map((permission) => (
                    <label key={permission.key} className="portal-choice" data-active={memberPermissions[permission.key]}>
                      <input
                        type="checkbox"
                        checked={memberPermissions[permission.key]}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setMemberPermissions((current) => {
                            const next = { ...current, [permission.key]: checked };
                            if (permission.key === "can_read" && !checked) next.can_manage = false;
                            if (permission.key === "can_manage" && checked) next.can_read = true;
                            return next;
                          });
                        }}
                      />
                      <span>
                        <strong>{permission.label}</strong>
                        <small className="block">{permission.hint}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <div className="portal-detail-actions">
              <PortalButton
                type="submit"
                disabled={addingMember || !memberMailboxId || !Object.values(memberPermissions).some(Boolean)}
              >
                <UserPlus className="h-4 w-4" />
                {addingMember ? "Adding…" : "Add member"}
              </PortalButton>
            </div>
          </form>
        )}

        {members.length === 0 ? (
          <PortalNotice tone="info">
            <Users className="mt-0.5 h-4 w-4 shrink-0" />
            <span>No members yet. The TeamBox can receive mail, but nobody can access or send from it until a personal mailbox is added.</span>
          </PortalNotice>
        ) : (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Member</th>
                  <th>Read</th>
                  <th>Manage</th>
                  <th>Send As</th>
                  <th>Send on behalf</th>
                  {canAdmin && <th className="text-right">Actions</th>}
                </tr>
              </thead>
              <tbody>
                {members.map((member) => (
                  <tr key={member.id}>
                    <td>
                      <div className="portal-identity-cell">
                        <span className="portal-avatar">{member.full_name?.[0]?.toUpperCase() || "M"}</span>
                        <span>
                          <strong>{member.full_name || member.email}</strong>
                          <small>{member.email}</small>
                        </span>
                      </div>
                    </td>
                    {permissionLabels.map((permission) => (
                      <td key={permission.key}>
                        <input
                          type="checkbox"
                          checked={member[permission.key]}
                          disabled={!canAdmin || busyMember === member.id}
                          onChange={(event) => updatePermission(member, permission.key, event.target.checked)}
                          aria-label={`${permission.label} for ${member.email}`}
                        />
                      </td>
                    ))}
                    {canAdmin && (
                      <td>
                        <div className="portal-inline-actions">
                          <button
                            type="button"
                            className="portal-action-button danger"
                            onClick={() => removeMember(member)}
                            disabled={busyMember === member.id}
                            aria-label={`Remove ${member.email}`}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </PortalCard>

      <PortalNotice tone="info">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <span>Direct login is disabled for TeamBoxes. Send As and Send on behalf permissions are also enforced by the Mail Engine, not only by the Hub UI.</span>
      </PortalNotice>
    </div>
  );
}
