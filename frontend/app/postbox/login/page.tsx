"use client";

/**
 * MateMail PostBox sign-in.
 *
 * For people who already have a mailbox. There is deliberately no signup, no
 * "start a trial", no organization creation and no Platform link — those are
 * different products on different hostnames, and offering them here would
 * invite attempts from people this form cannot help.
 *
 * There is also no self-service password recovery. A mailbox user's business
 * email may be the very account they cannot open, so emailing a reset link to
 * it solves nothing; the honest answer is their organization's MateMail
 * administrator, and that is what the page says.
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, Loader2, Mail } from "lucide-react";

import { describePostBoxError, usePostBox } from "@/contexts/postbox-context";
import { postbox, type SavedPostBoxAccount } from "@/lib/postbox-api";

export default function PostBoxLoginPage() {
  const router = useRouter();
  const { mailbox, isLoading, signIn } = usePostBox();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [savedAccounts, setSavedAccounts] = useState<SavedPostBoxAccount[]>([]);
  const [switchingAccount, setSwitchingAccount] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoading && mailbox) router.replace("/postbox");
  }, [isLoading, mailbox, router]);

  useEffect(() => {
    let cancelled = false;
    postbox
      .accounts()
      .then((value) => {
        if (!cancelled) setSavedAccounts(value.results);
      })
      .catch(() => {
        if (!cancelled) setSavedAccounts([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);
      setBusy(true);
      try {
        await signIn(email.trim(), password, remember);
        router.replace("/postbox");
      } catch (caught) {
        setError(describePostBoxError(caught, "Those details are not correct."));
        setPassword("");
      } finally {
        setBusy(false);
      }
    },
    [email, password, remember, signIn, router],
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
        <section className="pb-premium-login-card" aria-labelledby="postbox-login-title">
          <span className="pb-premium-login-emblem">
            <Mail className="h-[26px] w-[26px]" aria-hidden="true" />
          </span>
          <h1 id="postbox-login-title">Welcome back.</h1>
          <p>Sign in to your PostBox mailbox.</p>

          {savedAccounts.length > 0 && (
            <div className="pb-premium-login-saved">
              <span className="pb-premium-login-saved-label">Accounts on this device</span>
              {savedAccounts.map((item) => {
                const label = item.mailbox.full_name || item.mailbox.email;
                const initials = label
                  .split(/\s+|@/)
                  .filter(Boolean)
                  .slice(0, 2)
                  .map((part) => part[0]?.toUpperCase())
                  .join("");
                const switching = switchingAccount === item.session_id;
                return (
                  <button
                    key={item.session_id}
                    type="button"
                    disabled={Boolean(switchingAccount)}
                    onClick={async () => {
                      setError(null);
                      setSwitchingAccount(item.session_id);
                      try {
                        await postbox.switchAccount(item.session_id);
                        window.location.assign("/postbox?folder=INBOX");
                      } catch {
                        setSavedAccounts((current) =>
                          current.filter(
                            (saved) => saved.session_id !== item.session_id,
                          ),
                        );
                        setError("This account needs to be signed in again.");
                        setSwitchingAccount(null);
                      }
                    }}
                  >
                    <span className="pb-premium-login-saved-avatar" aria-hidden="true">
                      {initials || "PB"}
                    </span>
                    <span className="pb-premium-login-saved-copy">
                      <strong>{label}</strong>
                      <small>{item.mailbox.email}</small>
                    </span>
                    {switching && (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    )}
                  </button>
                );
              })}
              <div className="pb-premium-login-or"><span>or sign in with another account</span></div>
            </div>
          )}

          <form onSubmit={submit} className="pb-premium-login-form" noValidate>
            <div className="pb-premium-field">
              <label htmlFor="pb-email">Email address</label>
              <div className="pb-premium-field-wrap">
                <Mail className="h-4 w-4" aria-hidden="true" />
                <input
                  id="pb-email"
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
              <label htmlFor="pb-password">Password</label>
              <div className="pb-premium-field-wrap">
                <KeyRound className="h-4 w-4" aria-hidden="true" />
                <input
                  id="pb-password"
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
              Remember me on this device
            </label>

            {error && (
              <p className="pb-premium-login-error" role="alert">
                {error}
              </p>
            )}

            <button type="submit" className="pb-premium-login-submit" disabled={busy}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div className="pb-premium-login-help">
            <button
              type="button"
              aria-expanded={showHelp}
              onClick={() => setShowHelp((open) => !open)}
            >
              Forgotten your password?
            </button>
            {showHelp && (
              <p>
                Mailbox passwords are managed by your organization&rsquo;s MateMail
                administrator. Contact your organization administrator to reset access.
              </p>
            )}
          </div>
        </section>
      </main>

      <footer className="pb-premium-login-footer">
        <span>PostBox by MateMail</span>
        <span>
          <Mail className="h-3.5 w-3.5" aria-hidden="true" />
          A focused space for your work.
        </span>
      </footer>
    </div>
  );
}
