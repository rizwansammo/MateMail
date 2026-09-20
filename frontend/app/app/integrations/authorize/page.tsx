"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { apiRequest } from "@/lib/api";

type Permission = { key: string; label: string };
type Details = {
  name: string;
  purpose_label: string;
  organization: string;
  mailbox_email: string;
  mailbox_name: string;
  permissions: Permission[];
  user_2fa_required: boolean;
  mailbox_verification_required: boolean;
  mailbox_verification_label: string;
  request_status: string;
};

function AuthorizationContent() {
  const params = useSearchParams();
  const requestToken = params.get("request") || "";
  const [details, setDetails] = useState<Details | null>(null);
  const [password, setPassword] = useState("");
  const [twoFactorCode, setTwoFactorCode] = useState("");
  const [mailboxFactorCode, setMailboxFactorCode] = useState("");
  const [error, setError] = useState("");
  const [approved, setApproved] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!requestToken) return;
    void (async () => {
      const res = await apiRequest(
        "/api/integrations/authorize/?request=" + encodeURIComponent(requestToken)
      );
      const body = await res.json().catch(() => ({}));
      if (res.status === 401) {
        const next = window.location.pathname + window.location.search;
        window.location.href = "/login?next=" + encodeURIComponent(next);
        return;
      }
      if (!res.ok) {
        setError(body.detail || "This connection request could not be loaded.");
        return;
      }
      setDetails(body);
      setApproved(body.request_status === "approved");
    })();
  }, [requestToken]);

  async function approve(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const res = await apiRequest(
        "/api/integrations/authorize/?request=" + encodeURIComponent(requestToken),
        {
          method: "POST",
          body: JSON.stringify({
            password,
            two_factor_code: twoFactorCode,
            mailbox_factor_code: mailboxFactorCode,
          }),
        }
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const message =
          body.password?.[0] ||
          body.two_factor_code?.[0] ||
          body.mailbox_factor_code?.[0] ||
          body.detail ||
          "Authorization failed.";
        setError(message);
        return;
      }
      setApproved(true);
    } finally {
      setSubmitting(false);
    }
  }

  if (!requestToken) {
    return <div className="p-6 text-sm text-red-600">Missing connection request.</div>;
  }

  if (approved) {
    return (
      <div className="mx-auto mt-16 max-w-lg border border-emerald-200 bg-white p-6 text-center">
        <h1 className="text-xl font-semibold text-slate-900">MateMail connected</h1>
        <p className="mt-2 text-sm text-slate-600">
          SalesHub can now use the approved mailbox. You can close this window.
        </p>
        <button onClick={() => window.close()} className="mt-5 bg-slate-900 px-4 py-2 text-sm font-semibold text-white">
          Close window
        </button>
      </div>
    );
  }

  if (!details) {
    return <div className="p-6 text-sm text-slate-500">{error || "Loading connection…"}</div>;
  }

  return (
    <div className="mx-auto mt-10 max-w-lg border border-slate-200 bg-white">
      <div className="border-b border-slate-200 p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700">{details.name}</p>
        <h1 className="mt-1 text-xl font-semibold text-slate-900">Connect MateMail</h1>
        <p className="mt-2 text-sm text-slate-600">
          Approve {details.purpose_label.toLowerCase()} access to one exact mailbox.
        </p>
      </div>

      <div className="space-y-4 p-5">
        <div className="border border-slate-200 p-3">
          <p className="text-xs text-slate-400">Organization</p>
          <p className="font-semibold text-slate-900">{details.organization}</p>
          <p className="mt-3 text-xs text-slate-400">Mailbox</p>
          <p className="font-semibold text-slate-900">
            {details.mailbox_name ? details.mailbox_name + " — " : ""}
            {details.mailbox_email}
          </p>
        </div>

        <div className="border border-slate-200 p-3">
          <p className="text-sm font-semibold text-slate-800">SalesHub will be able to:</p>
          <ul className="mt-2 space-y-2 text-sm text-slate-700">
            {details.permissions.map((permission) => (
              <li key={permission.key}>✓ {permission.label}</li>
            ))}
          </ul>
        </div>

        <form onSubmit={approve} className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs font-semibold text-slate-600">Confirm your MateMail password</span>
            <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} className="w-full border border-slate-300 px-3 py-2 text-sm" autoComplete="current-password" />
          </label>

          {details.user_2fa_required && (
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-slate-600">Authentication code</span>
              <input required value={twoFactorCode} onChange={(e) => setTwoFactorCode(e.target.value)} className="w-full border border-slate-300 px-3 py-2 text-sm" inputMode="numeric" autoComplete="one-time-code" />
              <p className="mt-1 text-xs text-slate-400">Use your authenticator code or a backup code.</p>
            </label>
          )}

          {details.mailbox_verification_required && (
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-slate-600">{details.mailbox_verification_label}</span>
              <input required value={mailboxFactorCode} onChange={(e) => setMailboxFactorCode(e.target.value)} className="w-full border border-slate-300 px-3 py-2 text-sm" autoComplete="one-time-code" />
            </label>
          )}

          {error && <div className="text-sm text-red-600">{error}</div>}

          <div className="flex justify-end gap-2 border-t border-slate-200 pt-4">
            <button type="button" onClick={() => window.close()} className="border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700">
              Cancel
            </button>
            <button disabled={submitting} className="bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
              {submitting ? "Connecting…" : "Connect mailbox"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function AuthorizationPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-slate-500">Loading connection…</div>}>
      <AuthorizationContent />
    </Suspense>
  );
}
