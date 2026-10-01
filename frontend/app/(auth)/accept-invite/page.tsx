"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import { CheckCircle2, AlertTriangle, Loader2, Mail } from "lucide-react";

interface InvitePreview {
  valid: boolean;
  email?: string;
  role?: string;
  tenant_name?: string;
  invited_by?: string;
  expires_at?: string;
  detail?: string;
}

function AcceptInviteContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const { isAuthenticated, isLoading, user, setAuthResult } = useAuth();

  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);

  const [accepting, setAccepting] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      setPreviewLoading(false);
      return;
    }
    apiRequest(`/api/teams/invites/preview/?token=${encodeURIComponent(token)}`)
      .then((r) => r.json())
      .then((d) => setPreview(d))
      .catch(() => setPreview({ valid: false, detail: "Could not load invite details." }))
      .finally(() => setPreviewLoading(false));
  }, [token]);

  async function handleAccept() {
    setError("");
    setAccepting(true);
    try {
      const res = await apiRequest("/api/teams/invites/accept/", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        if (body.access && body.user && body.tenant) {
          setAuthResult(body);
        }
        setAccepted(true);
      } else {
        setError(body.detail ?? "Failed to accept invite.");
      }
    } finally {
      setAccepting(false);
    }
  }

  if (!token) {
    return (
      <InviteShell>
        <div className="flex flex-col items-center gap-3 text-center">
          <AlertTriangle className="h-8 w-8 text-amber-400" />
          <p className="text-sm text-slate-600">No invite token found in this link.</p>
          <Link href="/login" className="text-sm font-medium text-cyan-600 hover:underline">
            Go to login
          </Link>
        </div>
      </InviteShell>
    );
  }

  if (previewLoading || isLoading) {
    return (
      <InviteShell>
        <div className="flex justify-center py-6">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      </InviteShell>
    );
  }

  if (!preview?.valid) {
    return (
      <InviteShell>
        <div className="flex flex-col items-center gap-3 text-center">
          <AlertTriangle className="h-8 w-8 text-red-400" />
          <p className="text-sm font-medium text-slate-800">This invite is no longer valid</p>
          <p className="text-xs text-slate-500">
            {preview?.detail ?? "The invite may have expired or already been used."}
          </p>
          <Link href="/login" className="text-sm font-medium text-cyan-600 hover:underline">
            Go to login
          </Link>
        </div>
      </InviteShell>
    );
  }

  if (accepted) {
    return (
      <InviteShell>
        <div className="flex flex-col items-center gap-4 text-center">
          <CheckCircle2 className="h-10 w-10 text-emerald-500" />
          <div>
            <p className="text-base font-semibold text-slate-900">You&apos;re in!</p>
            <p className="mt-1 text-sm text-slate-500">
              You&apos;ve joined <strong>{preview.tenant_name}</strong>.
            </p>
          </div>
          <button
            onClick={() => router.push("/app")}
            className="rounded-md bg-slate-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-slate-700"
          >
            Open workspace
          </button>
        </div>
      </InviteShell>
    );
  }

  return (
    <InviteShell>
      <div className="space-y-5">
        <div className="text-center space-y-1">
          <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-cyan-50">
            <Mail className="h-5 w-5 text-cyan-600" />
          </div>
          <p className="text-lg font-semibold text-slate-900">
            Join {preview.tenant_name}
          </p>
          {preview.invited_by && (
            <p className="text-sm text-slate-500">
              <strong>{preview.invited_by}</strong> invited you
            </p>
          )}
          <p className="text-sm text-slate-500">
            Role: <span className="font-medium capitalize">{preview.role?.replace("_", " ")}</span>
          </p>
          <p className="text-xs text-slate-400">Sent to {preview.email}</p>
        </div>

        {!isAuthenticated && (
          <div className="space-y-3">
            <p className="text-center text-sm text-slate-600">
              You need to be logged in to accept this invite.
            </p>
            <div className="flex flex-col gap-2">
              <Link
                href={`/login?next=${encodeURIComponent(`/accept-invite?token=${token}`)}`}
                className="flex w-full items-center justify-center rounded-md bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-700"
              >
                Log in to accept
              </Link>
              <Link
                href={`/signup?invite=${encodeURIComponent(token)}`}
                className="flex w-full items-center justify-center rounded-md border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Create a MateMail account
              </Link>
            </div>
          </div>
        )}

        {isAuthenticated && user && preview.email && user.email.toLowerCase() !== preview.email.toLowerCase() && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 space-y-2">
            <p>
              You&apos;re logged in as <strong>{user.email}</strong>, but this invite was sent to{" "}
              <strong>{preview.email}</strong>.
            </p>
            <p>
              Please{" "}
              <Link href="/login" className="font-medium underline">
                log in with the invited email
              </Link>{" "}
              to accept.
            </p>
          </div>
        )}

        {isAuthenticated && user && preview.email && user.email.toLowerCase() === preview.email.toLowerCase() && (
          <div className="space-y-3">
            {error && (
              <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
            )}
            <button
              onClick={handleAccept}
              disabled={accepting}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-cyan-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-cyan-700 disabled:opacity-50"
            >
              {accepting && <Loader2 className="h-4 w-4 animate-spin" />}
              {accepting ? "Accepting…" : `Accept and join ${preview.tenant_name}`}
            </button>
            <p className="text-center text-xs text-slate-400">Accepting as {user.email}</p>
          </div>
        )}
      </div>
    </InviteShell>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={
      <InviteShell>
        <div className="flex justify-center py-6">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      </InviteShell>
    }>
      <AcceptInviteContent />
    </Suspense>
  );
}

function InviteShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <BrandMark size={32} />
          <span className="auth-brand-word">MateMail Hub</span>
        </div>
        {children}
      </div>
    </div>
  );
}
