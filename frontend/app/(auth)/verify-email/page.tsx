"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { CheckCircle2, XCircle } from "lucide-react";
import { api, ApiError } from "@/lib/api";

function VerifyEmailContent() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const [status, setStatus] = useState<"verifying" | "success" | "error">("verifying");
  const [message, setMessage] = useState("");
  const [resendLoading, setResendLoading] = useState(false);
  const [resendSent, setResendSent] = useState(false);

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setMessage("No verification token found in the link.");
      return;
    }
    api
      .post("/api/auth/verify-email/", { token })
      .then(() => setStatus("success"))
      .catch((err) => {
        setStatus("error");
        if (err instanceof ApiError) {
          try {
            const body = JSON.parse(err.message);
            setMessage(body.detail ?? "Verification failed.");
          } catch {
            setMessage("Verification failed.");
          }
        }
      });
  }, [token]);

  async function handleResend() {
    setResendLoading(true);
    try {
      await api.post("/api/auth/resend-verification/");
      setResendSent(true);
    } catch {
      // ignore
    } finally {
      setResendLoading(false);
    }
  }

  if (status === "verifying") {
    return (
      <div>
        <h2 className="text-3xl font-black tracking-tight text-slate-950">Verifying…</h2>
        <p className="mt-3 text-sm text-slate-500">Please wait while we verify your email address.</p>
      </div>
    );
  }

  if (status === "success") {
    return (
      <div>
        <CheckCircle2 className="mb-4 h-10 w-10 text-emerald-500" />
        <h2 className="text-3xl font-black tracking-tight text-slate-950">Email verified</h2>
        <p className="mt-3 text-sm text-slate-600">
          Your email address has been confirmed. Your account is fully active.
        </p>
        <div className="mt-6">
          <Link
            href="/app"
            className="inline-flex items-center justify-center bg-cyan-500 px-6 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400"
          >
            Go to dashboard
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      <XCircle className="mb-4 h-10 w-10 text-rose-500" />
      <h2 className="text-3xl font-black tracking-tight text-slate-950">Verification failed</h2>
      <p className="mt-3 text-sm text-slate-600">{message || "This link is invalid or has expired."}</p>

      {resendSent ? (
        <p className="mt-4 text-sm text-emerald-600">A new verification email has been sent.</p>
      ) : (
        <button
          onClick={handleResend}
          disabled={resendLoading}
          className="mt-5 text-sm font-semibold text-cyan-600 hover:underline disabled:opacity-50"
        >
          {resendLoading ? "Sending…" : "Resend verification email"}
        </button>
      )}

      <div className="mt-4">
        <Link href="/login" className="text-sm font-semibold text-slate-600 hover:underline">
          Back to sign in
        </Link>
      </div>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <VerifyEmailContent />
    </Suspense>
  );
}
