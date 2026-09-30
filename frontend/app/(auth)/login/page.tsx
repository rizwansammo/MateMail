"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { KeyRound, Loader2, Mail } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import {
  WorkspaceThemeProvider,
  WorkspaceThemeToggle,
} from "@/components/workspace/theme";
import { useAuth } from "@/contexts/auth-context";
import { ApiError } from "@/lib/api";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";

export default function LoginPage() {
  return (
    <WorkspaceThemeProvider>
      <WorkspaceLogin />
    </WorkspaceThemeProvider>
  );
}

function WorkspaceLogin() {
  const router = useRouter();
  const requestedNext =
    typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("next") || ""
      : "";
  const next = requestedNext.startsWith("/app/") ? requestedNext : "/app";
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await login(email, password);
      if (result.requires_2fa && result.partial_token) {
        router.push(
          `/2fa?token=${encodeURIComponent(result.partial_token)}&next=${encodeURIComponent(next)}`,
        );
      } else {
        router.push(next);
      }
    } catch (caught) {
      if (caught instanceof ApiError) {
        try {
          const body = JSON.parse(caught.message);
          setError(body.detail ?? "Login failed.");
        } catch {
          setError("Login failed. Check your credentials.");
        }
      } else {
        setError("Network error. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  if (IS_NETAMATE_EMAIL) {
    return (
      <div className="ws flex min-h-screen flex-col bg-slate-50 text-slate-950">
        <main className="flex flex-1 items-center justify-center px-4 py-10">
          <div className="w-full max-w-sm">
            <div className="mb-6 flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <BrandMark size={34} preload />
                <div>
                  <p className="text-base font-black leading-none text-slate-950">MateMail</p>
                  <p className="mt-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                    MailAdmin
                  </p>
                </div>
              </div>
              <WorkspaceThemeToggle />
            </div>

            <div className="border border-slate-200 bg-white p-6 shadow-2xl">
              <h1 className="text-base font-semibold text-slate-950">
                Sign in to MailAdmin
              </h1>
              <p className="mb-5 mt-1 text-xs text-slate-500">
                Manage NetaMate Solutions domains, mailboxes and email settings with MateMail.
              </p>

              <form onSubmit={handleSubmit} className="space-y-3" noValidate>
                <div>
                  <label htmlFor="workspace-email" className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                    Email address
                  </label>
                  <div className="relative mt-1">
                    <Mail
                      className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
                      aria-hidden="true"
                    />
                    <input
                      id="workspace-email"
                      type="email"
                      autoComplete="username"
                      autoFocus
                      required
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      placeholder="you@netamate.com"
                      className="w-full border border-slate-300 bg-white py-2.5 pl-8 pr-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-cyan-600 focus:ring-1 focus:ring-cyan-600"
                    />
                  </div>
                </div>

                <div>
                  <label htmlFor="workspace-password" className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                    Password
                  </label>
                  <div className="relative mt-1">
                    <KeyRound
                      className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
                      aria-hidden="true"
                    />
                    <input
                      id="workspace-password"
                      type="password"
                      autoComplete="current-password"
                      required
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      className="w-full border border-slate-300 bg-white py-2.5 pl-8 pr-3 text-sm text-slate-900 outline-none transition focus:border-cyan-600 focus:ring-1 focus:ring-cyan-600"
                    />
                  </div>
                </div>

                {error && (
                  <p className="text-xs text-red-600" role="alert">
                    {error}
                  </p>
                )}

                <button
                  type="submit"
                  disabled={loading}
                  className="inline-flex w-full items-center justify-center gap-2 bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                  {loading ? "Signing in…" : "Sign in"}
                </button>
              </form>

              <div className="mt-4 border-t border-slate-200 pt-3">
                <Link
                  href="/forgot-password"
                  className="text-xs font-semibold text-cyan-700 hover:underline"
                >
                  Forgotten your password?
                </Link>
              </div>
            </div>

            <p className="mt-4 text-center text-xs text-slate-400">
              MateMail MailAdmin · NetaMate Solutions
            </p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="ws flex min-h-screen flex-col bg-slate-50 text-slate-950">
      <main className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <BrandMark size={34} preload />
              <div>
                <p className="text-base font-black leading-none text-slate-950">MateMail</p>
                <p className="mt-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">
                  Portal
                </p>
              </div>
            </div>
            <WorkspaceThemeToggle />
          </div>

          <div className="border border-slate-200 bg-white p-6 shadow-2xl">
            <h1 className="text-base font-semibold text-slate-950">
              Sign in to MateMail Workspace
            </h1>
            <p className="mb-5 mt-1 text-xs text-slate-500">
              Manage your organization&apos;s domains, mailboxes and email settings.
            </p>

            <form onSubmit={handleSubmit} className="space-y-3" noValidate>
              <div>
                <label htmlFor="workspace-email" className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                  Email address
                </label>
                <div className="relative mt-1">
                  <Mail
                    className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
                    aria-hidden="true"
                  />
                  <input
                    id="workspace-email"
                    type="email"
                    autoComplete="username"
                    autoFocus
                    required
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="you@company.com"
                    className="w-full border border-slate-300 bg-white py-2.5 pl-8 pr-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-cyan-600 focus:ring-1 focus:ring-cyan-600"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="workspace-password" className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                  Password
                </label>
                <div className="relative mt-1">
                  <KeyRound
                    className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
                    aria-hidden="true"
                  />
                  <input
                    id="workspace-password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    className="w-full border border-slate-300 bg-white py-2.5 pl-8 pr-3 text-sm text-slate-900 outline-none transition focus:border-cyan-600 focus:ring-1 focus:ring-cyan-600"
                  />
                </div>
              </div>

              {error && (
                <p className="text-xs text-red-600" role="alert">
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={loading}
                className="inline-flex w-full items-center justify-center gap-2 bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                {loading ? "Signing in…" : "Sign in"}
              </button>
            </form>

            <div className="mt-4 border-t border-slate-200 pt-3">
              <Link
                href="/forgot-password"
                className="text-xs font-semibold text-cyan-700 hover:underline"
              >
                Forgotten your password?
              </Link>
            </div>
          </div>

          <p className="mt-4 text-center text-xs text-slate-400">
            MateMail Portal · NetaMate Solutions
          </p>
        </div>
      </main>
    </div>
  );
}
