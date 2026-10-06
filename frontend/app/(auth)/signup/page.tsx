"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Mail } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  AuthButton,
  AuthError,
  AuthField,
  AuthNotice,
  PremiumAuthShell,
} from "@/components/workspace/premium-auth";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest, ApiError } from "@/lib/api";

function message(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(" ");
  if (typeof value === "string") return value;
  return "";
}

interface InvitePreview {
  valid: boolean;
  email?: string;
  tenant_name?: string;
  detail?: string;
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
  const [inviteToken, setInviteToken] = useState("");
  const [invitePreview, setInvitePreview] = useState<InvitePreview | null>(null);
  const [inviteLoading, setInviteLoading] = useState(false);

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("invite")?.trim() || "";
    if (!token) return;

    setInviteToken(token);
    setInviteLoading(true);
    apiRequest("/api/teams/invites/preview/?token=" + encodeURIComponent(token))
      .then(async (response) => response.json())
      .then((data: InvitePreview) => {
        setInvitePreview(data);
        if (data.valid && data.email) {
          setForm((current) => ({ ...current, email: data.email || "" }));
        }
      })
      .catch(() => {
        setInvitePreview({ valid: false, detail: "Could not load this invitation." });
      })
      .finally(() => setInviteLoading(false));
  }, []);

  function set(field: keyof typeof form, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: "" }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setErrors({});
    setLoading(true);
    try {
      await signup(
        form.email,
        form.password,
        form.full_name,
        inviteToken ? undefined : form.workspace_name,
        inviteToken || undefined,
      );
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


  if (inviteLoading) {
    return (
      <PremiumAuthShell
        title="Checking your invitation…"
        description="MateMail Hub is confirming the organization and email address."
        icon={<Mail className="h-6 w-6" />}
      >
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
        </div>
      </PremiumAuthShell>
    );
  }

  if (inviteToken && invitePreview && !invitePreview.valid) {
    return (
      <PremiumAuthShell
        title="This invitation can’t be used."
        description={invitePreview.detail || "The invitation may have expired, been revoked, or no longer match an organization-owned domain."}
        icon={<Mail className="h-6 w-6" />}
      >
        <div className="auth-form">
          <Link href="/login" className="auth-button">
            Back to sign in
          </Link>
        </div>
      </PremiumAuthShell>
    );
  }

  const invited = Boolean(inviteToken && invitePreview?.valid);

  return (
    <PremiumAuthShell
      title={invited ? "Join your organization." : "Your team’s next chapter."}
      description={
        invited
          ? <>Create your MateMail Hub account for <strong>{invitePreview?.tenant_name}</strong>. No additional organization will be created.</>
          : "Create your MateMail account and organization to apply for Private Beta access."
      }
      icon={<Mail className="h-6 w-6" />}
    >
      <form onSubmit={handleSubmit} className="auth-form">
        {message(errors._) && <AuthError>{message(errors._)}</AuthError>}
        {message(errors.detail) && <AuthError>{message(errors.detail)}</AuthError>}
        {message(errors.invite_token) && <AuthError>{message(errors.invite_token)}</AuthError>}

        <AuthField label="Full name" error={message(errors.full_name)}>
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
          hint={invited ? "This address is fixed by the organization invitation." : undefined}
          error={message(errors.email)}
        >
          <input
            className="auth-input"
            type="email"
            autoComplete="email"
            required
            readOnly={invited}
            value={form.email}
            onChange={(event) => set("email", event.target.value)}
            placeholder="you@yourcompany.com"
          />
        </AuthField>

        {!invited && (
          <AuthField
            label="Organization name"
            hint="MateMail will create one organization for this account."
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
        )}

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
          {loading
            ? "Creating account…"
            : invited
              ? "Create account & join organization"
              : "Apply for Private Beta"}
        </AuthButton>

        <p className="auth-switch">
          Already have an account?{" "}
          <Link
            href={inviteToken
              ? "/login?next=" + encodeURIComponent("/accept-invite?token=" + inviteToken)
              : "/login"}
            className="auth-text-button"
          >
            Sign in
          </Link>
        </p>
      </form>
    </PremiumAuthShell>
  );
}
