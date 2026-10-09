"use client";

export const dynamic = "force-dynamic";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Eye, EyeOff, LockKeyhole } from "lucide-react";
import {
  AuthButton,
  AuthError,
  AuthField,
  PremiumAuthShell,
} from "@/components/workspace/premium-auth";
import { useAuth } from "@/contexts/auth-context";
import { ApiError, rateLimitMessage } from "@/lib/api";

function readApiDetail(caught: unknown, fallback: string) {
  if (!(caught instanceof ApiError)) return fallback;
  if (caught.status === 429) return rateLimitMessage(caught);
  try {
    const body = JSON.parse(caught.message);
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

function LoginContent() {
  const router = useRouter();
  const params = useSearchParams();
  const requestedNext = params.get("next") || "";
  const explicitNext = requestedNext.startsWith("/app/") ? requestedNext : "";
  const destination = explicitNext || "/app";
  const { login } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await login(email, password);
      if (result.requires_2fa && result.partial_token) {
        router.push(
          `/2fa?token=${encodeURIComponent(result.partial_token)}&next=${encodeURIComponent(destination)}`,
        );
        return;
      }
      router.push(destination);
    } catch (caught) {
      setError(readApiDetail(caught, "Sign in failed. Check your credentials and try again."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <PremiumAuthShell
      title="Welcome back."
      description="Sign in to manage your organization’s email."
      icon={<LockKeyhole className="h-6 w-6" />}
    >
      <form onSubmit={handleSubmit} className="auth-form" noValidate>
        <AuthField label="Email address">
          <input
            className="auth-input"
            type="email"
            autoComplete="username"
            autoFocus
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@yourcompany.com"
          />
        </AuthField>

        <AuthField label="Password">
          <div className="auth-password-wrap">
            <input
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button
              type="button"
              className="auth-password-toggle"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </AuthField>

        <Link href="/forgot-password" className="auth-text-button auth-forgot-link">
          Forgot password?
        </Link>

        {error && <AuthError>{error}</AuthError>}

        <AuthButton type="submit" loading={loading}>
          {loading ? "Signing in…" : "Sign in"}
        </AuthButton>

        <p className="auth-switch">
          New to MateMail?{" "}
          <Link href="/signup" className="auth-text-button">
            Create an account
          </Link>
        </p>
      </form>
    </PremiumAuthShell>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginContent />
    </Suspense>
  );
}
