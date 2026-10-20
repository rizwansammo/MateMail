"use client";

export const dynamic = "force-dynamic";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import {
  AuthButton, AuthError, AuthSuccess, PremiumAuthShell,
} from "@/components/workspace/premium-auth";
import { api, ApiError } from "@/lib/api";

export default function OwnerEmergencyConfirmation() {
  const [capability, setCapability] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [working, setWorking] = useState(false);
  const [done, setDone] = useState(false);
  const [full, setFull] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => {
      if (!active) return;
      const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      setCapability(params.get("capability") ?? "");
      if (window.location.hash) {
        window.history.replaceState(window.history.state, "", window.location.pathname);
      }
      setLoaded(true);
    });
    return () => { active = false; };
  }, []);

  async function confirm() {
    if (working || done || !capability) return;
    setWorking(true); setError("");
    try {
      const result = await api.post<{ detail: string; password_reset_required: boolean }>(
        "/api/auth/owner-emergency/confirm/", { capability, confirm: true },
      );
      setFull(result.password_reset_required);
      setCapability("");
      setDone(true);
    } catch (caught) {
      let message = "The emergency approval failed or expired. Contact MateMail security.";
      if (caught instanceof ApiError) {
        try {
          const payload = JSON.parse(caught.message);
          if (typeof payload.detail === "string") message = payload.detail;
        } catch { /* Keep safe generic error. */ }
      }
      setError(message);
    } finally {
      setWorking(false);
    }
  }

  return (
    <PremiumAuthShell title={done ? "Owner recovery confirmed" : "Confirm owner emergency recovery"}
      description={done
        ? "Previous account sessions and authenticator credentials are no longer valid."
        : "Your organization's emergency recovery was reviewed by two independent Platform operators."}
      icon={<ShieldCheck className="h-6 w-6" />}>
      <div className="auth-form">
        {!loaded ? <p>Loading your request…</p> : done ? (
          <>
            <AuthSuccess>Your account security recovery was confirmed successfully.</AuthSuccess>
            {full ? <p>Check your independently verified recovery email for a one-use password-reset link. Choose a new password, then sign in and set up a new authenticator.</p>
              : <p>Sign in with your existing password, then set up a new authenticator. Your previous backup codes cannot be reused.</p>}
            <Link href={full ? "/forgot-password" : "/login"} className="auth-button">
              {full ? "Password recovery assistance" : "Sign in to enroll 2FA"}
            </Link>
          </>
        ) : !capability ? (
          <AuthError>This link is missing its one-use security capability. Use the full link from your verified external recovery mailbox.</AuthError>
        ) : (
          <>
            <p>Confirm ONLY if you requested emergency recovery and recognized the independent identity verification process.</p>
            <p>Confirming will permanently invalidate your current Google Authenticator, backup codes and active Hub sessions. In Full Recovery, your old password becomes unusable and a new password-reset link is sent only to your verified independent recovery email.</p>
            <p className="text-sm font-semibold">If this is unexpected, do not confirm. Contact MateMail security.</p>
            {error && <AuthError>{error}</AuthError>}
            <AuthButton type="button" loading={working} onClick={() => void confirm()}>
              {working ? "Confirming…" : "I approve this emergency recovery"}
            </AuthButton>
            <Link href="/login" className="auth-text-button">Do not approve</Link>
          </>
        )}
      </div>
    </PremiumAuthShell>
  );
}
