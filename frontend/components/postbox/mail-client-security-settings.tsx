"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronUp, KeyRound, Laptop, Loader2, LockKeyhole, ShieldCheck, ShieldOff } from "lucide-react";
import { AuthenticatorSetupCode } from "@/components/security/authenticator-setup-code";
import { describePostBoxError, usePostBox } from "@/contexts/postbox-context";
import { postbox, PostBoxError, type MailSecurityState, type MailSecurityOneTimeSecret } from "@/lib/postbox-api";

type Action = "create" | "revoke" | "access";

export function MailClientSecuritySettings() {
  const { refresh: refreshMailboxSession } = usePostBox();
  const [loadFailure, setLoadFailure] = useState("");
  const [state, setState] = useState<MailSecurityState | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [label, setLabel] = useState("");
  const [secret, setSecret] = useState<MailSecurityOneTimeSecret | null>(null);
  const [enrollment, setEnrollment] = useState<{ partial: string; secret: string; uri: string } | null>(null);
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [enrollmentPassword, setEnrollmentPassword] = useState("");
  const [enrollmentCode, setEnrollmentCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [rotationPassword, setRotationPassword] = useState("");
  const [rotationCode, setRotationCode] = useState("");
  const [showClientForm, setShowClientForm] = useState(false);
  const [showClientAccess, setShowClientAccess] = useState(false);
  const [showBackupRotation, setShowBackupRotation] = useState(false);
  const [showDisable2fa, setShowDisable2fa] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const current = await postbox.clientSecurity();
      setState(current);
      setUnavailable(false);
      setLoadFailure("");
    } catch (caught) {
      setUnavailable(caught instanceof PostBoxError && caught.status === 503);
      // Never hide a newly issued one-time credential or backup codes if the
      // follow-up list request fails; retain last known state.
      setLoadFailure(describePostBoxError(caught, "Could not load mailbox security."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  async function modify(action: Action, extra: { label?: string; credential_id?: string; enabled?: boolean }) {
    if (!state || !password) return;
    if (state.two_factor_enabled && code.length !== 6) {
      setError("Enter the current six-digit authenticator code.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await postbox.manageClientSecurity({
        action, current_password: password,
        ...(state.two_factor_enabled ? { code } : {}), ...extra,
      });
      if (action === "create") {
        setSecret(result as MailSecurityOneTimeSecret);
        setNotice("New SMTP/IMAP Password created. Save it now; it cannot be displayed again.");
        setLabel("");
      } else {
        setNotice(action === "revoke" ? "Password revoked. Reconnect mail clients if needed." : "External mail-client access updated.");
      }
      setPassword("");
      setCode("");
      if (action !== "create") {
        if (action === "revoke") setRevokeTarget(null);
        await refresh(); // One-time password stays visible.
      }

    } catch (caught) {
      setError(describePostBoxError(caught, "Security operation could not be completed."));
    } finally { setBusy(false); }
  }

  async function startEnrollment() {
    if (!enrollmentPassword) return;
    setBusy(true);
    setError("");
    try {
      const challenge = await postbox.startMailbox2fa(enrollmentPassword);
      const setup = await postbox.beginMailbox2fa(challenge.partial_token);
      setEnrollment({ partial: challenge.partial_token, ...setup });
      setEnrollmentPassword("");
    } catch (caught) { setError(describePostBoxError(caught, "Authenticator setup could not begin.")); }
    finally { setBusy(false); }
  }

  async function finishEnrollment() {
    if (!enrollment || enrollmentCode.length !== 6) return;
    setBusy(true);
    setError("");
    try {
      const result = await postbox.confirmMailbox2fa(enrollment.partial, enrollmentCode);
      setBackupCodes(result.backup_codes);
      if (result.smtp_imap_password && result.smtp_imap_password_id) {
        setSecret({ id: result.smtp_imap_password_id, one_time_credential: result.smtp_imap_password });
      }
      setEnrollment(null);
      setEnrollmentCode("");
      setNotice("Mailbox two-factor authentication enabled. Save all one-time recovery credentials before leaving this page.");
      refreshMailboxSession();
      await refresh();
    } catch (caught) { setError(describePostBoxError(caught, "Authenticator verification failed.")); }
    finally { setBusy(false); }
  }

  async function regenerateBackupCodes() {
    if (busy || !rotationPassword || rotationCode.length !== 6) return;
    if (!window.confirm("Regenerate all 10 backup codes? Every previous unused code will immediately stop working.")) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await postbox.regenerateMailboxBackupCodes(rotationPassword, rotationCode);
      if (result.backup_codes.length !== 10) {
        throw new Error("Backup code regeneration returned an unexpected result.");
      }
      setBackupCodes(result.backup_codes);
      setRotationPassword("");
      setRotationCode("");
      setNotice("New backup codes generated. Save them now; all previous backup codes have been invalidated.");
    } catch (caught) {
      setError(describePostBoxError(caught, "Could not regenerate backup codes."));
    } finally { setBusy(false); }
  }

  async function disableMailbox2fa() {
    if (!disablePassword || disableCode.length !== 6) return;
    if (!window.confirm("Disable mailbox 2FA and revoke external client passwords? You will be signed out.")) return;
    setBusy(true);
    setError("");
    try {
      await postbox.disableMailbox2fa(disablePassword, disableCode);
      setDisablePassword("");
      setDisableCode("");
      // Backend revokes all existing PostBox sessions. A full navigation
      // refreshes the session boundary and cannot retain stale mailbox state.
      window.location.replace("/postbox/login");
    } catch (caught) {
      setError(describePostBoxError(caught, "Two-factor authentication could not be disabled."));
    } finally { setBusy(false); }
  }

  if (loading) return <div className="pb-info-box"><Loader2 className="h-4 w-4 animate-spin" /> Loading SMTP/IMAP Security…</div>;
  if (!state) return (
    <div className="pb-info-box" role="status">
      <ShieldOff className="h-5 w-5" />
      <div>
        <strong>{unavailable ? "Advanced mail-client security is unavailable" : "Could not load mailbox security"}</strong>
        <p>{unavailable
          ? "This control may be disabled until the security rollout. Existing mail access remains unchanged."
          : loadFailure || "Please try again or sign in to your mailbox again."}</p>
        <button type="button" className="pb-settings-secondary" onClick={() => { setLoading(true); void refresh(); }}>Retry</button>
      </div>
    </div>
  );

  return (
    <div className="pb-mail-client-security pb-security-redesign">
      <div className="pb-security-overview" aria-label="Current mailbox security">
        <div className="pb-security-overview-heading">
          <div className="pb-security-heading-icon"><ShieldCheck className="h-5 w-5" aria-hidden="true" /></div>
          <div className="min-w-0">
            <span className="pb-security-overline">Your mailbox</span>
            <h2 className="pb-security-mailbox-address">{state.mailbox_address}</h2>
            <p>These controls protect the account you signed in with, even when viewing a shared mailbox.</p>
          </div>
        </div>
        <div className="pb-security-status-grid">
          <div className="pb-security-status-item">
            <span>Sign-in protection</span>
            <strong>{state.two_factor_enabled ? "Authenticator enabled" : "Password only"}</strong>
            <small>{state.two_factor_enabled ? "A second step protects your PostBox sign-in." : "Set up an authenticator for stronger protection."}</small>
          </div>
          <div className="pb-security-status-item">
            <span>External mail apps</span>
            <strong>{state.enabled ? "Access enabled" : "Access disabled"}</strong>
            <small>{state.enabled ? "IMAP and SMTP are available." : "Mail client sign-ins are blocked."}</small>
          </div>
          <div className="pb-security-status-item">
            <span>App passwords</span>
            <strong>{state.credentials.filter((item) => !item.revoked_at).length} active</strong>
            <small>{state.dedicated_required ? "Required for external mail apps." : "Not required until 2FA is enabled."}</small>
          </div>
        </div>
      </div>

      {(secret || backupCodes.length > 0) && (
        <section className="pb-setting-card pb-security-one-time" aria-label="One-time recovery credentials">
          <header>
            <h2><LockKeyhole className="h-4 w-4" aria-hidden="true" /> Save your new credentials</h2>
            <p>This information is displayed only once. Store it securely before you dismiss it.</p>
          </header>
          <div className="pb-security-card-body">
            {secret && (
              <div className="pb-security-secret-group">
                <strong>SMTP/IMAP app password</strong>
                <code className="pb-security-secret-value">{secret.one_time_credential}</code>
                <div className="pb-security-actions">
                  <button type="button" className="pb-settings-secondary" onClick={() => void navigator.clipboard.writeText(secret.one_time_credential)}>
                    Copy app password
                  </button>
                  <button type="button" className="pb-settings-secondary" onClick={() => { setSecret(null); void refresh(); }}>
                    <Check className="h-4 w-4" aria-hidden="true" /> I saved this password
                  </button>
                </div>
              </div>
            )}
            {backupCodes.length > 0 && (
              <div className="pb-security-secret-group">
                <strong>10 single-use backup codes</strong>
                <p>Each code can recover your sign-in once. Old codes are invalid after regeneration.</p>
                <div className="pb-security-recovery-grid">
                  {backupCodes.map((backup) => <code key={backup}>{backup}</code>)}
                </div>
                <div className="pb-security-actions">
                  <button type="button" className="pb-settings-secondary" onClick={() => void navigator.clipboard.writeText(backupCodes.join("\n"))}>Copy backup codes</button>
                  <button type="button" className="pb-settings-secondary" onClick={() => setBackupCodes([])}>
                    <Check className="h-4 w-4" aria-hidden="true" /> I saved these codes
                  </button>
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      <section className="pb-setting-card pb-security-section pb-security-primary-section">
        <header className="pb-security-card-header">
          <div className="pb-security-section-head">
            <span className="pb-security-section-icon"><ShieldCheck className="h-5 w-5" aria-hidden="true" /></span>
            <div>
              <span className="pb-security-overline">01 · Account protection</span>
              <h2>Two-factor authentication</h2>
              <p>Protect your PostBox login with a six-digit code from an authenticator app.</p>
            </div>
          </div>
          <span className={state.two_factor_enabled ? "pb-settings-pill" : "pb-settings-pill neutral"}>
            {state.two_factor_enabled ? "Protected" : "Not set up"}
          </span>
        </header>
        {!state.two_factor_enabled ? (
          <div className="pb-security-card-body">
            {!enrollment ? (
              <>
                <div className="pb-security-explanation">
                  <strong>Recommended first step</strong>
                  <p>Enter your current mailbox password to connect Google Authenticator or another authenticator. After setup, you will receive backup codes and a separate SMTP/IMAP password.</p>
                </div>
                <div className="pb-security-form">
                  <label className="pb-premium-field" htmlFor="pb-mfa-enrollment-password">Current mailbox password
                    <input id="pb-mfa-enrollment-password" type="password" autoComplete="current-password" value={enrollmentPassword} onChange={(event) => setEnrollmentPassword(event.target.value)} />
                  </label>
                  <div className="pb-security-actions">
                    <button type="button" className="pb-settings-primary" disabled={busy || !enrollmentPassword} onClick={() => void startEnrollment()}>
                      {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <ShieldCheck className="h-4 w-4" aria-hidden="true" />}
                      Set up mailbox 2FA
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <div className="pb-security-enrollment">
                <div className="pb-security-explanation">
                  <strong>Step 2 of 2 — Scan and verify</strong>
                  <p>Scan the code with your authenticator app, then enter its current six-digit code below. Keep the setup key private.</p>
                </div>
                <AuthenticatorSetupCode secret={enrollment.secret} uri={enrollment.uri} compact />
                <div className="pb-security-form">
                  <label className="pb-premium-field" htmlFor="pb-mfa-confirm-code">Six-digit authenticator code
                    <input id="pb-mfa-confirm-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={enrollmentCode} onChange={(e) => setEnrollmentCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
                  </label>
                  <div className="pb-security-actions">
                    <button type="button" className="pb-settings-primary" disabled={busy || enrollmentCode.length !== 6} onClick={() => void finishEnrollment()}>Verify and enable 2FA</button>
                    <button type="button" className="pb-settings-secondary" disabled={busy} onClick={() => { setEnrollment(null); setEnrollmentCode(""); }}>Cancel setup</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="pb-security-card-body">
            <div className="pb-security-explanation pb-security-explanation-positive">
              <strong>Your account has authenticator protection</strong>
              <p>Each new PostBox sign-in needs your password and a second factor. Keep your backup codes in a password manager.</p>
            </div>
            <div className="pb-security-inline-option">
              <div><strong>Backup codes</strong><p>Replace your 10 single-use recovery codes if they are lost or compromised.</p></div>
              <button type="button" className="pb-settings-secondary" aria-expanded={showBackupRotation} aria-controls="pb-backup-rotation" onClick={() => setShowBackupRotation((value) => !value)}>
                {showBackupRotation ? "Close" : "Regenerate"} {showBackupRotation ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </button>
            </div>
            {showBackupRotation && (
              <div id="pb-backup-rotation" className="pb-security-nested-form">
                <p>All old backup codes will stop working immediately. Enter your password and a current authenticator code.</p>
                <div className="pb-security-fields">
                  <label className="pb-premium-field" htmlFor="pb-recovery-password">Current mailbox password
                    <input id="pb-recovery-password" type="password" autoComplete="current-password" value={rotationPassword} onChange={(e) => setRotationPassword(e.target.value)} />
                  </label>
                  <label className="pb-premium-field" htmlFor="pb-recovery-code">Authenticator code
                    <input id="pb-recovery-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={rotationCode} onChange={(e) => setRotationCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
                  </label>
                </div>
                <button type="button" className="pb-settings-secondary" disabled={busy || !rotationPassword || rotationCode.length !== 6} onClick={() => void regenerateBackupCodes()}>Generate new backup codes</button>
              </div>
            )}
            {state.organization_policy === "require" ? (
              <p className="pb-security-policy-note">Your organization requires 2FA; it cannot be disabled here.</p>
            ) : (
              <>
                <div className="pb-security-inline-option">
                  <div><strong>Turn off two-factor authentication</strong><p>Disables this login protection and revokes your external app passwords. Use only if necessary.</p></div>
                  <button type="button" className="pb-settings-secondary" aria-expanded={showDisable2fa} aria-controls="pb-disable-mfa" onClick={() => setShowDisable2fa((value) => !value)}>
                    {showDisable2fa ? "Cancel" : "Manage"} {showDisable2fa ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                  </button>
                </div>
                {showDisable2fa && (
                  <div id="pb-disable-mfa" className="pb-security-nested-form pb-security-danger-zone">
                    <p><AlertTriangle className="h-4 w-4" aria-hidden="true" /> This signs you out and invalidates existing mail-client app passwords.</p>
                    <div className="pb-security-fields">
                      <label className="pb-premium-field" htmlFor="pb-disable-password">Current mailbox password
                        <input id="pb-disable-password" type="password" autoComplete="current-password" value={disablePassword} onChange={(e) => setDisablePassword(e.target.value)} />
                      </label>
                      <label className="pb-premium-field" htmlFor="pb-disable-code">Authenticator code
                        <input id="pb-disable-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={disableCode} onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
                      </label>
                    </div>
                    <button type="button" className="pb-settings-secondary" disabled={busy || !disablePassword || disableCode.length !== 6} onClick={() => void disableMailbox2fa()}>Disable voluntary mailbox 2FA</button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </section>

      <section className="pb-setting-card pb-security-section">
        <header className="pb-security-card-header">
          <div className="pb-security-section-head">
            <span className="pb-security-section-icon"><Laptop className="h-5 w-5" aria-hidden="true" /></span>
            <div>
              <span className="pb-security-overline">02 · External mail applications</span>
              <h2>SMTP / IMAP app passwords</h2>
              <p>Connect Outlook, Gmail, Thunderbird or another mail client without sharing your PostBox sign-in credentials.</p>
            </div>
          </div>
          <span className={state.enabled ? "pb-settings-pill" : "pb-settings-pill neutral"}>{state.enabled ? "Access on" : "Access off"}</span>
        </header>
        <div className="pb-security-card-body">
          {!state.two_factor_enabled && (
            <div className="pb-security-explanation">
              <strong>Set up 2FA first for the best protection</strong>
              <p>App passwords become required after 2FA is enabled. Your existing mail client connection is not changed until you complete setup.</p>
            </div>
          )}
          {state.credentials.length ? (
            <div className="pb-security-client-list">
              <h3>Saved mail clients</h3>
              <div className="pb-session-list">
                {state.credentials.map((item) => (
                  <article key={item.id}>
                    <div className="pb-session-icon"><KeyRound className="h-5 w-5" aria-hidden="true" /></div>
                    <div className="min-w-0 flex-1">
                      <strong>{item.label}</strong>
                      <small>Created {new Date(item.created_at).toLocaleDateString()} · {item.revoked_at ? "Revoked" : "Active"}</small>
                    </div>
                    {!item.revoked_at && (
                      <button type="button" className="pb-settings-secondary" disabled={busy} aria-expanded={revokeTarget === item.id} onClick={() => {
                        setPassword("");
                        setCode("");
                        setShowClientForm(false);
                        setShowClientAccess(false);
                        setRevokeTarget((current) => current === item.id ? null : item.id);
                      }}>{revokeTarget === item.id ? "Cancel" : "Revoke"}</button>
                    )}
                    {revokeTarget === item.id && !item.revoked_at && (
                      <div className="pb-security-revoke-confirm">
                        <p>Revoke this app password? Sign-in from this mail client will stop immediately.</p>
                        <div className="pb-security-fields">
                          <label className="pb-premium-field" htmlFor={"pb-revoke-password-" + item.id}>Current mailbox password
                            <input id={"pb-revoke-password-" + item.id} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
                          </label>
                          {state.two_factor_enabled && <label className="pb-premium-field" htmlFor={"pb-revoke-code-" + item.id}>Authenticator code
                            <input id={"pb-revoke-code-" + item.id} inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
                          </label>}
                        </div>
                        <button type="button" className="pb-settings-secondary" disabled={busy || !password || (state.two_factor_enabled && code.length !== 6)} onClick={() => {
                          if (window.confirm("Permanently revoke the SMTP/IMAP app password for " + item.label + "?")) void modify("revoke", { credential_id: item.id });
                        }}>Confirm revoke</button>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </div>
          ) : (
            <p className="pb-security-empty-note">No dedicated app passwords yet. You can create one for each mail client.</p>
          )}
          <div className="pb-security-inline-option">
            <div><strong>Add a mail client</strong><p>Generate a unique, revocable password for a single device or app.</p></div>
            <button type="button" className="pb-settings-secondary" aria-expanded={showClientForm} aria-controls="pb-client-creation" onClick={() => setShowClientForm((value) => !value)}>
              {showClientForm ? "Close" : "Create app password"} {showClientForm ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          </div>
          {showClientForm && (
            <div id="pb-client-creation" className="pb-security-nested-form">
              <div className="pb-security-fields">
                <label className="pb-premium-field" htmlFor="pb-client-name">App or device name
                  <input id="pb-client-name" type="text" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Outlook on laptop" />
                </label>
                <label className="pb-premium-field" htmlFor="pb-client-password">Current mailbox password
                  <input id="pb-client-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
                </label>
                {state.two_factor_enabled && <label className="pb-premium-field" htmlFor="pb-client-code">Current authenticator code
                  <input id="pb-client-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
                </label>}
              </div>
              <div className="pb-security-actions">
                <button type="button" className="pb-settings-primary" disabled={busy || !password || !label.trim() || (state.two_factor_enabled && code.length !== 6)} onClick={() => void modify("create", { label: label.trim() })}>
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <KeyRound className="h-4 w-4" aria-hidden="true" />} Generate app password
                </button>
              </div>
            </div>
          )}
          <div className="pb-security-inline-option pb-security-access-option">
            <div><strong>External mail-client access</strong><p>{state.enabled ? "Mail applications can sign in using currently permitted credentials." : "IMAP and SMTP sign-ins are disabled for this mailbox."}</p></div>
            <button type="button" className="pb-settings-secondary" aria-expanded={showClientAccess} aria-controls="pb-client-access" onClick={() => setShowClientAccess((value) => !value)}>
              Manage access {showClientAccess ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          </div>
          {showClientAccess && (
            <div id="pb-client-access" className="pb-security-nested-form">
              <p>{state.enabled ? "Disabling access disconnects external mail apps. Incoming email and the PostBox website remain available." : "Re-enabling access permits eligible IMAP and SMTP client sign-ins."}</p>
              <div className="pb-security-fields">
                <label className="pb-premium-field" htmlFor="pb-access-password">Current mailbox password
                  <input id="pb-access-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
                </label>
                {state.two_factor_enabled && <label className="pb-premium-field" htmlFor="pb-access-code">Current authenticator code
                  <input id="pb-access-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
                </label>}
              </div>
              <button type="button" className="pb-settings-secondary" disabled={busy || !password || (state.two_factor_enabled && code.length !== 6)} onClick={() => {
                if (window.confirm(state.enabled ? "Disable IMAP/SMTP sign-ins for this mailbox? Existing sessions will be disconnected." : "Enable external IMAP/SMTP sign-ins?")) void modify("access", { enabled: !state.enabled });
              }}>{state.enabled ? "Disable external mail access" : "Enable external mail access"}</button>
            </div>
          )}
        </div>
      </section>
      {notice && <p className="pb-settings-success" role="status">{notice}</p>}
      {loadFailure && <p className="pb-settings-error" role="status">{loadFailure}</p>}
      {error && <p className="pb-settings-error" role="alert">{error}</p>}
    </div>
  );
}
