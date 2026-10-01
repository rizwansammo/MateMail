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
import { ApiError } from "@/lib/api";

function TwoFactorContent() {
  const router = useRouter();
  const params = useSearchParams();
  const partialToken = params.get("token") ?? "";
  const requestedNext = params.get("next") || "";
  const next =
    requestedNext === "/app" || requestedNext.startsWith("/app/")
      ? requestedNext
      : "/app";
  const { verify2fa } = useAuth();

  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [useBackup, setUseBackup] = useState(false);

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
