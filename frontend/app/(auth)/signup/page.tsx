"use client";

import { useState } from "react";
import Link from "next/link";
import { Mail } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  AuthButton,
  AuthError,
  AuthField,
  AuthNotice,
  PremiumAuthShell,
} from "@/components/workspace/premium-auth";
import { useAuth } from "@/contexts/auth-context";
import { ApiError } from "@/lib/api";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";

function message(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(" ");
  if (typeof value === "string") return value;
  return "";
}

export default function SignupPage() {
  const router = useRouter();
  const { signup } = useAuth();
  const [form, setForm] = useState({
    email: "",
    password: "",
    full_name: "",
    workspace_name: "",
  });
  const [errors, setErrors] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);

  function set(field: keyof typeof form, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: "" }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setErrors({});
    setLoading(true);
    try {
      await signup(form.email, form.password, form.full_name, form.workspace_name);
      router.push("/verify-email?sent=1");
    } catch (caught) {
      if (caught instanceof ApiError) {
        try {
          const body = JSON.parse(caught.message);
          if (body && typeof body === "object") {
            setErrors(body);
          } else {
            setErrors({ _: "Signup failed. Please try again." });
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

  if (IS_NETAMATE_EMAIL) {
    return (
      <PremiumAuthShell
        title="Account creation isn’t available here."
        description="This dedicated MailAdmin host only accepts existing authorized accounts."
        icon={<Mail className="h-6 w-6" />}
      >
        <div className="auth-form">
          <AuthNotice>
            Create and manage MateMail organization accounts from the main MateMail Portal.
          </AuthNotice>
          <Link href="/login" className="auth-button">
            Back to sign in
          </Link>
        </div>
      </PremiumAuthShell>
    );
  }

  return (
    <PremiumAuthShell
      title="Your team’s next chapter."
      description="Create your MateMail account and organization workspace to apply for Private Beta access."
      icon={<Mail className="h-6 w-6" />}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        {message(errors._) && <AuthError>{message(errors._)}</AuthError>}

        <AuthField
          label="Full name"
          error={message(errors.full_name)}
        >
          <input
            className="auth-input"
            type="text"
            autoComplete="name"
            required
            value={form.full_name}
            onChange={(event) => set("full_name", event.target.value)}
            placeholder="Your full name"
          />
        </AuthField>

        <AuthField
          label="Work email"
          error={message(errors.email)}
        >
          <input
            className="auth-input"
            type="email"
            autoComplete="email"
            required
            value={form.email}
            onChange={(event) => set("email", event.target.value)}
            placeholder="you@yourcompany.com"
          />
        </AuthField>

        <AuthField
          label="Organization name"
          hint="MateMail will create a unique workspace identifier automatically."
          error={message(errors.workspace_name)}
        >
          <input
            className="auth-input"
            type="text"
            required
            value={form.workspace_name}
            onChange={(event) => set("workspace_name", event.target.value)}
            placeholder="Harbor & Co."
          />
        </AuthField>

        <AuthField
          label="Password"
          hint="Use at least 10 characters. Standard password-strength rules apply."
          error={message(errors.password)}
        >
          <input
            className="auth-input"
            type="password"
            autoComplete="new-password"
            minLength={10}
            required
            value={form.password}
            onChange={(event) => set("password", event.target.value)}
          />
        </AuthField>

        <AuthButton type="submit" loading={loading}>
          {loading ? "Creating account…" : "Apply for Private Beta"}
        </AuthButton>

        <p className="auth-switch">
          Already have an account?{" "}
          <Link href="/login" className="auth-text-button">
            Sign in
          </Link>
        </p>
      </form>
    </PremiumAuthShell>
  );
}
