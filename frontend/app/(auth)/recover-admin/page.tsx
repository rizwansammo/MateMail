"use client";

export const dynamic = "force-dynamic";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import { AuthButton, AuthError, AuthSuccess, PremiumAuthShell } from "@/components/workspace/premium-auth";
import { api, ApiError } from "@/lib/api";

export default function RecoverAdminPage() {
  const [token, setToken] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [finished, setFinished] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const raw = fragment.get("token") ?? "";
    setToken(raw);
    // Remove the bearer capability from the visible URL after loading.
    if (window.location.hash) {
      window.history.replaceState(window.history.state, "", window.location.pathname);
    }
    setLoaded(true);
  }, []);

  async function approve() {
    if (!token || busy || finished) return;
    setBusy(true);
    setError("");
    try {
      await api.post("/api/auth/2fa/admin-recovery/confirm/", { token, confirm: true });
      setToken("");
      setFinished(true);
    } catch (caught) {
      let detail = "The approval could not be completed. The link may be expired or already used.";
      if (caught instanceof ApiError) {
        try {
          const body = JSON.parse(caught.message);
          if (typeof body.detail === "string") detail = body.detail;
        } catch { /* Keep the safe default. */ }
      }
      setError(detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <PremiumAuthShell
      title={finished ? "Authenticator recovery approved" : "Confirm administrator recovery"}
      description={finished
        ? "Sign in with your password and set up a new authenticator to regain Hub access."
        : "Your workspace owner requested a security reset for your administrator account."}
      icon={<ShieldCheck className="h-6 w-6" />}
    >
      <div className="auth-form">
        {!loaded ? (
          <p>Loading recovery request…</p>
        ) : finished ? (
          <>
            <AuthSuccess>Previous authenticator codes, backup codes and sessions have been revoked.</AuthSuccess>
            <p>After entering your current account password, you must configure a new authenticator before entering the Hub. If you also forgot your password, reset it separately first.</p>
            <Link href="/login" className="auth-button">Sign in and set up 2FA</Link>
            <Link href="/forgot-password" className="auth-text-button">Forgot password?</Link>
          </>
        ) : !token ? (
          <>
            <AuthError>The recovery token is missing. Open the link sent to your verified independent recovery email.</AuthError>
            <Link href="/login" className="auth-button">Return to sign in</Link>
          </>
        ) : (
          <>
            <p>Confirm this request only if you personally asked your workspace owner to help recover access.</p>
            <p>Confirming immediately invalidates your existing authenticator, recovery codes and active sessions. You will need your account password and a newly configured authenticator to sign in again.</p>
            {error && <AuthError>{error}</AuthError>}
            <AuthButton type="button" onClick={() => void approve()} loading={busy}>
              {busy ? "Confirming…" : "Confirm and reset authenticator"}
            </AuthButton>
            <Link href="/login" className="auth-text-button">Do not approve</Link>
          </>
        )}
      </div>
    </PremiumAuthShell>
  );
}
