"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ShieldCheck, X } from "lucide-react";
import { apiRequest } from "@/lib/api";

type Policy = "off" | "optional" | "required";
type Status = {
  email: string;
  policy: Policy;
  effective_policy: Policy;
  two_factor_enabled: boolean;
  require_ready: boolean;
  recovery_available?: boolean;
  verified_independent_recovery?: boolean;
};

interface Props {
  tenantId: string;
  memberId: string;
  memberEmail: string;
  onClose: () => void;
  onSaved: () => void;
}

export function AdminHubMfaPolicyDialog({ tenantId, memberId, memberEmail, onClose, onSaved }: Props) {
  const [state, setState] = useState<Status | null>(null);
  const [policy, setPolicy] = useState<Policy>("off");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState(false);
  const [recoveryReason, setRecoveryReason] = useState("");
  const [recoveryPassword, setRecoveryPassword] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [recoverySent, setRecoverySent] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const url = `/api/workspaces/${tenantId}/members/${memberId}/hub-mfa/`;

  useEffect(() => {
    let cancelled = false;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    void (async () => {
      try {
        const response = await apiRequest(url);
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(data?.detail ?? "Security settings could not be loaded.");
        if (!cancelled) {
          setState(data as Status);
          setPolicy(data.policy as Policy);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Security settings could not be loaded.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, [url]);

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); return; }
    if (event.key !== "Tab") return;
    const nodes = Array.from(panel.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((node) => node.getClientRects().length > 0);
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (!first || !last) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy || loading || !state || policy === state.policy || !password ||
        otp.length !== 6 || reason.trim().length < 5) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiRequest(url, {
        method: "PATCH",
        body: JSON.stringify({
          policy,
          current_password: password,
          authenticator_code: otp,
          reason: reason.trim(),
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail ?? "The 2FA policy could not be saved.");
      setPassword("");
      setOtp("");
      onSaved();
      onClose();
    } catch (caught) {
      setPassword("");
      setOtp("");
      setError(caught instanceof Error ? caught.message : "The 2FA policy could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function requestRecovery(event: React.FormEvent) {
    event.preventDefault();
    if (recoveryBusy || !state?.recovery_available || !state.verified_independent_recovery ||
        !state.two_factor_enabled || recoveryReason.trim().length < 10 ||
        !recoveryPassword || recoveryCode.length !== 6) return;
    setRecoveryBusy(true);
    setError("");
    try {
      const response = await apiRequest(`${url}recovery/`, {
        method: "POST",
        body: JSON.stringify({
          reason: recoveryReason.trim(),
          current_password: recoveryPassword,
          authenticator_code: recoveryCode,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Recovery request could not be sent.");
      setRecoverySent(true);
      setRecoveryOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Recovery request could not be sent.");
    } finally {
      setRecoveryPassword("");
      setRecoveryCode("");
      setRecoveryBusy(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[210] flex items-center justify-center overflow-y-auto bg-slate-950/70 p-3 backdrop-blur-[5px] sm:p-6">
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby="hub-admin-mfa-dialog-title"
        onKeyDown={onKeyDown}
        className="relative my-auto w-full max-w-[490px] rounded-3xl border border-slate-200 bg-white p-5 shadow-2xl sm:p-7 dark:border-slate-700 dark:bg-slate-900">
        <button ref={closeButton} type="button" aria-label="Close admin security settings" title="Close"
          disabled={busy || recoveryBusy} onClick={onClose}
          className="absolute right-4 top-4 inline-flex h-9 w-9 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:opacity-40 dark:hover:bg-slate-800">
          <X className="h-[19px] w-[19px]" aria-hidden="true" />
        </button>
        <div className="mb-5 flex flex-col items-center gap-2 text-center">
          <div className="mb-1 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300">
            <ShieldCheck className="h-6 w-6" aria-hidden="true" />
          </div>
          <h2 id="hub-admin-mfa-dialog-title" className="pr-3 text-xl font-bold text-slate-900 dark:text-white">
            Administrator 2FA policy
          </h2>
          <p className="max-w-[410px] break-all text-sm text-slate-500 dark:text-slate-400">{memberEmail}</p>
        </div>
        {loading ? <p className="text-center text-sm text-slate-500">Loading security settings…</p> : (
          <>
            {state && <p className="mb-4 text-sm text-slate-600 dark:text-slate-300">
              Authenticator: <strong>{state.two_factor_enabled ? "Enabled" : "Not configured"}</strong>.
              {state.policy === "required" && !state.require_ready
                ? " Required enforcement is not released yet." : null}
            </p>}
            {error && <p role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">{error}</p>}
            {state && (
              <form onSubmit={save} className="space-y-4">
                <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                  Sign-in policy
                  <select value={policy} onChange={(e) => setPolicy(e.target.value as Policy)}
                    className="mt-2 h-12 w-full rounded-xl border border-slate-300 bg-white px-3 text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-white">
                    <option value="off">Off: manual 2FA available</option>
                    <option value="optional">Optional: prompt with Skip</option>
                    <option value="required" disabled={!state.require_ready}>Required: enrollment before Hub access</option>
                  </select>
                </label>
                <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">
                  An enrolled admin must still pass 2FA on every sign-in, even with the Optional policy.
                  You cannot view or change another administrator&apos;s authenticator codes.
                </p>
                {policy !== state.policy && (
                  <>
                    <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                      Reason for security change
                      <textarea value={reason} onChange={(e) => setReason(e.target.value)} required minLength={5} maxLength={500}
                        rows={2}
                        className="mt-2 w-full rounded-xl border border-slate-300 bg-white p-3 text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-white" />
                    </label>
                    <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                      Your current account password
                      <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)}
                        className="mt-2 h-12 w-full rounded-xl border border-slate-300 bg-white px-3 text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-white" />
                    </label>
                    <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                      Your authenticator code
                      <input type="text" inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" maxLength={6}
                        value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                        className="mt-2 h-12 w-full rounded-xl border border-slate-300 bg-white px-3 text-center font-mono text-lg tracking-widest dark:border-slate-600 dark:bg-slate-800 dark:text-white" />
                    </label>
                    <button type="submit"
                      disabled={busy || !password || otp.length !== 6 || reason.trim().length < 5}
                      className="h-12 w-full rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">
                      {busy ? "Saving…" : "Save security policy"}
                    </button>
                  </>
                )}
              </form>
            )}
            {state?.recovery_available && (
              <section className="mt-5 border-t border-slate-200 pt-5 dark:border-slate-700" aria-labelledby="admin-mfa-recovery-title">
                <h3 id="admin-mfa-recovery-title" className="text-sm font-semibold text-slate-900 dark:text-white">Authenticator recovery</h3>
                <p className="mt-2 text-xs leading-5 text-slate-500 dark:text-slate-400">
                  Send a recovery approval link to this administrator's previously verified independent email.
                  No authenticator or backup code is reset until they confirm the request.
                </p>
                {recoverySent ? (
                  <p role="status" className="mt-3 rounded-xl bg-green-50 p-3 text-sm text-green-800 dark:bg-green-950/30 dark:text-green-200">
                    Recovery approval email sent. The administrator has 30 minutes to confirm.
                  </p>
                ) : !state.two_factor_enabled ? (
                  <p className="mt-3 text-xs text-slate-500">This administrator has no active authenticator to reset.</p>
                ) : !state.verified_independent_recovery ? (
                  <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
                    Verified independent recovery email is missing. The administrator must register one while signed in,
                    or use a separately verified support recovery process.
                  </p>
                ) : !recoveryOpen ? (
                  <button type="button" onClick={() => setRecoveryOpen(true)}
                    className="mt-3 h-10 rounded-xl border border-amber-300 px-4 text-sm font-semibold text-amber-800 hover:bg-amber-50 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-950/30">
                    Request 2FA recovery
                  </button>
                ) : (
                  <form onSubmit={requestRecovery} className="mt-3 space-y-3">
                    <p className="text-xs font-medium text-amber-800 dark:text-amber-200">
                      You are authorizing a security recovery request, not signing in as this administrator.
                    </p>
                    <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                      Recovery reason
                      <textarea minLength={10} maxLength={500} required rows={2}
                        value={recoveryReason} onChange={(e) => setRecoveryReason(e.target.value)}
                        className="mt-1 w-full rounded-xl border border-slate-300 bg-white p-3 dark:border-slate-600 dark:bg-slate-800" />
                    </label>
                    <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                      Your current password
                      <input type="password" autoComplete="current-password" required
                        value={recoveryPassword} onChange={(e) => setRecoveryPassword(e.target.value)}
                        className="mt-1 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 dark:border-slate-600 dark:bg-slate-800" />
                    </label>
                    <label className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                      Your authenticator code
                      <input type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}"
                        maxLength={6} required value={recoveryCode}
                        onChange={(e) => setRecoveryCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                        className="mt-1 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-center font-mono tracking-widest dark:border-slate-600 dark:bg-slate-800" />
                    </label>
                    <div className="flex gap-2">
                      <button type="button" disabled={recoveryBusy} onClick={() => setRecoveryOpen(false)}
                        className="h-11 flex-1 rounded-xl border border-slate-300 text-sm font-semibold dark:border-slate-700">Cancel</button>
                      <button type="submit" disabled={recoveryBusy || recoveryReason.trim().length < 10 ||
                          !recoveryPassword || recoveryCode.length !== 6}
                        className="h-11 flex-1 rounded-xl bg-amber-600 px-3 text-sm font-semibold text-white disabled:opacity-50">
                        {recoveryBusy ? "Sending…" : "Send approval link"}
                      </button>
                    </div>
                  </form>
                )}
              </section>
            )}
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
