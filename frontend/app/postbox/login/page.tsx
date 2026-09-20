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
import Image from "next/image";
import { useRouter } from "next/navigation";
import { KeyRound, Loader2, Mail } from "lucide-react";

import { describePostBoxError, usePostBox } from "@/contexts/postbox-context";

export default function PostBoxLoginPage() {
  const router = useRouter();
  const { mailbox, isLoading, signIn } = usePostBox();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  useEffect(() => {
    if (!isLoading && mailbox) router.replace("/postbox");
  }, [isLoading, mailbox, router]);

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
    <div
      className="pb flex min-h-screen flex-col"
      style={{ background: "var(--pb-surface)" }}
    >
      <main className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex items-center gap-3">
            <Image
              src="/matemail-logo.png"
              alt=""
              width={34}
              height={25}
              priority
            />
            <div>
              <p
                className="pb-brand text-base leading-none"
                style={{ color: "var(--pb-primary)" }}
              >
                MateMail
              </p>
              <p className="pb-label mt-1">PostBox</p>
            </div>
          </div>

          <div className="pb-panel p-6" style={{ boxShadow: "var(--pb-shadow-lg)" }}>
            <h1 className="text-base font-semibold">Sign in to your mailbox</h1>
            <p className="mb-5 mt-1 text-xs pb-muted">
              Use your full email address and mailbox password.
            </p>

            <form onSubmit={submit} className="space-y-3" noValidate>
              <div>
                <label htmlFor="pb-email" className="pb-label">
                  Email address
                </label>
                <div className="relative mt-1">
                  <Mail
                    className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
                    style={{ color: "var(--pb-subtle)" }}
                    aria-hidden="true"
                  />
                  <input
                    id="pb-email"
                    className="pb-input"
                    style={{ paddingLeft: "1.9rem" }}
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

              <div>
                <label htmlFor="pb-password" className="pb-label">
                  Password
                </label>
                <div className="relative mt-1">
                  <KeyRound
                    className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
                    style={{ color: "var(--pb-subtle)" }}
                    aria-hidden="true"
                  />
                  <input
                    id="pb-password"
                    className="pb-input"
                    style={{ paddingLeft: "1.9rem" }}
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </div>
              </div>

              <label className="flex items-center gap-2 text-xs pb-muted">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(event) => setRemember(event.target.checked)}
                />
                Keep me signed in on this device
              </label>

              {error && (
                <p
                  className="text-xs"
                  role="alert"
                  style={{ color: "var(--pb-danger)" }}
                >
                  {error}
                </p>
              )}

              <button
                type="submit"
                className="pb-btn pb-btn-primary w-full"
                disabled={busy}
              >
                {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                Sign in
              </button>
            </form>

            <div className="mt-4 border-t pt-3" style={{ borderColor: "var(--pb-border)" }}>
              <button
                type="button"
                className="text-xs font-medium"
                style={{ color: "var(--pb-accent-fg)" }}
                aria-expanded={showHelp}
                onClick={() => setShowHelp((open) => !open)}
              >
                Forgotten your password?
              </button>
              {showHelp && (
                <p className="mt-2 text-xs pb-muted">
                  Mailbox passwords are reset by your organization&rsquo;s MateMail
                  administrator. MateMail cannot email you a reset link, because
                  it would go to the mailbox you are trying to open.
                </p>
              )}
            </div>
          </div>

          <p className="mt-4 text-center text-xs pb-subtle">
            MateMail PostBox · NetaMate Solutions
          </p>
        </div>
      </main>
    </div>
  );
}
