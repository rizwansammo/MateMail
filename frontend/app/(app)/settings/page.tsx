"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/contexts/auth-context";
import { api, ApiError, apiRequest } from "@/lib/api";
import { CheckCircle2, Key, Settings } from "lucide-react";

interface WorkspaceDetail {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
  domain_count: number;
  mailbox_count: number;
  member_count: number;
  my_role: string;
  created_at: string;
}

const STATUS_STYLES: Record<string, string> = {
  trial:     "bg-cyan-50 text-cyan-700 ring-cyan-600/20",
  active:    "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  past_due:  "bg-amber-50 text-amber-700 ring-amber-600/20",
  suspended: "bg-red-50 text-red-700 ring-red-600/20",
  cancelled: "bg-slate-100 text-slate-500 ring-slate-400/20",
};

export default function SettingsPage() {
  const { tenant, setAuthResult } = useAuth();
  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null);
  const [loading, setLoading] = useState(true);

  // Rename state
  const [nameEdit, setNameEdit] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState("");
  const [renameSuccess, setRenameSuccess] = useState(false);

  async function fetchWorkspace() {
    if (!tenant?.id) return;
    setLoading(true);
    try {
      const res = await apiRequest(`/api/workspaces/${tenant.id}/`);
      if (res.ok) {
        const data = await res.json();
        setWorkspace(data);
        setNameEdit(data.name);
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchWorkspace(); }, [tenant?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleRename(e: React.FormEvent) {
    e.preventDefault();
    if (!tenant?.id || !nameEdit.trim()) return;
    setRenameError("");
    setRenameSuccess(false);
    setRenaming(true);
    try {
      const res = await apiRequest(`/api/workspaces/${tenant.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ name: nameEdit.trim() }),
      });
      if (res.ok) {
        const data = await res.json();
        setWorkspace(data);
        setRenameSuccess(true);
        setTimeout(() => setRenameSuccess(false), 3000);
      } else {
        const body = await res.json().catch(() => ({}));
        setRenameError(body.detail ?? "Failed to rename workspace.");
      }
    } catch (err) {
      if (err instanceof ApiError) setRenameError(err.message);
    } finally {
      setRenaming(false);
    }
  }

  const canEdit = workspace?.my_role === "owner" || workspace?.my_role === "admin";

  if (loading) {
    return <div className="p-6 text-sm text-slate-400">Loading…</div>;
  }

  if (!workspace) {
    return <div className="p-6 text-sm text-slate-500">Could not load workspace settings.</div>;
  }

  return (
    <div className="flex flex-col gap-6 p-6 max-w-2xl">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Settings className="h-5 w-5 text-slate-400" />
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Settings</h1>
          <p className="text-sm text-slate-500">Workspace configuration</p>
        </div>
      </div>

      {/* Workspace info */}
      <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100">
        <InfoRow label="Workspace ID">
          <span className="font-mono text-xs text-slate-500">{workspace.id}</span>
        </InfoRow>
        <InfoRow label="URL slug">
          <span className="font-mono text-sm text-slate-700">{workspace.slug}</span>
        </InfoRow>
        <InfoRow label="Plan">
          <span className="capitalize text-sm text-slate-700">{workspace.plan}</span>
        </InfoRow>
        <InfoRow label="Status">
          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[workspace.status] ?? STATUS_STYLES.active}`}>
            {workspace.status.replace("_", " ")}
          </span>
        </InfoRow>
        <InfoRow label="Domains">{workspace.domain_count}</InfoRow>
        <InfoRow label="Mailboxes">{workspace.mailbox_count}</InfoRow>
        <InfoRow label="Members">{workspace.member_count}</InfoRow>
        <InfoRow label="Your role">
          <span className="capitalize text-sm text-slate-700">{workspace.my_role?.replace("_", " ")}</span>
        </InfoRow>
        <InfoRow label="Created">
          {new Date(workspace.created_at).toLocaleDateString(undefined, {
            year: "numeric", month: "long", day: "numeric",
          })}
        </InfoRow>
      </div>

      {/* Rename workspace */}
      {canEdit && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-4">
          <div>
            <p className="text-sm font-medium text-slate-800">Workspace name</p>
            <p className="mt-0.5 text-xs text-slate-500">This name appears in the sidebar and email headers.</p>
          </div>

          {renameError && <p className="text-sm text-red-600">{renameError}</p>}

          <form onSubmit={handleRename} className="flex items-center gap-3">
            <input
              type="text"
              value={nameEdit}
              onChange={(e) => setNameEdit(e.target.value)}
              maxLength={255}
              className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            />
            <button
              type="submit"
              disabled={renaming || !nameEdit.trim() || nameEdit.trim() === workspace.name}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {renaming ? "Saving…" : "Save"}
            </button>
          </form>

          {renameSuccess && (
            <p className="flex items-center gap-1.5 text-sm text-emerald-600">
              <CheckCircle2 className="h-4 w-4" />
              Workspace renamed successfully.
            </p>
          )}
        </div>
      )}

      {/* Read-only note for non-admins */}
      {!canEdit && (
        <p className="text-sm text-slate-400">
          Only workspace owners and admins can change settings.
        </p>
      )}

      {/* API Keys */}
      {canEdit && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-800">API Keys</p>
              <p className="mt-0.5 text-xs text-slate-500">
                Generate keys for programmatic access to the MateMail API.
              </p>
            </div>
            <Link
              href="/app/settings/api-keys"
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              <Key className="h-3.5 w-3.5" />
              Manage
            </Link>
          </div>
        </div>
      )}

      {/* Suspended warning */}
      {workspace.status === "suspended" && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <strong>Workspace suspended.</strong> Mail sending and receiving is disabled.
          Contact support to resolve your account status.
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <span className="w-32 shrink-0 text-xs text-slate-400">{label}</span>
      <div className="flex-1 text-sm text-slate-800">{children}</div>
    </div>
  );
}
