"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft, Mail } from "lucide-react";
import {
  AuthButton,
  AuthError,
  AuthField,
  AuthNotice,
  PremiumAuthShell,
} from "@/components/workspace/premium-auth";
import { api, ApiError } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.post("/api/auth/forgot-password/", { email });
      setSent(true);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status !== 200) {
        setError("Something went wrong. Please try again.");
      } else {
        setSent(true);
      }
    } finally {
      setLoading(false);
    }
  }

  if (sent) {
    return (
      <PremiumAuthShell
        title="A fresh start is on its way."
        description="If that address belongs to a MateMail account, a secure reset link has been sent."
        icon={<Mail className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthNotice>
            Check <strong>{email}</strong> and your spam folder. Reset links expire in 1 hour.
          </AuthNotice>
          <Link href="/login" className="auth-button">
            Back to sign in
          </Link>
        </div>
      </PremiumAuthShell>
    );
  }

  return (
    <PremiumAuthShell
      title="Forgot your password?"
      description="Enter your email and we’ll send a secure password-reset link if the account exists."
      icon={<Mail className="h-6 w-6" />}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        <AuthField label="Email address">
          <input
            className="auth-input"
            type="email"
            autoComplete="email"
            autoFocus
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@yourcompany.com"
          />
        </AuthField>

        {error && <AuthError>{error}</AuthError>}

        <AuthButton type="submit" loading={loading}>
          {loading ? "Sending…" : "Send reset link"}
        </AuthButton>

        <Link href="/login" className="auth-back">
          <ArrowLeft className="h-4 w-4" />
          Back to sign in
        </Link>
      </form>
    </PremiumAuthShell>
  );
}
