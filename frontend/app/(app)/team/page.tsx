"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import { Users, Mail, Trash2, ChevronDown, Clock, X } from "lucide-react";

interface Member {
  id: string;
  email: string;
  full_name: string;
  role: string;
  status: string;
  created_at: string;
}

interface Invite {
  id: string;
  email: string;
  role: string;
  invited_by_email: string;
  created_at: string;
  expires_at: string;
  is_pending: boolean;
}

const ROLE_STYLES: Record<string, string> = {
  owner:     "bg-slate-900 text-white",
  admin:     "bg-cyan-600 text-white",
  support:   "bg-amber-100 text-amber-800",
  read_only: "bg-slate-100 text-slate-600",
};

const ROLES = [
  { value: "admin",     label: "Admin",      desc: "Manage domains, mailboxes, and members" },
  { value: "support",   label: "Support",    desc: "View mailboxes and logs" },
  { value: "read_only", label: "Read-only",  desc: "View only" },
];

export default function TeamPage() {
  const { tenant, user } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [loading, setLoading] = useState(true);
  const [myRole, setMyRole] = useState<string | null>(null);

  // Invite form
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("admin");
  const [inviteError, setInviteError] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteSent, setInviteSent] = useState(false);

  // Role change + remove state
  const [roleChanging, setRoleChanging] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  async function fetchData() {
    if (!tenant?.id) return;
    setLoading(true);
    try {
      const [membersRes, invitesRes] = await Promise.all([
        apiRequest(`/api/workspaces/${tenant.id}/members/`),
        apiRequest("/api/teams/invites/"),
      ]);
      if (membersRes.ok) setMembers(await membersRes.json());
      if (invitesRes.ok) setInvites(await invitesRes.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchData(); }, [tenant?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!user || !members.length) return;
    const mine = members.find((m) => m.email === user.email);
    if (mine) setMyRole(mine.role);
  }, [members, user]);

  const isOwner = myRole === "owner";
  const canManage = myRole === "owner" || myRole === "admin";

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setInviteError("");
    setInviteSent(false);
    setInviting(true);
    try {
      const res = await apiRequest("/api/teams/invites/", {
        method: "POST",
        body: JSON.stringify({ email: inviteEmail.trim(), role: inviteRole }),
      });
      if (res.ok) {
        setInviteSent(true);
        setInviteEmail("");
        setInviteRole("admin");
        setTimeout(() => {
          setInviteOpen(false);
          setInviteSent(false);
        }, 2000);
        await fetchData();
      } else {
        const body = await res.json().catch(() => ({}));
        setInviteError(body.detail ?? "Failed to send invite.");
      }
    } finally {
      setInviting(false);
    }
  }

  async function handleRoleChange(memberId: string, newRole: string) {
    if (!tenant?.id) return;
    setRoleChanging(memberId);
    try {
      const res = await apiRequest(`/api/workspaces/${tenant.id}/members/${memberId}/`, {
        method: "PATCH",
        body: JSON.stringify({ role: newRole }),
      });
      if (res.ok) await fetchData();
    } finally {
      setRoleChanging(null);
    }
  }

  async function handleRemove(memberId: string) {
    if (!tenant?.id) return;
    setRemoving(memberId);
    try {
      await apiRequest(`/api/workspaces/${tenant.id}/members/${memberId}/`, {
        method: "DELETE",
      });
      await fetchData();
    } finally {
      setRemoving(null);
    }
  }

  async function handleRevoke(inviteId: string) {
    setRevoking(inviteId);
    try {
      await apiRequest(`/api/teams/invites/${inviteId}/`, { method: "DELETE" });
      await fetchData();
    } finally {
      setRevoking(null);
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Users className="h-5 w-5 text-slate-400" />
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Team</h1>
            <p className="text-sm text-slate-500">Manage who has access to this workspace.</p>
          </div>
        </div>
        {canManage && (
          <button
            onClick={() => { setInviteOpen(true); setInviteSent(false); setInviteError(""); }}
            className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            <Mail className="h-4 w-4" />
            Invite member
          </button>
        )}
      </div>

      {/* Invite form */}
      {inviteOpen && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-4">
          <p className="text-sm font-medium text-slate-800">Send an invite email</p>
          <p className="text-xs text-slate-500">
            An invite link valid for 7 days will be emailed to the address below.
            The person doesn&apos;t need a MateMail account yet.
          </p>
          {inviteSent ? (
            <p className="flex items-center gap-2 text-sm text-emerald-700">
              <Mail className="h-4 w-4" /> Invite sent!
            </p>
          ) : (
            <>
              {inviteError && <p className="text-sm text-red-600">{inviteError}</p>}
              <form onSubmit={handleInvite} className="space-y-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-700">Email address</label>
                  <input
                    type="email"
                    placeholder="colleague@example.com"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-700">Role</label>
                  <select
                    value={inviteRole}
                    onChange={(e) => setInviteRole(e.target.value)}
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
                  >
                    {ROLES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label} — {r.desc}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex gap-3 pt-1">
                  <button
                    type="submit"
                    disabled={inviting || !inviteEmail.trim()}
                    className="rounded-md bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
                  >
                    {inviting ? "Sending…" : "Send invite"}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setInviteOpen(false); setInviteError(""); setInviteEmail(""); }}
                    className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            </>
          )}
        </div>
      )}

      {/* Pending invites */}
      {invites.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Pending invites
          </p>
          <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {invites.map((inv) => (
              <div key={inv.id} className="flex items-center gap-4 px-5 py-3">
                <Clock className="h-4 w-4 shrink-0 text-amber-400" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-slate-800">{inv.email}</p>
                  <p className="text-xs text-slate-400">
                    {inv.role.replace("_", " ")} · expires{" "}
                    {new Date(inv.expires_at).toLocaleDateString()}
                  </p>
                </div>
                {canManage && (
                  <button
                    onClick={() => handleRevoke(inv.id)}
                    disabled={revoking === inv.id}
                    title="Revoke invite"
                    className="rounded p-1 text-slate-300 hover:bg-red-50 hover:text-red-500 disabled:opacity-40"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Member list */}
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Members
        </p>
        {loading ? (
          <div className="py-12 text-center text-sm text-slate-400">Loading members…</div>
        ) : (
          <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {members.map((m) => {
              const isSelf = m.email === user?.email;
              const isOwnerRow = m.role === "owner";
              const canChangeRole = isOwner && !isSelf && !isOwnerRow;
              const canRemove = (isOwner && !isOwnerRow) || (isSelf && !isOwnerRow);

              return (
                <div key={m.id} className="flex items-center gap-4 px-5 py-4">
                  <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-slate-100 text-sm font-semibold text-slate-600">
                    {(m.full_name || m.email).charAt(0).toUpperCase()}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-900">
                      {m.full_name || m.email}
                      {isSelf && <span className="ml-1.5 text-xs text-slate-400">(you)</span>}
                    </p>
                    <p className="truncate text-xs text-slate-400">{m.email}</p>
                  </div>
                  {canChangeRole ? (
                    <div className="relative">
                      <select
                        value={m.role}
                        disabled={roleChanging === m.id}
                        onChange={(e) => handleRoleChange(m.id, e.target.value)}
                        className="appearance-none rounded-md border border-slate-200 py-1 pl-2.5 pr-7 text-xs font-medium text-slate-700 hover:border-slate-300 focus:outline-none focus:ring-1 focus:ring-cyan-500 disabled:opacity-50"
                      >
                        {ROLES.map((r) => (
                          <option key={r.value} value={r.value}>{r.label}</option>
                        ))}
                      </select>
                      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-400" />
                    </div>
                  ) : (
                    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${ROLE_STYLES[m.role] ?? ROLE_STYLES.read_only}`}>
                      {m.role.replace("_", " ")}
                    </span>
                  )}
                  {canRemove ? (
                    <button
                      onClick={() => handleRemove(m.id)}
                      disabled={removing === m.id}
                      title={isSelf ? "Leave workspace" : "Remove member"}
                      className="ml-1 rounded p-1 text-slate-300 hover:bg-red-50 hover:text-red-500 disabled:opacity-40"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  ) : (
                    <div className="ml-1 w-6" />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Role legend */}
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 space-y-2">
        <p className="text-xs font-medium text-slate-600">Role permissions</p>
        <div className="space-y-1">
          {[
            { role: "Owner",     desc: "Full control, including billing and workspace deletion" },
            { role: "Admin",     desc: "Manage domains, mailboxes, aliases, and team members" },
            { role: "Support",   desc: "View mailboxes, logs, and queue — no changes" },
            { role: "Read-only", desc: "View-only access to all sections" },
          ].map(({ role, desc }) => (
            <div key={role} className="flex items-start gap-2 text-xs text-slate-500">
              <span className="w-20 shrink-0 font-medium text-slate-700">{role}</span>
              <span>{desc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
