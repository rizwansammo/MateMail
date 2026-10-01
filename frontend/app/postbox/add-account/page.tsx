"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { ArrowLeft, KeyRound, Loader2, Mail, ShieldCheck, UserPlus } from "lucide-react";

import { describePostBoxError, usePostBox } from "@/contexts/postbox-context";

export default function PostBoxAddAccountPage() {
  const { signIn } = usePostBox();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);
      setBusy(true);
      try {
        await signIn(email.trim(), password, remember);
        window.location.assign("/postbox?folder=INBOX");
      } catch (caught) {
        setError(describePostBoxError(caught, "Those details are not correct."));
        setPassword("");
        setBusy(false);
      }
    },
    [email, password, remember, signIn],
  );

  return (
    <div className="pb pb-premium-login">
      <header className="pb-premium-login-top">
        <div className="pb-premium-login-brand">
          <svg
            className="pb-premium-login-mark"
            viewBox="0 0 64 64"
            role="img"
            aria-label="PostBox"
          >
            <rect width="64" height="64" fill="#0B1F44" />
            <path
              fill="#FFFFFF"
              d="M13.5 27L13.5 50.5L50.5 50.5L50.5 27L45.5 27L45.5 45.5L18.5 45.5L18.5 27Z"
            />
            <path
              fill="#6FA8FF"
              d="M13.5 12L32 25.875L50.5 12L50.5 18.25L32 32.125L13.5 18.25Z"
            />
          </svg>
          <span className="pb-premium-wordmark">PostBox</span>
        </div>
      </header>

      <main className="pb-premium-login-main">
        <section className="pb-premium-login-card" aria-labelledby="postbox-add-account-title">
          <span className="pb-premium-login-emblem">
            <UserPlus className="h-[26px] w-[26px]" aria-hidden="true" />
          </span>
          <h1 id="postbox-add-account-title">Add another account.</h1>
          <p>Sign in once, then switch between your PostBox accounts from the profile menu.</p>

          <form onSubmit={submit} className="pb-premium-login-form" noValidate>
            <div className="pb-premium-field">
              <label htmlFor="pb-add-email">Email address</label>
              <div className="pb-premium-field-wrap">
                <Mail className="h-4 w-4" aria-hidden="true" />
                <input
                  id="pb-add-email"
                  type="email"
                  autoComplete="username"
                  autoFocus
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@yourcompany.com"
                />
              </div>
            </div>

            <div className="pb-premium-field">
              <label htmlFor="pb-add-password">Password</label>
              <div className="pb-premium-field-wrap">
                <KeyRound className="h-4 w-4" aria-hidden="true" />
                <input
                  id="pb-add-password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
            </div>

            <label className="pb-premium-remember">
              <input
                type="checkbox"
                checked={remember}
                onChange={(event) => setRemember(event.target.checked)}
              />
              Keep this account available on this device for 30 days
            </label>

            {error && (
              <p className="pb-premium-login-error" role="alert">
                {error}
              </p>
            )}

            <button type="submit" className="pb-premium-login-submit" disabled={busy}>
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <UserPlus className="h-4 w-4" aria-hidden="true" />
              )}
              {busy ? "Adding account…" : "Add account"}
            </button>
          </form>

          <div className="pb-premium-login-help">
            <p className="flex items-start gap-2">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              PostBox keeps an opaque, HttpOnly browser session capability, not your mailbox
              password. The session remains revocable from Settings → Security.
            </p>
            <Link href="/postbox" className="mt-4 inline-flex items-center gap-2">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Back to PostBox
            </Link>
          </div>
        </section>
      </main>

      <footer className="pb-premium-login-footer">
        <span>PostBox by MateMail</span>
        <span>
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
          Account sessions stay isolated.
        </span>
      </footer>
    </div>
  );
}
