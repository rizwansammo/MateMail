"use client";

import { useCallback, useEffect, useState } from "react";
import { MailCheck, ShieldCheck } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { PortalButton, PortalCard, PortalNotice, PortalStatus } from "@/components/workspace/premium-ui";

interface RecoveryStatus {
  verified_email: string | null;
  verified: boolean;
  pending: boolean;
}

export function IndependentRecoveryEmail({ primaryEmail, twoFactorEnabled }: {
  primaryEmail: string; twoFactorEnabled: boolean;
}) {
  const [status, setStatus] = useState<RecoveryStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authenticatorCode, setAuthenticatorCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiRequest("/api/auth/recovery-email/");
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Recovery email information unavailable.");
      setStatus(data as RecoveryStatus);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Recovery email information unavailable.");
    } finally { setLoading(false); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 0);
    return () => window.clearTimeout(timer);
  }, [reload]);

  async function begin(event: React.FormEvent) {
    event.preventDefault();
    if (email.trim().toLowerCase() === primaryEmail.toLowerCase()) {
      setError("Your recovery address must be independent of your Hub sign-in email.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiRequest("/api/auth/recovery-email/", {
        method: "POST",
        body: JSON.stringify({
          recovery_email: email.trim(), current_password: password,
          ...(twoFactorEnabled ? { totp_code: authenticatorCode } : {}),
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Could not send verification email.");
      setChallenge(data.challenge);
      setPassword("");
      setAuthenticatorCode("");
      setNotice("A verification code was sent to the new recovery address.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not send verification email."); }
    finally { setBusy(false); }
  }

  async function confirm(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiRequest("/api/auth/recovery-email/confirm/", {
        method: "POST",
        body: JSON.stringify({ challenge, code: verificationCode }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Verification failed.");
      setChallenge("");
      setVerificationCode("");
      setEmail("");
      setNotice("Independent recovery email verified.");
      await reload();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Verification failed."); }
    finally { setBusy(false); }
  }

  return (
    <PortalCard className="mt-5" title="Independent recovery email" subtitle="Use an external address you can access without signing in to MateMail. It can receive an account recovery code after your password has been verified.">
      {status?.verified && (
        <div className="portal-security-action">
          <MailCheck className="h-5 w-5" aria-hidden="true" />
          <div>
            <strong>{status.verified_email}</strong>
            <span>This address has completed ownership verification.</span>
          </div>
          <PortalStatus value="Verified" />
        </div>
      )}
      {!loading && !status?.verified && (
        <div className="mb-4"><PortalNotice tone="info">No independently verified recovery address is configured yet. Your primary MateMail mailbox is not an acceptable recovery destination.</PortalNotice></div>
      )}
      {status?.pending && !challenge && (
        <div className="mb-4"><PortalNotice tone="info">A pending recovery address requires verification. Start verification again if you no longer have the previous code.</PortalNotice></div>
      )}
      {challenge ? (
        <form onSubmit={confirm}>
          <div className="portal-field">
            <label htmlFor="recovery-verification-code">Six-digit code from your recovery email</label>
            <input id="recovery-verification-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={verificationCode} onChange={(e) => setVerificationCode(e.target.value.replace(/\D/g, "").slice(0, 6))} required />
          </div>
          <div className="portal-detail-actions">
            <PortalButton type="submit" disabled={busy || verificationCode.length !== 6}>{busy ? "Verifying…" : "Verify recovery address"}</PortalButton>
            <PortalButton type="button" variant="secondary" disabled={busy} onClick={() => { setChallenge(""); setVerificationCode(""); }}>Cancel</PortalButton>
          </div>
        </form>
      ) : (
        <form onSubmit={begin}>
          <div className="portal-field">
            <label htmlFor="recovery-email-address">{status?.verified ? "Replace recovery email" : "Recovery email address"}</label>
            <input id="recovery-email-address" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="independent@example.com" required />
          </div>
          <div className="portal-field mt-3">
            <label htmlFor="recovery-current-password">Current Hub account password</label>
            <input id="recovery-current-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {twoFactorEnabled && (
            <div className="portal-field mt-3">
              <label htmlFor="recovery-current-factor">Current authenticator code</label>
              <input id="recovery-current-factor" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={authenticatorCode} onChange={(e) => setAuthenticatorCode(e.target.value.replace(/\D/g, "").slice(0, 6))} required />
            </div>
          )}
          <div className="portal-detail-actions">
            <PortalButton type="submit" disabled={busy || !email || !password || (twoFactorEnabled && authenticatorCode.length !== 6)}>
              <ShieldCheck className="h-4 w-4" />
              {busy ? "Sending…" : "Send verification code"}
            </PortalButton>
          </div>
        </form>
      )}
      {notice && <div className="mt-3"><PortalNotice tone="success">{notice}</PortalNotice></div>}
      {error && <div className="mt-3"><PortalNotice tone="danger">{error}</PortalNotice></div>}
    </PortalCard>
  );
}
