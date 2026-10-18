"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, KeyRound, Loader2, ShieldCheck, ShieldOff } from "lucide-react";
import { describePostBoxError } from "@/contexts/postbox-context";
import { postbox, type MailSecurityState, type MailSecurityOneTimeSecret } from "@/lib/postbox-api";

type Action = "create" | "revoke" | "access";

export function MailClientSecuritySettings() {
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

  const refresh = useCallback(async () => {
    try {
      const current = await postbox.clientSecurity();
      setState(current);
      setUnavailable(false);
    } catch (caught) {
      setUnavailable(true);
      setState(null);
      setError(describePostBoxError(caught, "SMTP/IMAP Security is not available yet."));
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
    if (state.dedicated_required && code.length !== 6) {
      setError("Enter the current six-digit authenticator code.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await postbox.manageClientSecurity({
        action, current_password: password,
        ...(state.dedicated_required ? { code } : {}), ...extra,
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
      await refresh();
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
      await refresh();
    } catch (caught) { setError(describePostBoxError(caught, "Authenticator verification failed.")); }
    finally { setBusy(false); }
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
  if (unavailable) return (
    <div className="pb-info-box">
      <ShieldOff className="h-5 w-5" />
      <div>
        <strong>Advanced mail-client security is not active yet</strong>
        <p>The controlled rollout has not enabled dedicated SMTP/IMAP Password Management. Existing mail access is unchanged.</p>
      </div>
    </div>
  );

  return (
    <div className="pb-mail-client-security">
      <section className="pb-setting-card">
        <header><h2>SMTP/IMAP Passwords</h2><p>Use a separate, revocable password for each external mail client. These passwords cannot sign in to PostBox.</p></header>
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
                {!item.revoked_at && <button type="button" className="pb-settings-secondary" disabled={busy || !password || (state.dedicated_required && code.length !== 6)} onClick={() => {
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
          {state?.dedicated_required && <label className="pb-premium-field">Current authenticator code
            <input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
          </label>}
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="pb-settings-primary" disabled={busy || !password || !label.trim() || (Boolean(state?.dedicated_required) && code.length !== 6)} onClick={() => void modify("create", { label: label.trim() })}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />} Create SMTP/IMAP Password
          </button>
          <button type="button" className="pb-settings-secondary" disabled={busy || !password || (Boolean(state?.dedicated_required) && code.length !== 6)} onClick={() => {
            if (window.confirm(state?.enabled ? "Disable SMTP/IMAP login for this mailbox? Existing sessions will be disconnected." : "Enable external SMTP/IMAP login?")) {
              void modify("access", { enabled: !state?.enabled });
            }
          }}>{state?.enabled ? "Disable external clients" : "Enable external clients"}</button>
        </div>
      </section>

      {state && !state.dedicated_required && (
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
              <div className="pb-info-box"><ShieldCheck className="h-5 w-5" /><div><strong>Add the authenticator secret</strong><p>Paste this secret into a trusted authenticator app, then enter its six-digit code. The secret is only for enrollment.</p><code className="break-all">{enrollment.secret}</code><button type="button" className="pb-settings-secondary" onClick={() => void navigator.clipboard.writeText(enrollment.secret)}>Copy secret</button></div></div>
              <label className="pb-premium-field">Authenticator code
                <input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={enrollmentCode} onChange={(e) => setEnrollmentCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
              </label>
              <button type="button" className="pb-settings-primary" disabled={busy || enrollmentCode.length !== 6} onClick={() => void finishEnrollment()}>Verify and enable</button>
              <button type="button" className="pb-settings-secondary" onClick={() => { setEnrollment(null); setEnrollmentCode(""); }}>Cancel</button>
            </>
          )}
        </section>
      )}

      {state?.dedicated_required && (
        <section className="pb-setting-card">
          <header>
            <h2>Two-factor authentication</h2>
            <p>Authenticator protection is currently required for this mailbox. Organization-required 2FA cannot be disabled by a mailbox user.</p>
          </header>
          <div className="pb-password-grid">
            <label className="pb-premium-field">Current mailbox password
              <input type="password" autoComplete="current-password" value={disablePassword} onChange={(e) => setDisablePassword(e.target.value)} />
            </label>
            <label className="pb-premium-field">Current authenticator code
              <input autoComplete="one-time-code" inputMode="numeric" maxLength={6} value={disableCode} onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
            </label>
          </div>
          <button type="button" className="pb-settings-secondary" disabled={busy || !disablePassword || disableCode.length !== 6} onClick={() => void disableMailbox2fa()}>
            Disable voluntary mailbox 2FA
          </button>
        </section>
      )}

      {secret && <section className="pb-setting-card">
        <header><h2>Save your new SMTP/IMAP Password</h2><p>This secret is shown once. Store it in your password manager before closing.</p></header>
        <code className="block break-all">{secret.one_time_credential}</code>
        <button type="button" className="pb-settings-secondary" onClick={() => void navigator.clipboard.writeText(secret.one_time_credential)}>Copy password</button>
        <button type="button" className="pb-settings-secondary" onClick={() => setSecret(null)}><Check className="h-4 w-4" /> I&apos;ve saved it</button>
      </section>}
      {backupCodes.length > 0 && <section className="pb-setting-card">
        <header><h2>Save backup codes</h2><p>Each code works only once. They will not be displayed again.</p></header>
        <div className="pb-password-grid">{backupCodes.map((backup) => <code key={backup}>{backup}</code>)}</div>
        <button type="button" className="pb-settings-secondary" onClick={() => void navigator.clipboard.writeText(backupCodes.join("\n"))}>Copy backup codes</button>
        <button type="button" className="pb-settings-secondary" onClick={() => setBackupCodes([])}><Check className="h-4 w-4" /> I&apos;ve stored my codes</button>
      </section>}
      {notice && <p className="pb-settings-success">{notice}</p>}
      {error && <p className="pb-settings-error" role="alert">{error}</p>}
    </div>
  );
}
