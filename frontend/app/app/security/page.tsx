"use client";

import { useCallback, useEffect, useState } from "react";
import {
  KeyRound,
  LockKeyhole,
  MailCheck,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import { IndependentRecoveryEmail } from "@/components/workspace/independent-recovery-email";
import { AuthenticatorSetupDialog } from "@/components/security/authenticator-setup-dialog";
import { BackupCodesRotationDialog } from "@/components/security/backup-codes-rotation-dialog";
import {
  PortalButton,
  PortalCard,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface AccountProfile {
  id: string;
  email: string;
  full_name: string;
  email_verified: boolean;
  two_factor_enabled: boolean;
  is_platform_admin: boolean;
  created_at: string;
}

interface SetupResponse {
  secret: string;
  uri: string;
}

export default function SecurityPage() {
  const { user } = useAuth();
  const [profile, setProfile] = useState<AccountProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [setupOpen, setSetupOpen] = useState(false);
  const [setup, setSetup] = useState<SetupResponse | null>(null);
  const [setupCode, setSetupCode] = useState("");
  const [setupBusy, setSetupBusy] = useState(false);
  const [setupError, setSetupError] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [rotationOpen, setRotationOpen] = useState(false);

  const [disableOpen, setDisableOpen] = useState(false);
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [disableBusy, setDisableBusy] = useState(false);
  const [disableError, setDisableError] = useState("");

  const [resetBusy, setResetBusy] = useState(false);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "warn" | "danger">("success");

  const fetchProfile = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await apiRequest("/api/auth/me/");
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setProfile(data);
      } else {
        setLoadError(data?.detail ?? "Account security information could not be loaded.");
      }
    } catch {
      setLoadError("Account security information could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchProfile(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchProfile]);

  async function resendVerification() {
    setVerifyBusy(true);
    setMessage("");
    try {
      const response = await apiRequest("/api/auth/resend-verification/", { method: "POST" });
      const data = await response.json().catch(() => null);
      if (response.ok) {
        setMessageTone("success");
        setMessage(data?.detail ?? "Verification email sent.");
      } else {
        setMessageTone("danger");
        setMessage(data?.detail ?? "Verification email could not be sent.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Verification email could not be sent.");
    } finally {
      setVerifyBusy(false);
    }
  }

  async function requestPasswordReset() {
    const email = profile?.email || user?.email;
    if (!email) return;
    setResetBusy(true);
    setMessage("");
    try {
      const response = await apiRequest("/api/auth/forgot-password/", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok) {
        setMessageTone("success");
        setMessage(data?.detail ?? "If the address is registered, a password-reset email will arrive shortly.");
      } else {
        setMessageTone("danger");
        setMessage(data?.detail ?? "Password reset could not be requested.");
      }
    } catch {
      setMessageTone("danger");
      setMessage("Password reset could not be requested.");
    } finally {
      setResetBusy(false);
    }
  }

  async function beginTwoFactor() {
    setSetupBusy(true);
    setSetupError("");
    setBackupCodes([]);
    try {
      const response = await apiRequest("/api/auth/2fa/setup/");
      const data = await response.json().catch(() => null);
      if (response.ok && data?.secret && data?.uri) {
        setSetup(data);
        setSetupOpen(true);
      } else {
        setSetupError(data?.detail ?? "2FA setup could not be started.");
        setSetupOpen(true);
      }
    } catch {
      setSetupError("2FA setup could not be started.");
      setSetupOpen(true);
    } finally {
      setSetupBusy(false);
    }
  }

  function closeSetup() {
    if (setupBusy) return;
    setSetupOpen(false);
    setSetup(null);
    setSetupCode("");
    setSetupError("");
  }

  function finishRecoveryDisclosure() {
    setBackupCodes([]);
    setSetupOpen(false);
    setSetup(null);
    setSetupCode("");
    setSetupError("");
  }

  async function confirmTwoFactor(event: React.FormEvent) {
    event.preventDefault();
    if (setupCode.length !== 6) return;
    setSetupBusy(true);
    setSetupError("");
    setMessage("");
    try {
      const response = await apiRequest("/api/auth/2fa/setup/", {
        method: "POST",
        body: JSON.stringify({ code: setupCode }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok) {
        const codes = Array.isArray(data?.backup_codes) ? data.backup_codes : [];
        setBackupCodes(codes);
        setSetupCode("");
        setProfile((current) => current ? { ...current, two_factor_enabled: true } : current);
        // Keep the setup modal open until the user has explicitly acknowledged
        // the one-time recovery codes (they cannot be viewed again).
        setMessageTone("success");
        setMessage("Two-factor authentication is enabled.");
      } else {
        setSetupError(data?.detail ?? "The authenticator code was not accepted.");
      }
    } catch {
      setSetupError("The authenticator code was not accepted.");
    } finally {
      setSetupBusy(false);
    }
  }

  async function disableTwoFactor(event: React.FormEvent) {
    event.preventDefault();
    if (!disablePassword || !disableCode.trim()) return;
    setDisableBusy(true);
    setDisableError("");
    setMessage("");
    try {
      const response = await apiRequest("/api/auth/2fa/disable/", {
        method: "POST",
        body: JSON.stringify({ password: disablePassword, code: disableCode.trim() }),
      });
      const data = await response.json().catch(() => null);
      if (response.ok) {
        setProfile((current) => current ? { ...current, two_factor_enabled: false } : current);
        setDisablePassword("");
        setDisableCode("");
        setDisableOpen(false);
        setSetupOpen(false);
        setSetup(null);
        setBackupCodes([]);
        setMessageTone("success");
        setMessage(data?.detail ?? "Two-factor authentication disabled.");
      } else {
        setDisableError(data?.detail ?? "Two-factor authentication could not be disabled.");
      }
    } catch {
      setDisableError("Two-factor authentication could not be disabled.");
    } finally {
      setDisableBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="portal-page astra-resource-page astra-advanced-page astra-security-page">
        <PortalSkeleton className="mb-5 h-20 w-full" />
        <PortalSkeleton className="h-[420px] w-full" />
      </div>
    );
  }

  if (!profile || loadError) {
    return (
      <div className="portal-page astra-resource-page astra-advanced-page astra-security-page">
        <PortalPageHeading title="Account security" description="Protect your MateMail Portal sign-in." />
        <PortalNotice tone="danger">{loadError || "Account security information could not be loaded."}</PortalNotice>
      </div>
    );
  }

  return (
    <div className="portal-page astra-resource-page astra-advanced-page astra-security-page">
      {setupOpen && (
        <AuthenticatorSetupDialog
          setup={setup}
          code={setupCode}
          backupCodes={backupCodes}
          onCodesSaved={finishRecoveryDisclosure}
          error={setupError}
          busy={setupBusy}
          onCodeChange={setSetupCode}
          onSubmit={confirmTwoFactor}
          onRetry={() => void beginTwoFactor()}
          onClose={closeSetup}
        />
      )}
      {rotationOpen && <BackupCodesRotationDialog onClose={() => setRotationOpen(false)} />}
      <PortalPageHeading
        title="Account security"
        description="Protect your MateMail Portal account and manage the security controls the backend currently supports."
        actions={
          <button type="button" className="portal-button secondary" onClick={fetchProfile} disabled={loading}>
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
            Refresh
          </button>
        }
      />

      <div className={"portal-security-banner " + (profile.two_factor_enabled ? "protected" : "attention")}>
        <span className="portal-plan-symbol"><ShieldCheck className="h-5 w-5" /></span>
        <div>
          <strong>{profile.two_factor_enabled ? "Your account has an extra layer of protection" : "Strengthen your Portal sign-in"}</strong>
          <p>{profile.two_factor_enabled ? "Authenticator-based two-factor authentication is enabled." : "Enable an authenticator app to require a second factor after your password."}</p>
        </div>
        <PortalStatus value={profile.two_factor_enabled ? "Protected" : "Needs attention"} />
      </div>

      {message && (
        <div className="mb-5"><PortalNotice tone={messageTone}>{message}</PortalNotice></div>
      )}

      <div className="portal-settings-layout">
        <div>
          <div className="portal-settings-intro">
            <h2>Sign-in & verification</h2>
            <p>Real controls for this MateMail account.</p>
          </div>

          <PortalCard>
            <div className="portal-security-action">
              <MailCheck className="h-5 w-5" />
              <div>
                <strong>Email verification</strong>
                <span>{profile.email_verified ? "Your account email is verified." : "Verification is required for provisioning actions."}</span>
              </div>
              {profile.email_verified ? (
                <PortalStatus value="Verified" />
              ) : (
                <PortalButton type="button" variant="secondary" disabled={verifyBusy} onClick={resendVerification}>
                  {verifyBusy ? "Sending…" : "Resend"}
                </PortalButton>
              )}
            </div>

            <div className="portal-security-action">
              <LockKeyhole className="h-5 w-5" />
              <div>
                <strong>Password</strong>
                <span>MateMail changes passwords through a short-lived reset link sent to {profile.email}.</span>
              </div>
              <PortalButton type="button" variant="secondary" disabled={resetBusy} onClick={requestPasswordReset}>
                {resetBusy ? "Sending…" : "Send reset link"}
              </PortalButton>
            </div>

            <div className="portal-security-action">
              <Smartphone className="h-5 w-5" />
              <div>
                <strong>Two-factor authentication</strong>
                <span>{profile.two_factor_enabled ? "Authenticator app protection is active." : "Use a TOTP-compatible authenticator app."}</span>
              </div>
              {profile.two_factor_enabled ? (
                <PortalButton type="button" variant="secondary" onClick={() => setDisableOpen((value) => !value)}>
                  Disable
                </PortalButton>
              ) : (
                <PortalButton type="button" variant="secondary" disabled={setupBusy} onClick={beginTwoFactor}>
                  {setupBusy ? "Starting…" : "Set up 2FA"}
                </PortalButton>
              )}
            </div>

            {profile.two_factor_enabled && (
              <div className="portal-security-action">
                <KeyRound className="h-5 w-5" aria-hidden="true" />
                <div>
                  <strong>Backup authentication codes</strong>
                  <span>Generate new recovery codes. Previously issued backup codes will be invalidated.</span>
                </div>
                <PortalButton type="button" variant="secondary" onClick={() => setRotationOpen(true)}>
                  Regenerate
                </PortalButton>
              </div>
            )}
          </PortalCard>

          <IndependentRecoveryEmail primaryEmail={profile.email} twoFactorEnabled={profile.two_factor_enabled} />

          {disableOpen && profile.two_factor_enabled && (
            <PortalCard className="mt-5" title="Disable two-factor authentication" subtitle="Enter your current password AND a current authenticator or unused backup code. Organization-required 2FA cannot be disabled.">
              <form onSubmit={disableTwoFactor}>
                {disableError && <div className="mb-4"><PortalNotice tone="danger">{disableError}</PortalNotice></div>}
                <div className="portal-field">
                  <label>Current account password</label>
                  <input
                    type="password"
                    autoComplete="current-password"
                    required
                    value={disablePassword}
                    onChange={(event) => setDisablePassword(event.target.value)}
                  />
                </div>
                <div className="portal-field mt-3">
                  <label htmlFor="disable-factor-code">Current authenticator or backup code</label>
                  <input id="disable-factor-code" autoComplete="one-time-code" value={disableCode} onChange={(event) => setDisableCode(event.target.value.trim().toUpperCase())} required maxLength={64} />
                </div>
                <div className="portal-detail-actions">
                  <PortalButton type="submit" variant="danger" disabled={disableBusy || !disablePassword || !disableCode.trim()}>
                    {disableBusy ? "Disabling…" : "Disable 2FA"}
                  </PortalButton>
                  <PortalButton type="button" variant="secondary" disabled={disableBusy} onClick={() => {
                    setDisableOpen(false);
                    setDisablePassword("");
                    setDisableCode("");
                    setDisableError("");
                  }}>Cancel</PortalButton>
                </div>
              </form>
            </PortalCard>
          )}
        </div>

        <div>
          <div className="portal-settings-intro">
            <h2>Account facts</h2>
            <p>Security-relevant account state from MateMail.</p>
          </div>

          <PortalCard>
            <dl className="portal-detail-list">
              <div className="portal-detail-row"><dt>Email</dt><dd>{profile.email}</dd></div>
              <div className="portal-detail-row"><dt>Email verified</dt><dd>{profile.email_verified ? "Yes" : "No"}</dd></div>
              <div className="portal-detail-row"><dt>Two-factor auth</dt><dd>{profile.two_factor_enabled ? "Enabled" : "Disabled"}</dd></div>
              <div className="portal-detail-row"><dt>Account created</dt><dd>{new Date(profile.created_at).toLocaleString()}</dd></div>
            </dl>
          </PortalCard>

          <div className="mt-5">
            <PortalNotice tone="info">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <span>MateMail does not currently expose an active-device/session inventory or remote per-device sign-out API, so the prototype’s simulated session list is intentionally not shown here.</span>
            </PortalNotice>
          </div>

          {profile.two_factor_enabled && backupCodes.length === 0 && (
            <div className="mt-4">
              <PortalNotice tone="info">
                <KeyRound className="mt-0.5 h-4 w-4 shrink-0" />
                <span>Existing backup codes cannot be viewed again. They are only returned once when 2FA is enabled.</span>
              </PortalNotice>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
