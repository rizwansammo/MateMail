"use client";

export const dynamic = "force-dynamic";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/contexts/auth-context";
import { ApiError } from "@/lib/api";

function TwoFactorContent() {
  const router = useRouter();
  const params = useSearchParams();
  const partial_token = params.get("token") ?? "";
  const { verify2fa } = useAuth();

  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [useBackup, setUseBackup] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await verify2fa(partial_token, code);
      router.push("/app");
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const body = JSON.parse(err.message);
          setError(body.detail ?? "Invalid code.");
        } catch {
          setError("Invalid code. Please try again.");
        }
      }
    } finally {
      setLoading(false);
    }
  }

  if (!partial_token) {
    return (
      <div>
        <h2 className="text-3xl font-black tracking-tight text-slate-950">Session expired</h2>
        <p className="mt-3 text-sm text-slate-500">Please sign in again.</p>
        <Link href="/login" className="mt-4 inline-block text-sm font-semibold text-cyan-600 hover:underline">
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-3xl font-black tracking-tight text-slate-950">
        Two-factor authentication
      </h2>
      <p className="mt-2 text-sm text-slate-500">
        {useBackup
          ? "Enter one of your 8-character backup codes."
          : "Enter the 6-digit code from your authenticator app."}
      </p>

      <form onSubmit={handleSubmit} className="mt-8 space-y-5">
        {error && (
          <div className="border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div>
        )}

        <label className="block">
          <span className="mb-2 block text-sm font-semibold text-slate-800">
            {useBackup ? "Backup code" : "Authentication code"}
          </span>
          <input
            type="text"
            required
            autoFocus
            autoComplete="one-time-code"
            inputMode={useBackup ? "text" : "numeric"}
            maxLength={useBackup ? 8 : 6}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder={useBackup ? "XXXXXXXX" : "123456"}
            className="w-full border border-slate-300 bg-white px-3 py-2.5 text-center text-xl font-mono tracking-[0.4em] text-slate-900 outline-none transition focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10"
          />
        </label>

        <button
          type="submit"
          disabled={loading}
          className="inline-flex w-full items-center justify-center bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "Verifying…" : "Verify"}
        </button>

        <button
          type="button"
          onClick={() => { setUseBackup(!useBackup); setCode(""); setError(""); }}
          className="w-full text-sm font-semibold text-cyan-600 hover:underline"
        >
          {useBackup ? "Use authenticator app instead" : "Use a backup code instead"}
        </button>

        <p className="text-center text-sm text-slate-400">
          <Link href="/login" className="hover:underline">
            Sign in to a different account
          </Link>
        </p>
      </form>
    </div>
  );
}

export default function TwoFactorPage() {
  return (
    <Suspense>
      <TwoFactorContent />
    </Suspense>
  );
}
