"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalNotice,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface GroupMember {
  id: string;
  mailbox_id: string;
  email: string;
  full_name: string;
  kind: "personal" | "team_box";
  role: "member" | "owner";
}

interface AllowedSender {
  id: string;
  mailbox_id: string;
  email: string;
  full_name: string;
}

interface ForwardGroup {
  id: string;
  address: string;
  display_name: string;
  status: "active" | "disabled";
  sender_policy: "anyone" | "organization" | "members" | "selected";
  mail_service_ready: boolean;
  mail_service_message: string;
  members: GroupMember[];
  allowed_senders: AllowedSender[];
}

interface Mailbox {
  id: string;
  email: string;
  full_name: string;
  kind: "personal" | "team_box";
  status: string;
}

const policyLabels: Record<ForwardGroup["sender_policy"], string> = {
  anyone: "Anyone",
  organization: "Organization only",
  members: "Members only",
  selected: "Selected senders",
};

export default function ForwardGroupDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [group, setGroup] = useState<ForwardGroup | null>(null);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [memberMailboxId, setMemberMailboxId] = useState("");
  const [senderMailboxId, setSenderMailboxId] = useState("");
  const [busy, setBusy] = useState(false);
  const [rowBusy, setRowBusy] = useState("");

  const fetchAll = useCallback(async () => {
    const [groupResponse, mailboxResponse] = await Promise.all([
      apiRequest(`/api/forward-groups/${params.id}/`),
      apiRequest("/api/mailboxes/"),
    ]);
    if (groupResponse.ok) setGroup(await groupResponse.json());
    if (mailboxResponse.ok) {
      const rows = (await mailboxResponse.json()) as Mailbox[];
      setMailboxes(rows.filter((mailbox) => mailbox.status === "active"));
    }
  }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await fetchAll();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [fetchAll]);

  const members = group?.members ?? [];
  const allowedSenders = group?.allowed_senders ?? [];

  const availableMembers = useMemo(
    () => mailboxes.filter(
      (mailbox) => !members.some((member) => member.mailbox_id === mailbox.id)
    ),
    [mailboxes, members]
  );

  const availableSenders = useMemo(
    () => mailboxes.filter(
      (mailbox) =>
        mailbox.kind === "personal" &&
        !allowedSenders.some((sender) => sender.mailbox_id === mailbox.id)
    ),
    [mailboxes, allowedSenders]
  );

  const chosenMemberId = availableMembers.some((row) => row.id === memberMailboxId)
    ? memberMailboxId
    : availableMembers[0]?.id || "";

  const chosenSenderId = availableSenders.some((row) => row.id === senderMailboxId)
    ? senderMailboxId
    : availableSenders[0]?.id || "";

  function setNotice(text: string, isFailed = false) {
    setMessage(text);
    setFailed(isFailed);
  }

  async function patchPolicy(policy: ForwardGroup["sender_policy"]) {
    setBusy(true);
    setNotice("");
    try {
      const response = await apiRequest(`/api/forward-groups/${params.id}/policy/`, {
        method: "PATCH",
        body: JSON.stringify({ sender_policy: policy }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ?? "Sender policy could not be updated.", true);
        return;
      }
      setGroup(data);
      setNotice("Sender policy updated.");
    } finally {
      setBusy(false);
    }
  }

  async function addMember() {
    if (!chosenMemberId) return;
    setBusy(true);
    setNotice("");
    try {
      const response = await apiRequest(`/api/forward-groups/${params.id}/members/`, {
        method: "POST",
        body: JSON.stringify({ mailbox_id: chosenMemberId, role: "member" }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ?? data?.mailbox_id ?? "Member could not be added.", true);
        return;
      }
      await fetchAll();
      setNotice("Member added.");
    } finally {
      setBusy(false);
    }
  }

  async function removeMember(member: GroupMember) {
    setRowBusy(member.id);
    setNotice("");
    try {
      const response = await apiRequest(
        `/api/forward-groups/${params.id}/members/${member.id}/`,
        { method: "DELETE" }
      );
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ?? "Member could not be removed.", true);
        return;
      }
      await fetchAll();
      setNotice("Member removed.");
    } finally {
      setRowBusy("");
    }
  }

  async function changeRole(member: GroupMember, role: GroupMember["role"]) {
    setRowBusy(member.id);
    try {
      const response = await apiRequest(
        `/api/forward-groups/${params.id}/members/${member.id}/`,
        { method: "PATCH", body: JSON.stringify({ role }) }
      );
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        setNotice(data?.detail ?? "Member role could not be changed.", true);
        return;
      }
      await fetchAll();
    } finally {
      setRowBusy("");
    }
  }

  async function addSender() {
    if (!chosenSenderId) return;
    setBusy(true);
    try {
      const response = await apiRequest(`/api/forward-groups/${params.id}/senders/`, {
        method: "POST",
        body: JSON.stringify({ mailbox_id: chosenSenderId }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ?? data?.mailbox_id ?? "Sender could not be added.", true);
        return;
      }
      await fetchAll();
      setNotice("Allowed sender added.");
    } finally {
      setBusy(false);
    }
  }

  async function removeSender(sender: AllowedSender) {
    setRowBusy(sender.id);
    try {
      const response = await apiRequest(
        `/api/forward-groups/${params.id}/senders/${sender.id}/`,
        { method: "DELETE" }
      );
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        setNotice(data?.detail ?? "Sender could not be removed.", true);
        return;
      }
      await fetchAll();
      setNotice("Allowed sender removed.");
    } finally {
      setRowBusy("");
    }
  }

  async function toggleStatus() {
    if (!group) return;
    setBusy(true);
    try {
      const response = await apiRequest(`/api/forward-groups/${group.id}/status/`, {
        method: "PATCH",
        body: JSON.stringify({ status: group.status === "active" ? "disabled" : "active" }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ?? "Status could not be changed.", true);
        return;
      }
      setGroup(data);
      setNotice(data.status === "active" ? "Forward Group enabled." : "Forward Group disabled.");
    } finally {
      setBusy(false);
    }
  }

  async function retrySync() {
    setBusy(true);
    try {
      const response = await apiRequest(`/api/forward-groups/${params.id}/reprovision/`, {
        method: "POST",
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setNotice(data?.detail ?? "Mail service sync failed.", true);
        return;
      }
      setGroup(data);
      setNotice("Forward Group mail service synced.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteGroup() {
    if (!group || !window.confirm(`Delete ${group.address}? This does not delete member mailboxes.`)) return;
    setBusy(true);
    try {
      const response = await apiRequest(`/api/forward-groups/${group.id}/`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        setNotice(data?.detail ?? "Forward Group could not be deleted.", true);
        return;
      }
      router.push("/app/forward-groups");
    } finally {
      setBusy(false);
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

  if (!group) {
    return <div className="portal-page"><PortalNotice tone="danger">Forward Group not found.</PortalNotice></div>;
  }

  return (
    <div className="portal-page">
      <div className="mb-4">
        <Link href="/app/forward-groups" className="auth-text-button inline-flex items-center gap-2">
          <ArrowLeft className="h-4 w-4" />
          Forward Groups
        </Link>
      </div>

      <PortalCard className="mb-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="portal-identity-cell">
            <span className="portal-avatar"><Users className="h-4 w-4" /></span>
            <span>
              <strong>{group.display_name}</strong>
              <small>{group.address}</small>
            </span>
          </div>
          <div className="portal-inline-actions">
            <PortalStatus value={group.status} />
            <PortalButton type="button" variant="secondary" disabled={busy} onClick={toggleStatus}>
              {group.status === "active" ? "Disable" : "Enable"}
            </PortalButton>
            <PortalButton type="button" variant="secondary" disabled={busy} onClick={retrySync}>
              <RefreshCw className={"h-4 w-4 " + (busy ? "animate-spin" : "")} />
              Retry mail service
            </PortalButton>
            <PortalButton type="button" variant="danger" disabled={busy} onClick={deleteGroup}>
              <Trash2 className="h-4 w-4" />
              Delete
            </PortalButton>
          </div>
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-3">
          <div className="portal-detail-stat"><span>Members</span><strong>{members.length}</strong></div>
          <div className="portal-detail-stat"><span>Sender policy</span><strong>{policyLabels[group.sender_policy]}</strong></div>
          <div className="portal-detail-stat">
            <span>Mail service</span>
            <strong className="inline-flex items-center gap-2">
              {group.mail_service_ready
                ? <CheckCircle2 className="h-4 w-4" />
                : <AlertCircle className="h-4 w-4" />}
              {group.mail_service_ready ? "Ready" : "Needs attention"}
            </strong>
          </div>
        </div>
        {!group.mail_service_ready && group.mail_service_message && (
          <div className="mt-4"><PortalNotice tone="warn">{group.mail_service_message}</PortalNotice></div>
        )}
      </PortalCard>

      {message && (
        <div className="mb-5">
          <PortalNotice tone={failed ? "danger" : "success"}>{message}</PortalNotice>
        </div>
      )}

      <PortalCard className="mb-5" title="Members" subtitle="Each member receives its own copy in its own mailbox.">
        <div className="mb-5 flex flex-wrap items-end gap-3">
          <div className="portal-field min-w-[18rem] flex-1">
            <label>Add mailbox</label>
            <select value={chosenMemberId} onChange={(event) => setMemberMailboxId(event.target.value)}>
              {availableMembers.map((mailbox) => (
                <option key={mailbox.id} value={mailbox.id}>
                  {mailbox.full_name ? `${mailbox.full_name} — ${mailbox.email}` : mailbox.email}
                  {mailbox.kind === "team_box" ? " · TeamBox" : ""}
                </option>
              ))}
            </select>
          </div>
          <PortalButton type="button" onClick={addMember} disabled={busy || !chosenMemberId}>
            <UserPlus className="h-4 w-4" />
            Add member
          </PortalButton>
        </div>

        <div className="portal-management-table-wrap">
          <table className="portal-management-table">
            <thead><tr><th>Mailbox</th><th>Role</th><th className="text-right">Actions</th></tr></thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.id}>
                  <td>
                    <div className="portal-identity-cell">
                      <span className="portal-avatar">{member.full_name?.[0]?.toUpperCase() || "M"}</span>
                      <span>
                        <strong>{member.full_name || member.email}</strong>
                        <small>{member.email}{member.kind === "team_box" ? " · TeamBox" : ""}</small>
                      </span>
                    </div>
                  </td>
                  <td>
                    <select
                      value={member.role}
                      disabled={rowBusy === member.id}
                      onChange={(event) => changeRole(member, event.target.value as GroupMember["role"])}
                    >
                      <option value="member">Member</option>
                      <option value="owner">Owner</option>
                    </select>
                  </td>
                  <td>
                    <div className="portal-inline-actions">
                      <button
                        type="button"
                        className="portal-action-button danger"
                        disabled={rowBusy === member.id}
                        onClick={() => removeMember(member)}
                        aria-label={`Remove ${member.email}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </PortalCard>

      <PortalCard className="mb-5" title="Sender policy" subtitle="Restricted policies are enforced by Postfix using the authenticated mailbox, not the visible From address.">
        <div className="portal-form-grid">
          <div className="portal-field full">
            <label>Who can send to this group?</label>
            <select
              value={group.sender_policy}
              disabled={busy}
              onChange={(event) => void patchPolicy(event.target.value as ForwardGroup["sender_policy"])}
            >
              <option value="anyone">Anyone</option>
              <option value="organization">Organization only</option>
              <option value="members">Members only</option>
              <option value="selected">Selected senders</option>
            </select>
          </div>
        </div>

        {group.sender_policy === "selected" && (
          <div className="mt-5">
            <div className="mb-4 flex flex-wrap items-end gap-3">
              <div className="portal-field min-w-[18rem] flex-1">
                <label>Add allowed sender</label>
                <select value={chosenSenderId} onChange={(event) => setSenderMailboxId(event.target.value)}>
                  {availableSenders.map((mailbox) => (
                    <option key={mailbox.id} value={mailbox.id}>
                      {mailbox.full_name ? `${mailbox.full_name} — ${mailbox.email}` : mailbox.email}
                    </option>
                  ))}
                </select>
              </div>
              <PortalButton type="button" onClick={addSender} disabled={busy || !chosenSenderId}>
                Add sender
              </PortalButton>
            </div>

            {allowedSenders.length === 0 ? (
              <PortalNotice tone="warn">
                Selected sender policy is active but no sender is allowed yet. The group is closed until you add one.
              </PortalNotice>
            ) : (
              <div className="portal-management-table-wrap">
                <table className="portal-management-table">
                  <thead><tr><th>Allowed sender</th><th className="text-right">Actions</th></tr></thead>
                  <tbody>
                    {allowedSenders.map((sender) => (
                      <tr key={sender.id}>
                        <td>{sender.full_name || sender.email}<small className="block">{sender.email}</small></td>
                        <td>
                          <div className="portal-inline-actions">
                            <button
                              type="button"
                              className="portal-action-button danger"
                              disabled={rowBusy === sender.id}
                              onClick={() => removeSender(sender)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </PortalCard>

      <PortalNotice tone="info">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          Forward Groups never get a PostBox login, mailbox storage or Send As identity.
          Existing mailbox Forwarding remains a separate feature.
        </span>
      </PortalNotice>
    </div>
  );
}
