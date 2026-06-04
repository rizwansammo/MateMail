"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft, CheckCircle2, AlertCircle, ExternalLink,
  RefreshCw, Mail, Lock, HardDrive,
} from "lucide-react";
import { api, ApiError, apiRequest } from "@/lib/api";

interface Mailbox {
  id: string;
  email: string;
  full_name: string;
  local_part: string;
  domain: string;
  domain_name: string;
  status: "active" | "disabled" | "suspended";
  quota_mb: number;
  storage_used_mb: number;
  mail_engine_provisioned: boolean;
  mail_engine_error: string;
  last_login: string | null;
  created_at: string;
}

const STATUS_STYLES: Record<string, string> = {
  active:    "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  disabled:  "bg-slate-100  text-slate-500   ring-slate-400/20",
  suspended: "bg-red-50     text-red-700     ring-red-600/20",
};

export default function MailboxDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();

  const [mailbox, setMailbox] = useState<Mailbox | null>(null);
  const [loading, setLoading] = useState(true);

  // SSO state
  const [ssoLoading, setSsoLoading] = useState(false);
  const [ssoError, setSsoError] = useState("");

  // Status toggle state
  const [statusLoading, setStatusLoading] = useState(false);

  // Re-provision state
  const [reprovOpen, setReprovOpen] = useState(false);
  const [reprovPassword, setReprovPassword] = useState("");
  const [reprovLoading, setReprovLoading] = useState(false);
  const [reprovError, setReprovError] = useState("");

  const fetchMailbox = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiRequest(`/api/mailboxes/${params.id}/`);
      if (res.ok) setMailbox(await res.json());
    } finally {
      setLoading(false);
    }
  }, [params.id]);

  useEffect(() => { fetchMailbox(); }, [fetchMailbox]);

  async function openWebmail() {
    if (!mailbox) return;
    setSsoError("");
    setSsoLoading(true);
    try {
      const res = await apiRequest(`/api/webmail/sso/?mailbox_id=${mailbox.id}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setSsoError(body.detail ?? "Could not open webmail.");
        return;
      }
      const { url } = await res.json();
      window.open(url, "_blank", "noopener,noreferrer");
    } finally {
      setSsoLoading(false);
    }
  }

  async function toggleStatus() {
    if (!mailbox || mailbox.status === "suspended") return;
    const newStatus = mailbox.status === "active" ? "disabled" : "active";
    setStatusLoading(true);
    try {
      const res = await apiRequest(`/api/mailboxes/${params.id}/status/`, {
        method: "PATCH",
        body: JSON.stringify({ status: newStatus }),
      });
      if (res.ok) setMailbox(await res.json());
    } finally {
      setStatusLoading(false);
    }
  }

  async function reprovision(e: React.FormEvent) {
    e.preventDefault();
    setReprovError("");
    setReprovLoading(true);
    try {
      const res = await apiRequest(`/api/mailboxes/${params.id}/reprovision/`, {
        method: "POST",
        body: JSON.stringify({ password: reprovPassword }),
      });
      if (res.ok) {
        setMailbox(await res.json());
        setReprovOpen(false);
        setReprovPassword("");
      } else {
        const body = await res.json().catch(() => ({}));
        setReprovError(body.detail ?? "Re-provisioning failed.");
      }
    } catch (err) {
      if (err instanceof ApiError) setReprovError(err.message);
    } finally {
      setReprovLoading(false);
    }
  }

  if (loading) {
    return <div className="py-24 text-center text-sm text-slate-400">Loading mailbox…</div>;
  }

  if (!mailbox) {
    return (
      <div className="py-24 text-center">
        <p className="text-sm text-slate-500">Mailbox not found.</p>
        <button
          onClick={() => router.push("/app/mailboxes")}
          className="mt-3 text-sm text-cyan-600 hover:text-cyan-700"
        >
          Back to mailboxes
        </button>
      </div>
    );
  }

  const usagePct = mailbox.quota_mb > 0
    ? Math.min(100, Math.round((mailbox.storage_used_mb / mailbox.quota_mb) * 100))
    : 0;
  const usageColor = usagePct >= 90 ? "bg-red-500" : usagePct >= 70 ? "bg-amber-500" : "bg-emerald-500";
  const canToggle = mailbox.status !== "suspended";

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <button
            onClick={() => router.push("/app/mailboxes")}
            className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Mailboxes
          </button>
          <h1 className="text-xl font-semibold text-slate-900">{mailbox.email}</h1>
          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[mailbox.status]}`}>
              {mailbox.status}
            </span>
            {mailbox.mail_engine_provisioned ? (
              <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
                <CheckCircle2 className="h-3.5 w-3.5" /> Provisioned
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs text-amber-500">
                <AlertCircle className="h-3.5 w-3.5" /> Not provisioned
              </span>
            )}
          </div>
        </div>

        {/* Open Webmail */}
        <button
          onClick={openWebmail}
          disabled={ssoLoading || !mailbox.mail_engine_provisioned || mailbox.status !== "active"}
          className="inline-flex items-center gap-1.5 rounded-md bg-cyan-600 px-3 py-2 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50 disabled:cursor-not-allowed"
          title={!mailbox.mail_engine_provisioned ? "Mailbox not yet provisioned" : mailbox.status !== "active" ? "Mailbox is not active" : "Open webmail"}
        >
          {ssoLoading ? (
            <RefreshCw className="h-4 w-4 animate-spin" />
          ) : (
            <ExternalLink className="h-4 w-4" />
          )}
          {ssoLoading ? "Opening…" : "Open Webmail"}
        </button>
      </div>

      {ssoError && (
        <p className="text-sm text-red-600">{ssoError}</p>
      )}

      {/* Info card */}
      <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100">
        <InfoRow icon={<Mail className="h-4 w-4 text-slate-400" />} label="Email">
          {mailbox.email}
        </InfoRow>
        <InfoRow icon={<span className="h-4 w-4 text-slate-400 text-xs font-mono">Fn</span>} label="Display name">
          {mailbox.full_name}
        </InfoRow>
        <InfoRow icon={<HardDrive className="h-4 w-4 text-slate-400" />} label="Storage quota">
          <div className="flex items-center gap-3">
            <div className="h-2 w-28 rounded-full bg-slate-100 overflow-hidden">
              <div className={`h-full rounded-full ${usageColor}`} style={{ width: `${usagePct}%` }} />
            </div>
            <span className="text-sm text-slate-500">
              {Math.round(mailbox.storage_used_mb / 1024 * 10) / 10} GB of {Math.round(mailbox.quota_mb / 1024)} GB used
            </span>
          </div>
        </InfoRow>
        <InfoRow icon={<span className="h-4 w-4" />} label="Last login">
          {mailbox.last_login ? new Date(mailbox.last_login).toLocaleString() : "Never"}
        </InfoRow>
        <InfoRow icon={<span className="h-4 w-4" />} label="Created">
          {new Date(mailbox.created_at).toLocaleDateString()}
        </InfoRow>
      </div>

      {/* Mail engine status */}
      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-slate-800">Mail engine</p>
          {!mailbox.mail_engine_provisioned && (
            <button
              onClick={() => setReprovOpen(true)}
              className="text-xs font-medium text-cyan-600 hover:text-cyan-700"
            >
              Provision now →
            </button>
          )}
        </div>
        {mailbox.mail_engine_error && (
          <p className="rounded bg-red-50 px-3 py-2 text-xs font-mono text-red-700">
            {mailbox.mail_engine_error}
          </p>
        )}
        {mailbox.mail_engine_provisioned && (
          <button
            onClick={() => setReprovOpen(true)}
            className="text-xs text-slate-400 hover:text-slate-600"
          >
            Change password / re-provision
          </button>
        )}
      </div>

      {/* Re-provision form */}
      {reprovOpen && (
        <div className="rounded-lg border border-slate-200 bg-white p-5 space-y-4">
          <p className="text-sm font-medium text-slate-800">
            {mailbox.mail_engine_provisioned ? "Change mailbox password" : "Provision mailbox"}
          </p>
          <p className="text-xs text-slate-500">
            The new password will be set in the mail engine immediately. It is never stored here.
          </p>
          {reprovError && <p className="text-sm text-red-600">{reprovError}</p>}
          <form onSubmit={reprovision} className="space-y-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">New password</label>
              <input
                type="password"
                placeholder="Min 10 characters"
                value={reprovPassword}
                onChange={(e) => setReprovPassword(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
              />
            </div>
            <div className="flex gap-3">
              <button
                type="submit"
                disabled={reprovLoading || reprovPassword.length < 10}
                className="rounded-md bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
              >
                {reprovLoading ? "Saving…" : "Set password"}
              </button>
              <button
                type="button"
                onClick={() => { setReprovOpen(false); setReprovPassword(""); setReprovError(""); }}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Enable / Disable */}
      {canToggle && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-slate-800">
              {mailbox.status === "active" ? "Disable mailbox" : "Enable mailbox"}
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              {mailbox.status === "active"
                ? "The mailbox will stop accepting and sending mail immediately."
                : "Re-activate this mailbox so it can send and receive mail."}
            </p>
          </div>
          <button
            onClick={toggleStatus}
            disabled={statusLoading}
            className={`rounded-md px-4 py-2 text-sm font-medium disabled:opacity-50 ${
              mailbox.status === "active"
                ? "border border-red-300 text-red-700 hover:bg-red-50"
                : "border border-emerald-300 text-emerald-700 hover:bg-emerald-50"
            }`}
          >
            {statusLoading
              ? "Saving…"
              : mailbox.status === "active" ? "Disable" : "Enable"}
          </button>
        </div>
      )}

      {mailbox.status === "suspended" && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          This mailbox is suspended. Contact your workspace owner to resolve the account status.
        </div>
      )}

      {/* Password note */}
      <div className="flex items-start gap-2 text-xs text-slate-400">
        <Lock className="h-3.5 w-3.5 mt-0.5 shrink-0" />
        <span>Mailbox passwords are never stored here. Set or change them via the &quot;Set password&quot; form above.</span>
      </div>
    </div>
  );
}

function InfoRow({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <div className="flex items-center gap-2 w-32 shrink-0">
        {icon}
        <span className="text-xs text-slate-400">{label}</span>
      </div>
      <div className="flex-1 text-sm text-slate-800">{children}</div>
    </div>
  );
}
