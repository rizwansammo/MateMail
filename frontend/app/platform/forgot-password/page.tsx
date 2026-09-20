"use client";

/**
 * Platform Admin password recovery.
 *
 * Three stages on one page: address, emailed code, new password. The server
 * answers the first stage identically for every address — a real platform
 * account and a stranger's both receive a challenge — so this page must not
 * imply otherwise. It says "if that address belongs to a platform
 * administrator", and it moves to the code step either way.
 *
 * Finishing does not sign anybody in. Proving control of a mailbox is not the
 * same as completing a login, and the console is still reached through the
 * normal two-stage sign-in afterwards.
 */
import { useCallback, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, CheckCircle2, Loader2 } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/platform/theme";
import { api } from "@/lib/api";
import { describeError } from "@/contexts/platform-auth-context";

type Stage = "email" | "code" | "done";

export default function PlatformForgotPasswordPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("email");
  const [email, setEmail] = useState("");
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revoked, setRevoked] = useState(0);

  const requestCode = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);
      setBusy(true);
      try {
        const data = await api.post<{ challenge: string }>(
          "/api/platform/auth/forgot-password/",
          { email: email.trim() },
        );
        setChallenge(data.challenge);
        setStage("code");
      } catch (caught) {
        setError(describeError(caught, "Could not start recovery. Please try again."));
      } finally {
        setBusy(false);
      }
    },
    [email],
  );

  const submitReset = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (newPassword !== confirmPassword) {
        setError("The two passwords do not match.");
        return;
      }
      setError(null);
      setBusy(true);
      try {
        const data = await api.post<{ sessions_revoked: number }>(
          "/api/platform/auth/reset-password/",
          { challenge, code: code.trim(), new_password: newPassword },
        );
        setRevoked(data.sessions_revoked ?? 0);
        setStage("done");
      } catch (caught) {
        setError(
          describeError(caught, "That code is not valid, or the password was rejected."),
        );
      } finally {
        setBusy(false);
      }
    },
    [challenge, code, newPassword, confirmPassword],
  );

  return (
    <div className="pf flex min-h-screen flex-col">
      <header className="flex items-center justify-between p-4">
        <div className="flex items-center gap-2.5">
          <BrandMark size={30} preload />
          <div>
            <p className="text-sm font-bold leading-tight" style={{ color: "var(--pf-text)" }}>
              MateMail
            </p>
            <p className="pf-label" style={{ letterSpacing: "0.08em" }}>
              Platform Console
            </p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      <main className="flex flex-1 items-center justify-center px-4 pb-16">
        <div className="w-full max-w-sm">
          <div className="pf-card p-6">
            {stage === "email" && (
              <>
                <h1 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>
                  Reset your password
                </h1>
                <p className="mb-5 mt-1 text-xs pf-muted">
                  Enter your platform administrator address. If it belongs to a
                  platform account, a six-digit code will arrive by email.
                </p>

                <form onSubmit={requestCode} className="space-y-3">
                  <div>
                    <label htmlFor="pf-reset-email" className="pf-label">
                      Email
                    </label>
                    <input
                      id="pf-reset-email"
                      className="pf-input mt-1"
                      type="email"
                      autoComplete="username"
                      required
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                    />
                  </div>

                  {error && (
                    <p className="text-xs" role="alert" style={{ color: "var(--pf-danger)" }}>
                      {error}
                    </p>
                  )}

                  <button type="submit" className="pf-btn pf-btn-primary w-full" disabled={busy}>
                    {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                    Send code
                  </button>
                </form>
              </>
            )}

            {stage === "code" && (
              <>
                <h1 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>
                  Choose a new password
                </h1>
                <p className="mb-5 mt-1 text-xs pf-muted">
                  Enter the code from your email and the password you want to use.
                </p>

                <form onSubmit={submitReset} className="space-y-3">
                  <div>
                    <label htmlFor="pf-reset-code" className="pf-label">
                      Security code
                    </label>
                    <input
                      id="pf-reset-code"
                      className="pf-input mt-1 pf-num"
                      style={{ letterSpacing: "0.3em", textAlign: "center" }}
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      pattern="\d{6}"
                      maxLength={6}
                      required
                      value={code}
                      onChange={(event) =>
                        setCode(event.target.value.replace(/\D/g, "").slice(0, 6))
                      }
                    />
                  </div>

                  <div>
                    <label htmlFor="pf-new-password" className="pf-label">
                      New password
                    </label>
                    <input
                      id="pf-new-password"
                      className="pf-input mt-1"
                      type="password"
                      autoComplete="new-password"
                      required
                      value={newPassword}
                      onChange={(event) => setNewPassword(event.target.value)}
                    />
                  </div>

                  <div>
                    <label htmlFor="pf-confirm-password" className="pf-label">
                      Confirm new password
                    </label>
                    <input
                      id="pf-confirm-password"
                      className="pf-input mt-1"
                      type="password"
                      autoComplete="new-password"
                      required
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                    />
                  </div>

                  {error && (
                    <p className="text-xs" role="alert" style={{ color: "var(--pf-danger)" }}>
                      {error}
                    </p>
                  )}

                  <button
                    type="submit"
                    className="pf-btn pf-btn-primary w-full"
                    disabled={busy || code.length !== 6}
                  >
                    {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                    Change password
                  </button>
                </form>
              </>
            )}

            {stage === "done" && (
              <div className="text-center">
                <CheckCircle2
                  className="mx-auto mb-3 h-7 w-7"
                  style={{ color: "var(--pf-ok)" }}
                  aria-hidden="true"
                />
                <h1 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>
                  Password changed
                </h1>
                <p className="mt-1 text-xs pf-muted">
                  {revoked > 0
                    ? `${revoked} existing session${revoked === 1 ? "" : "s"} were signed out.`
                    : "Any existing sessions were signed out."}{" "}
                  Sign in again with your new password — a fresh security code
                  will be emailed.
                </p>
                <button
                  type="button"
                  className="pf-btn pf-btn-primary mt-4 w-full"
                  onClick={() => router.replace("/platform/login")}
                >
                  Go to sign in
                </button>
              </div>
            )}
          </div>

          {stage !== "done" && (
            <div className="mt-4 text-center">
              <Link
                href="/platform/login"
                className="inline-flex items-center gap-1 text-xs font-medium"
                style={{ color: "var(--pf-text-muted)" }}
              >
                <ArrowLeft className="h-3 w-3" aria-hidden="true" />
                Back to sign in
              </Link>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
