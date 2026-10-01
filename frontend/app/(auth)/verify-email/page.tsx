"use client";

export const dynamic = "force-dynamic";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, Mail, XCircle } from "lucide-react";
import {
  AuthButton,
  AuthError,
  AuthNotice,
  AuthSuccess,
  PremiumAuthShell,
} from "@/components/workspace/premium-auth";
import { useAuth } from "@/contexts/auth-context";
import { api, ApiError } from "@/lib/api";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";

function VerifyEmailContent() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const sent = params.get("sent") === "1";
  const { isAuthenticated } = useAuth();
  const [status, setStatus] = useState<"verifying" | "success" | "error">("verifying");
  const [error, setError] = useState("");
  const [resendLoading, setResendLoading] = useState(false);
  const [resendMessage, setResendMessage] = useState("");

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    api
      .post("/api/auth/verify-email/", { token })
      .then(() => {
        if (!cancelled) setStatus("success");
      })
      .catch((caught) => {
        if (cancelled) return;
        setStatus("error");
        if (caught instanceof ApiError) {
          try {
            const body = JSON.parse(caught.message);
            setError(body.detail ?? "Verification failed.");
          } catch {
            setError("Verification failed.");
          }
        } else {
          setError("Verification failed.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleResend() {
    setResendMessage("");
    setResendLoading(true);
    try {
      const result = await api.post<{ detail?: string }>("/api/auth/resend-verification/");
      setResendMessage(result.detail ?? "Verification email sent.");
    } catch (caught) {
      if (caught instanceof ApiError) {
        try {
          const body = JSON.parse(caught.message);
          setResendMessage(body.detail ?? "Could not resend verification email.");
        } catch {
          setResendMessage("Could not resend verification email.");
        }
      } else {
        setResendMessage("Could not resend verification email.");
      }
    } finally {
      setResendLoading(false);
    }
  }

  if (sent && !token) {
    return (
      <PremiumAuthShell
        title="Check your inbox."
        description="We sent a verification link to your account email. Open that link to confirm your address."
        icon={<Mail className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthNotice>
            Your workspace has been created and is pending Private Beta approval. Email verification confirms the account address; platform approval controls mail provisioning.
          </AuthNotice>
          {resendMessage && <AuthSuccess>{resendMessage}</AuthSuccess>}
          <AuthButton type="button" loading={resendLoading} onClick={handleResend} secondary>
            {resendLoading ? "Sending…" : "Resend verification email"}
          </AuthButton>
          <Link href="/app/onboarding" className="auth-button">
            Continue to workspace
          </Link>
        </div>
      </PremiumAuthShell>
    );
  }

  if (!token) {
    return (
      <PremiumAuthShell
        title="Verification link missing."
        description="This page needs the secure token from your MateMail verification email."
        icon={<XCircle className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthError>Open the verification link from your inbox, or sign in and request a new one.</AuthError>
          <Link href="/login" className="auth-button">Back to sign in</Link>
        </div>
      </PremiumAuthShell>
    );
  }

  if (status === "verifying") {
    return (
      <PremiumAuthShell
        title="Verifying your email…"
        description="Please wait while MateMail validates this secure verification link."
        icon={<Mail className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthNotice>Verification is in progress.</AuthNotice>
        </div>
      </PremiumAuthShell>
    );
  }

  if (status === "success") {
    const next = isAuthenticated
      ? IS_NETAMATE_EMAIL
        ? "/app"
        : "/workspaces"
      : "/login";
    return (
      <PremiumAuthShell
        title="Email verified."
        description="Your email address has been confirmed successfully."
        icon={<CheckCircle2 className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthSuccess>Your MateMail account email is now verified.</AuthSuccess>
          <Link href={next} className="auth-button">
            {isAuthenticated ? "Continue to MateMail" : "Sign in to MateMail"}
          </Link>
        </div>
      </PremiumAuthShell>
    );
  }

  return (
    <PremiumAuthShell
      title="Verification failed."
      description="This verification link could not be accepted."
      icon={<XCircle className="h-6 w-6" />}
    >
      <div className="auth-form">
        <AuthError>{error || "This link is invalid or has expired."}</AuthError>
        {isAuthenticated && (
          <>
            {resendMessage && <AuthNotice>{resendMessage}</AuthNotice>}
            <AuthButton type="button" loading={resendLoading} onClick={handleResend} secondary>
              {resendLoading ? "Sending…" : "Resend verification email"}
            </AuthButton>
          </>
        )}
        <Link href="/login" className="auth-back">Back to sign in</Link>
      </div>
    </PremiumAuthShell>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <VerifyEmailContent />
    </Suspense>
  );
}
