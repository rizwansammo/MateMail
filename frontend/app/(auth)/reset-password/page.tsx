"use client";

export const dynamic = "force-dynamic";

import { Suspense, useState } from "react";
import Link from "next/link";
import { KeyRound } from "lucide-react";
import { useSearchParams } from "next/navigation";
import {
  AuthButton,
  AuthError,
  AuthField,
  AuthSuccess,
  PremiumAuthShell,
} from "@/components/workspace/premium-auth";
import { api, ApiError } from "@/lib/api";

function ResetPasswordForm() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");

    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    if (password.length < 10) {
      setError("Password must be at least 10 characters.");
      return;
    }

    setLoading(true);
    try {
      await api.post("/api/auth/reset-password/", {
        token,
        new_password: password,
      });
      setDone(true);
    } catch (caught) {
      if (caught instanceof ApiError) {
        try {
          const body = JSON.parse(caught.message);
          setError(body.detail ?? "Reset failed.");
        } catch {
          setError("Reset failed. The link may have expired.");
        }
      } else {
        setError("Reset failed. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <PremiumAuthShell
        title="Invalid reset link."
        description="This password-reset page is missing its secure token."
        icon={<KeyRound className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthError>Request a new password-reset link to continue.</AuthError>
          <Link href="/forgot-password" className="auth-button">Request reset link</Link>
        </div>
      </PremiumAuthShell>
    );
  }

  if (done) {
    return (
      <PremiumAuthShell
        title="Password updated."
        description="Your old sessions have been revoked. Sign in again using your new password."
        icon={<KeyRound className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthSuccess>Your MateMail password was changed successfully.</AuthSuccess>
          <Link href="/login" className="auth-button">Sign in</Link>
        </div>
      </PremiumAuthShell>
    );
  }

  return (
    <PremiumAuthShell
      title="Choose a new password."
      description="Make it strong, memorable, and unique to your MateMail account."
      icon={<KeyRound className="h-6 w-6" />}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        <AuthField label="New password" hint="Use at least 10 characters. Standard password-strength rules apply.">
          <input
            className="auth-input"
            type="password"
            autoComplete="new-password"
            minLength={10}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </AuthField>

        <AuthField label="Confirm new password">
          <input
            className="auth-input"
            type="password"
            autoComplete="new-password"
            minLength={10}
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </AuthField>

        {error && <AuthError>{error}</AuthError>}

        <AuthButton type="submit" loading={loading}>
          {loading ? "Saving…" : "Reset password"}
        </AuthButton>
      </form>
    </PremiumAuthShell>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetPasswordForm />
    </Suspense>
  );
}
