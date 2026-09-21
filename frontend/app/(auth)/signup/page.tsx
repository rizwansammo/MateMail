"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/auth-context";
import { ApiError } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const { signup } = useAuth();
  const [form, setForm] = useState({
    email: "",
    password: "",
    full_name: "",
    workspace_name: "",
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);

  function set(field: string, value: string) {
    setForm((f) => ({ ...f, [field]: value }));
    setErrors((e) => ({ ...e, [field]: "" }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrors({});
    setLoading(true);
    try {
      await signup(
        form.email,
        form.password,
        form.full_name,
        form.workspace_name
      );
      router.push("/app/onboarding");
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const body = JSON.parse(err.message);
          if (typeof body === "object") {
            setErrors(body);
          } else {
            setErrors({ _: body.detail ?? "Signup failed." });
          }
        } catch {
          setErrors({ _: "Signup failed. Please try again." });
        }
      } else {
        setErrors({ _: "Network error. Please try again." });
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <h2 className="text-3xl font-black tracking-tight text-slate-950">
        Create your account
      </h2>
      <p className="mt-2 text-sm text-slate-500">
        Apply for Private Beta access — approval is required.
      </p>

      <form onSubmit={handleSubmit} className="mt-8 space-y-4">
        {errors._ && (
          <div className="border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
            {errors._}
          </div>
        )}

        {(
          [
            { id: "full_name", label: "Full name", type: "text", placeholder: "Jane Smith" },
            { id: "email", label: "Work email", type: "email", placeholder: "you@company.com" },
            { id: "workspace_name", label: "Workspace name", type: "text", placeholder: "Acme Corp" },
            { id: "password", label: "Password", type: "password", placeholder: "Min 10 characters" },
          ] as const
        ).map(({ id, label, type, placeholder }) => (
          <label key={id} className="block">
            <span className="mb-2 block text-sm font-semibold text-slate-800">
              {label}
            </span>
            <input
              type={type}
              required
              value={form[id]}
              onChange={(e) => set(id, e.target.value)}
              placeholder={placeholder}
              className="w-full border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10"
            />
            {errors[id] && (
              <p className="mt-1 text-xs text-rose-600">{errors[id]}</p>
            )}
          </label>
        ))}

        <button
          type="submit"
          disabled={loading}
          className="inline-flex w-full items-center justify-center bg-cyan-500 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "Creating account…" : "Apply for Private Beta"}
        </button>

        <p className="text-center text-sm text-slate-500">
          Already have an account?{" "}
          <Link
            href="/login"
            className="font-semibold text-cyan-600 hover:underline"
          >
            Sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
