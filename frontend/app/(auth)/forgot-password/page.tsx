"use client";

import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.post("/api/auth/forgot-password/", { email });
      setSent(true);
    } catch (err) {
      if (err instanceof ApiError && err.status !== 200) {
        setError("Something went wrong. Please try again.");
      } else {
        setSent(true); // Always show success to avoid email enumeration
      }
    } finally {
      setLoading(false);
    }
  }

  if (sent) {
    return (
      <div>
        <h2 className="text-3xl font-black tracking-tight text-slate-950">
          Check your inbox
        </h2>
        <p className="mt-3 text-sm text-slate-600">
          If <strong>{email}</strong> is registered, a password reset link has
          been sent. Check your spam folder if it doesn&apos;t arrive within a
          few minutes.
        </p>
        <p className="mt-2 text-xs text-slate-400">Reset links expire in 1 hour.</p>
        <div className="mt-6">
          <Link
            href="/login"
            className="text-sm font-semibold text-cyan-600 hover:underline"
          >
            Back to sign in
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-3xl font-black tracking-tight text-slate-950">
        Reset password
      </h2>
      <p className="mt-2 text-sm text-slate-500">
        Enter your email and we&apos;ll send you a secure reset link.
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

        <button
          type="submit"
          disabled={loading}
          className="inline-flex w-full items-center justify-center bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "Sending…" : "Send reset link"}
        </button>

        <p className="text-center">
          <Link
            href="/login"
            className="text-sm font-semibold text-slate-600 hover:underline"
          >
            Back to sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
