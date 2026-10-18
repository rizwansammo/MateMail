"use client";

export const dynamic = "force-dynamic";

import { Suspense, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AuthButton,
  AuthError,
  AuthField,
  PremiumAuthShell,
} from "@/components/workspace/premium-auth";
import { useAuth } from "@/contexts/auth-context";
import { ApiError, api } from "@/lib/api";
import { PortalCopyButton } from "@/components/workspace/premium-ui";
import type { AuthUser, AuthTenant } from "@/contexts/auth-context";

function TwoFactorContent() {
  const router = useRouter();
  const params = useSearchParams();
  const partialToken = params.get("token") ?? "";
  const requestedNext = params.get("next") || "";
  const next =
    requestedNext === "/app" || requestedNext.startsWith("/app/")
      ? requestedNext
      : "/app";
  const { verify2fa, setAuthResult } = useAuth();
  const requiresEnrollment = params.get("enroll") === "1";

  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [useBackup, setUseBackup] = useState(false);
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [recoveryChallenge, setRecoveryChallenge] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [recoveryRequested, setRecoveryRequested] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      await verify2fa(partialToken, code);
      router.push(next);
    } catch (caught) {
      if (caught instanceof ApiError) {
        try {
          const body = JSON.parse(caught.message);
          setError(body.detail ?? "Invalid authentication code.");
        } catch {
          setError("Invalid authentication code. Please try again.");
        }
      } else {
        setError("Unable to verify the code. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  async function beginEnrollment() {
    setError("");
    setLoading(true);
    try {
      const result = await api.post<{ secret: string; uri: string }>(
        "/api/auth/2fa/enroll-required/", { partial_token: partialToken, action: "begin" },
      );
      setSetup(result);
    } catch { setError("Authenticator setup could not be started. Please sign in again."); }
    finally { setLoading(false); }
  }

  async function finishEnrollment(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await api.post<{ access: string; user: AuthUser; tenant: AuthTenant; backup_codes: string[] }>(
        "/api/auth/2fa/enroll-required/",
        { partial_token: partialToken, action: "confirm", code },
      );
      setAuthResult(result);
      setBackupCodes(result.backup_codes);
      setSetup(null);
      setCode("");
    } catch { setError("Verification failed. Check your authenticator code."); }
    finally { setLoading(false); }
  }

  async function requestRecoveryCode() {
    setError("");
    setLoading(true);
    try {
      const result = await api.post<{ recovery_challenge: string }>(
        "/api/auth/2fa/recovery/start/", { partial_token: partialToken },
      );
      setRecoveryChallenge(result.recovery_challenge);
      setRecoveryRequested(true);
    } catch { setError("Recovery is unavailable. You need an independently verified recovery email."); }
    finally { setLoading(false); }
  }

  async function finishRecovery(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await api.post<{ access: string; user: AuthUser; tenant: AuthTenant }>(
        "/api/auth/2fa/recovery/finish/",
        { partial_token: partialToken, recovery_challenge: recoveryChallenge, code: recoveryCode },
      );
      setAuthResult(result);
      router.push(next);
    } catch { setError("Recovery verification failed or expired."); }
    finally { setLoading(false); }
  }

  if (!partialToken) {
    return (
      <PremiumAuthShell
        title="Session expired."
        description="The two-factor challenge is missing or no longer available."
        icon={<ShieldCheck className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthError>Please sign in again to start a new secure challenge.</AuthError>
          <Link href="/login" className="auth-button">Back to sign in</Link>
        </div>
      </PremiumAuthShell>
    );
  }

  if (backupCodes.length > 0) {
    return (
      <PremiumAuthShell title="Save your recovery codes" description="Each code can be used only once. They cannot be shown again after leaving this page." icon={<ShieldCheck className="h-6 w-6" />}>
        <div className="auth-form">
          <div className="grid grid-cols-2 gap-3">{backupCodes.map((item) => <code key={item}>{item}</code>)}</div>
          <PortalCopyButton value={backupCodes.join("\n")} label="Copy recovery codes" />
          <button type="button" className="auth-button" onClick={() => router.push(next)}>I&apos;ve saved my codes — continue</button>
        </div>
      </PremiumAuthShell>
    );
  }

  if (requiresEnrollment) {
    return (
      <PremiumAuthShell title="Set up two-factor authentication" description="Your organization requires an authenticator before you can access the Hub." icon={<ShieldCheck className="h-6 w-6" />}>
        <div className="auth-form">
          {!setup ? (
            <AuthButton type="button" loading={loading} onClick={() => void beginEnrollment()}>Begin authenticator enrollment</AuthButton>
          ) : (
            <form onSubmit={finishEnrollment} className="auth-form">
              <AuthField label="Authenticator setup secret">
                <code className="block break-all">{setup.secret}</code>
                <PortalCopyButton value={setup.secret} label="Copy setup secret" />
              </AuthField>
              <AuthField label="Six-digit authenticator code">
                <input className="auth-code-input" autoComplete="one-time-code" inputMode="numeric" maxLength={6} pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} required />
              </AuthField>
              <AuthButton type="submit" loading={loading}>Verify and enable</AuthButton>
            </form>
          )}
          {error && <AuthError>{error}</AuthError>}
          <Link href="/login" className="auth-back">Back to sign in</Link>
        </div>
      </PremiumAuthShell>
    );
  }

  if (recoveryRequested) {
    return (
      <PremiumAuthShell title="Verify your recovery email" description="Enter the six-digit code sent to your independently verified recovery address." icon={<ShieldCheck className="h-6 w-6" />}>
        <form onSubmit={finishRecovery} className="auth-form">
          <AuthField label="Recovery email code">
            <input className="auth-code-input" autoComplete="one-time-code" inputMode="numeric" maxLength={6} pattern="[0-9]{6}" value={recoveryCode} onChange={(event) => setRecoveryCode(event.target.value.replace(/\D/g, "").slice(0, 6))} required />
          </AuthField>
          {error && <AuthError>{error}</AuthError>}
          <AuthButton type="submit" loading={loading}>Verify recovery code</AuthButton>
          <button type="button" className="auth-text-button" onClick={() => { setRecoveryRequested(false); setRecoveryChallenge(""); setRecoveryCode(""); setError(""); }}>Use authenticator instead</button>
        </form>
      </PremiumAuthShell>
    );
  }

  return (
    <PremiumAuthShell
      title={useBackup ? "Use a backup code." : "One more step."}
      description={
        useBackup
          ? "Enter one of the recovery codes you saved when two-factor authentication was enabled."
          : "Enter the 6-digit code from your authenticator app."
      }
      icon={<ShieldCheck className="h-6 w-6" />}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        <AuthField label={useBackup ? "Backup code" : "Authentication code"}>
          <input
            className={useBackup ? "auth-input" : "auth-code-input"}
            type="text"
            required
            autoFocus
            autoComplete="one-time-code"
            inputMode={useBackup ? "text" : "numeric"}
            maxLength={useBackup ? 8 : 6}
            value={code}
            onChange={(event) => {
              const nextValue = useBackup
                ? event.target.value.toUpperCase()
                : event.target.value.replace(/\D/g, "");
              setCode(nextValue);
              setError("");
            }}
            placeholder={useBackup ? "XXXXXXXX" : "123456"}
          />
        </AuthField>

        {error && <AuthError>{error}</AuthError>}

        <AuthButton type="submit" loading={loading}>
          {loading ? "Verifying…" : "Verify & sign in"}
        </AuthButton>

        <button
          type="button"
          className="auth-text-button"
          onClick={() => {
            setUseBackup((value) => !value);
            setCode("");
            setError("");
          }}
        >
          {useBackup ? "Use authenticator app instead" : "Use a backup code"}
        </button>

        <button type="button" className="auth-text-button" disabled={loading} onClick={() => void requestRecoveryCode()}>
          Send a recovery code to my verified external email
        </button>

        <Link href="/login" className="auth-back">
          <ArrowLeft className="h-4 w-4" />
          Sign in to a different account
        </Link>
      </form>
    </PremiumAuthShell>
  );
}

export default function TwoFactorPage() {
  return (
    <Suspense>
      <TwoFactorContent />
    </Suspense>
  );
}
