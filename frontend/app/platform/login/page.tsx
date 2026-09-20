"use client";

/**
 * The Platform Console sign-in.
 *
 * Two stages, and the page makes that structural rather than cosmetic: the
 * password form and the code form are different states of one component, and
 * the only path to a session runs through `verifyCode`.
 *
 * There is deliberately no signup link, no trial call to action and no mention
 * of creating an organization. This page is for a handful of NetaMate staff;
 * anything that looks like customer onboarding here is both wrong and an
 * invitation to try.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, LockKeyhole, MailCheck } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/platform/theme";
import {
  describeError,
  usePlatformAuth,
  type ChallengeState,
} from "@/contexts/platform-auth-context";

export default function PlatformLoginPage() {
  const router = useRouter();
  const { user, isLoading, signIn, verifyCode, resendCode } = usePlatformAuth();

  const [stage, setStage] = useState<"password" | "code">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState<ChallengeState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);

  const codeRef = useRef<HTMLInputElement | null>(null);

  // Already signed in — this happens when an operator opens /login with a live
  // session, and bouncing them to the console is friendlier than a form.
  useEffect(() => {
    if (!isLoading && user) router.replace("/platform");
  }, [isLoading, user, router]);

  // The countdown is the honest version of "check your email": when it reaches
  // zero the code really has stopped working, and the page says so instead of
  // leaving somebody typing an expired one.
  useEffect(() => {
    if (stage !== "code" || secondsLeft <= 0) return;
    const timer = window.setInterval(() => setSecondsLeft((value) => value - 1), 1000);
    return () => window.clearInterval(timer);
  }, [stage, secondsLeft]);

  useEffect(() => {
    if (stage === "code") codeRef.current?.focus();
  }, [stage]);

  const submitPassword = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);
      setNotice(null);
      setBusy(true);
      try {
        const issued = await signIn(email.trim(), password);
        setChallenge(issued);
        setSecondsLeft(issued.expiresIn);
        setStage("code");
        setPassword("");
      } catch (caught) {
        setError(
          describeError(caught, "Could not sign in. Check your details and try again."),
        );
      } finally {
        setBusy(false);
      }
    },
    [email, password, signIn],
  );

  const submitCode = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (!challenge) return;
      setError(null);
      setBusy(true);
      try {
        await verifyCode(challenge.challenge, code.trim());
        router.replace("/platform");
      } catch (caught) {
        setError(describeError(caught, "That code is not valid."));
        setCode("");
        codeRef.current?.focus();
      } finally {
        setBusy(false);
      }
    },
    [challenge, code, verifyCode, router],
  );

  const resend = useCallback(async () => {
    if (!challenge) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      // A resend supersedes the previous challenge server-side, so the page
      // must adopt the new token or the next attempt verifies against a code
      // that has already been retired.
      const issued = await resendCode(challenge.challenge);
      setChallenge(issued);
      setSecondsLeft(issued.expiresIn);
      setCode("");
      setNotice("A new code is on its way.");
    } catch (caught) {
      setError(describeError(caught, "Could not send another code just now."));
    } finally {
      setBusy(false);
    }
  }, [challenge, resendCode]);

  const expired = stage === "code" && secondsLeft <= 0;

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
            {stage === "password" ? (
              <>
                <div className="mb-5">
                  <h1 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>
                    Platform Admin sign in
                  </h1>
                  <p className="mt-1 text-xs pf-muted">
                    Restricted to MateMail platform administrators. A security
                    code will be sent to your email to complete sign in.
                  </p>
                </div>

                <form onSubmit={submitPassword} className="space-y-3">
                  <div>
                    <label htmlFor="pf-email" className="pf-label">
                      Email
                    </label>
                    <input
                      id="pf-email"
                      className="pf-input mt-1"
                      type="email"
                      autoComplete="username"
                      required
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                    />
                  </div>

                  <div>
                    <label htmlFor="pf-password" className="pf-label">
                      Password
                    </label>
                    <input
                      id="pf-password"
                      className="pf-input mt-1"
                      type="password"
                      autoComplete="current-password"
                      required
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
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
                    disabled={busy}
                  >
                    {busy ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    ) : (
                      <LockKeyhole className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                    Continue
                  </button>
                </form>

                <div className="mt-4 text-center">
                  <Link
                    href="/platform/forgot-password"
                    className="text-xs font-medium"
                    style={{ color: "var(--pf-accent-text)" }}
                  >
                    Forgot password?
                  </Link>
                </div>
              </>
            ) : (
              <>
                <div className="mb-5">
                  <div
                    className="mb-3 inline-flex h-9 w-9 items-center justify-center rounded-lg"
                    style={{ background: "var(--pf-accent-soft)" }}
                  >
                    <MailCheck
                      className="h-4 w-4"
                      style={{ color: "var(--pf-accent-text)" }}
                      aria-hidden="true"
                    />
                  </div>
                  <h1 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>
                    Enter your security code
                  </h1>
                  <p className="mt-1 text-xs pf-muted">
                    We sent a six-digit code to{" "}
                    <span style={{ color: "var(--pf-text)" }}>{challenge?.sentTo}</span>.
                  </p>
                </div>

                <form onSubmit={submitCode} className="space-y-3">
                  <div>
                    <label htmlFor="pf-code" className="pf-label">
                      Security code
                    </label>
                    <input
                      id="pf-code"
                      ref={codeRef}
                      className="pf-input mt-1 pf-num"
                      style={{ letterSpacing: "0.4em", fontSize: "1.1rem", textAlign: "center" }}
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

                  <p className="text-xs pf-faint">
                    {expired
                      ? "This code has expired. Send another to continue."
                      : `Expires in ${Math.floor(secondsLeft / 60)}:${String(
                          secondsLeft % 60,
                        ).padStart(2, "0")}.`}
                  </p>

                  {notice && (
                    <p className="text-xs" role="status" style={{ color: "var(--pf-ok)" }}>
                      {notice}
                    </p>
                  )}
                  {error && (
                    <p className="text-xs" role="alert" style={{ color: "var(--pf-danger)" }}>
                      {error}
                    </p>
                  )}

                  <button
                    type="submit"
                    className="pf-btn pf-btn-primary w-full"
                    disabled={busy || code.length !== 6 || expired}
                  >
                    {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                    Verify and sign in
                  </button>
                </form>

                <div className="mt-4 flex items-center justify-between text-xs">
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 font-medium"
                    style={{ color: "var(--pf-text-muted)" }}
                    onClick={() => {
                      setStage("password");
                      setCode("");
                      setError(null);
                      setNotice(null);
                    }}
                  >
                    <ArrowLeft className="h-3 w-3" aria-hidden="true" />
                    Back
                  </button>
                  <button
                    type="button"
                    className="font-medium"
                    style={{ color: "var(--pf-accent-text)" }}
                    onClick={resend}
                    disabled={busy}
                  >
                    Send another code
                  </button>
                </div>
              </>
            )}
          </div>

          <p className="mt-4 text-center text-xs pf-faint">
            MateMail · NetaMate Solutions
          </p>
        </div>
      </main>
    </div>
  );
}
