"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/auth-context";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await login(email, password);
      if (result.requires_2fa && result.partial_token) {
        router.push(`/2fa?token=${encodeURIComponent(result.partial_token)}&next=${encodeURIComponent(next)}`);
      } else {
        router.push(next);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const body = JSON.parse(err.message);
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

  return (
    <div>
      <h2 className="text-3xl font-black tracking-tight text-slate-950">
        Sign in
      </h2>
      <p className="mt-2 text-sm text-slate-500">
        Welcome back to MateMail.
      </p>

      <form onSubmit={handleSubmit} className="mt-8 space-y-5">
        {error && (
          <div className="border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
            {error}
          </div>
        )}

        <label className="block">
          <span className="mb-2 block text-sm font-semibold text-slate-800">
            Email address
          </span>
          <input
            type="email"
            required
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            className="w-full border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10"
          />
        </label>

        <label className="block">
          <span className="mb-2 block text-sm font-semibold text-slate-800">
            Password
          </span>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••••"
            className="w-full border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10"
          />
        </label>

        <div className="flex items-center justify-between">
          <Link
            href="/forgot-password"
            className="text-sm font-semibold text-cyan-600 hover:underline"
          >
            Forgot password?
          </Link>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="inline-flex w-full items-center justify-center bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>

        <p className="text-center text-sm text-slate-500">
          Don&apos;t have an account?{" "}
          <Link
            href="/signup"
            className="font-semibold text-cyan-600 hover:underline"
          >
            Start free trial
          </Link>
        </p>
      </form>
    </div>
  );
}
