"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, KeyRound, Loader2, ShieldCheck, ShieldOff } from "lucide-react";
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
      if (action !== "create") await refresh(); // One-time password stays visible.

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
    <div className="pb-mail-client-security">
      <section className="pb-setting-card">
        <header><h2>SMTP/IMAP Passwords</h2><p>Use a separate, revocable password for each external mail client. These passwords cannot sign in to PostBox.</p></header>
        <div className="pb-setting-row">
          <div>
            <strong>Authenticated mailbox</strong>
            <p>These settings apply to {state.mailbox_address}, not necessarily the team mailbox you are viewing.</p>
          </div>
          <span className="pb-settings-pill neutral">{state.two_factor_enabled ? "2FA on" : "2FA off"}</span>
        </div>
        <div className="pb-setting-row">
          <div>
            <strong>External mail-client access</strong>
            <p>{state?.enabled ? "IMAP and SMTP authentication are permitted according to mailbox security policy." : "IMAP and SMTP login are blocked. Incoming email and PostBox web access remain available."}</p>
          </div>
          <span className={`pb-settings-pill ${state?.enabled ? "" : "neutral"}`}>{state?.enabled ? "Enabled" : "Disabled"}</span>
        </div>
        <div className="pb-setting-row">
          <div>
            <strong>Dedicated client passwords</strong>
            <p>{state?.dedicated_required ? "Required — your normal mailbox password cannot authenticate external clients." : "Optional — legacy mailbox passwords may work while 2FA is off."}</p>
          </div>
          <span className="pb-settings-pill neutral">{state?.dedicated_required ? "Required" : "Not required"}</span>
        </div>
        {state?.credentials.length ? (
          <div className="pb-session-list">
            {state.credentials.map((item) => (
              <article key={item.id}>
                <div className="pb-session-icon"><KeyRound className="h-5 w-5" /></div>
                <div className="min-w-0 flex-1">
                  <strong>{item.label}</strong>
                  <small>Created {new Date(item.created_at).toLocaleDateString()} · {item.revoked_at ? "Revoked" : "Active"}</small>
                </div>
                {!item.revoked_at && <button type="button" className="pb-settings-secondary" disabled={busy || !password || (state.two_factor_enabled && code.length !== 6)} onClick={() => {
                  if (window.confirm(`Revoke the SMTP/IMAP Password for ${item.label}? Connected mail clients may need a new password.`)) {
                    void modify("revoke", { credential_id: item.id });
                  }
                }}>Revoke</button>}
              </article>
            ))}
          </div>
        ) : <p className="pb-subtle">No dedicated SMTP/IMAP Passwords have been created.</p>}
        <div className="pb-password-grid">
          <label className="pb-premium-field">Device label
            <input type="text" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Laptop, phone, Outlook…" />
          </label>
          <label className="pb-premium-field">Current mailbox password
            <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {state.two_factor_enabled && <label className="pb-premium-field">Current authenticator code
            <input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
          </label>}
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="pb-settings-primary" disabled={busy || !password || !label.trim() || (state.two_factor_enabled && code.length !== 6)} onClick={() => void modify("create", { label: label.trim() })}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />} Create SMTP/IMAP Password
          </button>
          <button type="button" className="pb-settings-secondary" disabled={busy || !password || (state.two_factor_enabled && code.length !== 6)} onClick={() => {
            if (window.confirm(state?.enabled ? "Disable SMTP/IMAP login for this mailbox? Existing sessions will be disconnected." : "Enable external SMTP/IMAP login?")) {
              void modify("access", { enabled: !state?.enabled });
            }
          }}>{state?.enabled ? "Disable external clients" : "Enable external clients"}</button>
        </div>
      </section>

      {!state.two_factor_enabled && (
        <section className="pb-setting-card">
          <header><h2>Authenticator protection</h2><p>Require a six-digit authenticator code to sign in to PostBox. Your normal password will no longer work for external mail clients.</p></header>
          {!enrollment ? (
            <div className="pb-password-grid">
              <label className="pb-premium-field">Current mailbox password
                <input type="password" autoComplete="current-password" value={enrollmentPassword} onChange={(event) => setEnrollmentPassword(event.target.value)} />
              </label>
              <button type="button" className="pb-settings-primary" disabled={busy || !enrollmentPassword} onClick={() => void startEnrollment()}>
                <ShieldCheck className="h-4 w-4" /> Set up mailbox 2FA
              </button>
            </div>
          ) : (
            <>
              <div className="pb-info-box"><ShieldCheck className="h-5 w-5" /><div><strong>Scan to secure your mailbox</strong><p>Scan with an authenticator app, or switch to the manual setup key.</p></div></div>
              <AuthenticatorSetupCode secret={enrollment.secret} uri={enrollment.uri} compact />
              <label className="pb-premium-field">Authenticator code
                <input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={enrollmentCode} onChange={(e) => setEnrollmentCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
              </label>
              <button type="button" className="pb-settings-primary" disabled={busy || enrollmentCode.length !== 6} onClick={() => void finishEnrollment()}>Verify and enable</button>
              <button type="button" className="pb-settings-secondary" onClick={() => { setEnrollment(null); setEnrollmentCode(""); }}>Cancel</button>
            </>
          )}
        </section>
      )}

      {state.two_factor_enabled && (
        <section className="pb-setting-card">
          <header>
            <h2>Two-factor authentication</h2>
            <p>Authenticator protection is currently required for this mailbox. Organization-required 2FA cannot be disabled by a mailbox user.</p>
          </header>
          <div className="pb-info-box">
            <ShieldCheck className="h-5 w-5" />
            <div>
              <strong>Backup codes</strong>
              <p>Regenerate 10 one-time backup codes if yours are lost or compromised. The old ones will stop working immediately. Your SMTP/IMAP Passwords and Connected Apps remain unchanged.</p>
            </div>
          </div>
          <div className="pb-password-grid">
            <label className="pb-premium-field">Current mailbox password for code regeneration
              <input type="password" autoComplete="current-password" value={rotationPassword} onChange={(e) => setRotationPassword(e.target.value)} />
            </label>
            <label className="pb-premium-field">Current authenticator code
              <input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={rotationCode} onChange={(e) => setRotationCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
            </label>
          </div>
          <button type="button" className="pb-settings-secondary" disabled={busy || !rotationPassword || rotationCode.length !== 6} onClick={() => void regenerateBackupCodes()}>
            Regenerate backup codes
          </button>
          <div className="pb-password-grid">
            <label className="pb-premium-field">Current mailbox password
              <input type="password" autoComplete="current-password" value={disablePassword} onChange={(e) => setDisablePassword(e.target.value)} />
            </label>
            <label className="pb-premium-field">Current authenticator code
              <input autoComplete="one-time-code" inputMode="numeric" maxLength={6} value={disableCode} onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
            </label>
          </div>
          {state.organization_policy === "require" ? (
            <p className="pb-subtle">Your organization requires mailbox 2FA; it cannot be disabled here.</p>
          ) : (
            <button type="button" className="pb-settings-secondary" disabled={busy || !disablePassword || disableCode.length !== 6} onClick={() => void disableMailbox2fa()}>
              Disable voluntary mailbox 2FA
            </button>
          )}
        </section>
      )}

      {secret && <section className="pb-setting-card">
        <header><h2>Save your new SMTP/IMAP Password</h2><p>This secret is shown once. Store it in your password manager before closing.</p></header>
        <code className="block break-all">{secret.one_time_credential}</code>
        <button type="button" className="pb-settings-secondary" onClick={() => void navigator.clipboard.writeText(secret.one_time_credential)}>Copy password</button>
        <button type="button" className="pb-settings-secondary" onClick={() => { setSecret(null); void refresh(); }}><Check className="h-4 w-4" /> I&apos;ve saved it</button>
      </section>}
      {backupCodes.length > 0 && <section className="pb-setting-card">
        <header><h2>Save backup codes</h2><p>Each code works only once. They will not be displayed again.</p></header>
        <div className="pb-password-grid">{backupCodes.map((backup) => <code key={backup}>{backup}</code>)}</div>
        <button type="button" className="pb-settings-secondary" onClick={() => void navigator.clipboard.writeText(backupCodes.join("\n"))}>Copy backup codes</button>
        <button type="button" className="pb-settings-secondary" onClick={() => setBackupCodes([])}><Check className="h-4 w-4" /> I&apos;ve stored my codes</button>
      </section>}
      {notice && <p className="pb-settings-success">{notice}</p>}
      {loadFailure && <p className="pb-settings-error" role="status">{loadFailure}</p>}
      {error && <p className="pb-settings-error" role="alert">{error}</p>}
    </div>
  );
}
