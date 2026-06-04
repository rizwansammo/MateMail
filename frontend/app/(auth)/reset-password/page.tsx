"use client";

export const dynamic = "force-dynamic";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";

function ResetPasswordForm() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    if (password.length < 10) {
      setError("Password must be at least 10 characters.");
      return;
    }
    setLoading(true);
    try {
      await api.post("/api/auth/reset-password/", {
        token,
        new_password: password,
      });
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const body = JSON.parse(err.message);
          setError(body.detail ?? "Reset failed.");
        } catch {
          setError("Reset failed. The link may have expired.");
        }
      }
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <div>
        <h2 className="text-3xl font-black tracking-tight">Invalid link</h2>
        <p className="mt-3 text-sm text-slate-600">
          This password reset link is missing a token. Request a new one.
        </p>
        <Link href="/forgot-password" className="mt-4 inline-block text-sm font-semibold text-cyan-600 hover:underline">
          Request reset link
        </Link>
      </div>
    );
  }

  if (done) {
    return (
      <div>
        <h2 className="text-3xl font-black tracking-tight text-slate-950">Password updated</h2>
        <p className="mt-3 text-sm text-slate-600">
          Your password has been changed. You can now sign in with your new password.
        </p>
        <div className="mt-6">
          <Link href="/login" className="inline-flex items-center justify-center bg-slate-950 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800">
            Sign in
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-3xl font-black tracking-tight text-slate-950">
        Set new password
      </h2>
      <p className="mt-2 text-sm text-slate-500">Choose a strong password of at least 10 characters.</p>

      <form onSubmit={handleSubmit} className="mt-8 space-y-5">
        {error && (
          <div className="border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div>
        )}

        {[
          { id: "password", label: "New password", value: password, set: setPassword },
          { id: "confirm", label: "Confirm password", value: confirm, set: setConfirm },
        ].map(({ id, label, value, set }) => (
          <label key={id} className="block">
            <span className="mb-2 block text-sm font-semibold text-slate-800">{label}</span>
            <input
              type="password"
              required
              value={value}
              onChange={(e) => set(e.target.value)}
              placeholder="••••••••••"
              className="w-full border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10"
            />
          </label>
        ))}

        <button
          type="submit"
          disabled={loading}
          className="inline-flex w-full items-center justify-center bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "Saving…" : "Set new password"}
        </button>
      </form>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetPasswordForm />
    </Suspense>
  );
}
