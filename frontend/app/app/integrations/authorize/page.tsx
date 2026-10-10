"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  AppWindow,
  CheckCircle2,
  KeyRound,
  LockKeyhole,
  Mail,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
} from "@/components/workspace/premium-ui";

type Permission = { key: string; label: string };

type Details = {
  name: string;
  purpose_label: string;
  organization: string;
  mailbox_email: string;
  mailbox_name: string;
  permissions: Permission[];
  user_2fa_required: boolean;
  mailbox_verification_required: boolean;
  mailbox_verification_label: string;
  request_status: string;
};

function AuthorizationContent() {
  const params = useSearchParams();
  const requestToken = params.get("request") || "";

  const [details, setDetails] = useState<Details | null>(null);
  const [loading, setLoading] = useState(true);
  const [password, setPassword] = useState("");
  const [twoFactorCode, setTwoFactorCode] = useState("");
  const [mailboxFactorCode, setMailboxFactorCode] = useState("");
  const [error, setError] = useState("");
  const [approved, setApproved] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const loadRequest = useCallback(async () => {
    if (!requestToken) {
      setLoading(false);
      setError("Missing connection request.");
      return;
    }

    setLoading(true);
    setError("");
    try {
      const response = await apiRequest(
        "/api/integrations/authorize/?request=" + encodeURIComponent(requestToken)
      );
      const body = await response.json().catch(() => ({}));

      if (response.status === 401) {
        const next = window.location.pathname + window.location.search;
        window.location.href = "/login?next=" + encodeURIComponent(next);
        return;
      }

      if (!response.ok) {
        setError(body.detail || "This connection request could not be loaded.");
        return;
      }

      setDetails(body);
      setApproved(body.request_status === "approved");
    } catch {
      setError("This connection request could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [requestToken]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadRequest(), 0);
    return () => window.clearTimeout(timer);
  }, [loadRequest]);

  async function approve(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await apiRequest(
        "/api/integrations/authorize/?request=" + encodeURIComponent(requestToken),
        {
          method: "POST",
          body: JSON.stringify({
            password,
            two_factor_code: twoFactorCode,
            mailbox_factor_code: mailboxFactorCode,
          }),
        }
      );
      const body = await response.json().catch(() => ({}));

      if (!response.ok) {
        setError(
          body.password?.[0] ||
          body.two_factor_code?.[0] ||
          body.mailbox_factor_code?.[0] ||
          body.detail ||
          "Authorization failed."
        );
        return;
      }

      setApproved(true);
      setPassword("");
      setTwoFactorCode("");
      setMailboxFactorCode("");
    } catch {
      setError("Authorization failed.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="portal-page astra-resource-page astra-advanced-page astra-authorization-page">
        <PortalSkeleton className="mx-auto h-[420px] max-w-[650px]" />
      </div>
    );
  }

  if (approved) {
    return (
      <div className="portal-page astra-resource-page astra-advanced-page astra-authorization-page">
        <div className="portal-authz-wrap">
          <PortalCard>
            <div className="portal-authz-success">
              <span className="portal-empty-icon"><CheckCircle2 className="h-5 w-5" /></span>
              <h1>MateMail connected</h1>
              <p>
                {details?.name || "The connected application"} can now use the approved mailbox within the exact permissions you accepted.
              </p>
              <PortalButton type="button" onClick={() => window.close()}>Close window</PortalButton>
            </div>
          </PortalCard>
        </div>
      </div>
    );
  }

  if (!details) {
    return (
      <div className="portal-page astra-resource-page astra-advanced-page astra-authorization-page">
        <div className="portal-authz-wrap">
          <PortalNotice tone="danger">{error || "This connection request is unavailable."}</PortalNotice>
        </div>
      </div>
    );
  }

  return (
    <div className="portal-page astra-resource-page astra-advanced-page astra-authorization-page">
      <div className="portal-authz-wrap">
        <PortalPageHeading
          eyebrow="CONNECTED APP AUTHORIZATION"
          title="Connect MateMail"
          description={"Review exactly what " + details.name + " is asking to access before approving it."}
        />

        <PortalCard className="portal-authz-card">
          <div className="portal-authz-app">
            <span className="portal-plan-symbol"><AppWindow className="h-5 w-5" /></span>
            <div>
              <strong>{details.name}</strong>
              <span>{details.purpose_label}</span>
            </div>
          </div>

          <div className="portal-authz-facts">
            <div>
              <span>Organization</span>
              <strong>{details.organization}</strong>
            </div>
            <div>
              <span>Mailbox</span>
              <strong>{details.mailbox_name ? details.mailbox_name + " — " : ""}{details.mailbox_email}</strong>
            </div>
          </div>

          <div className="portal-authz-permissions">
            <h2>{details.name} will be able to:</h2>
            {details.permissions.map((permission) => (
              <div key={permission.key}>
                <CheckCircle2 className="h-4 w-4" />
                <span>{permission.label}</span>
              </div>
            ))}
          </div>

          <div className="my-5">
            <PortalNotice tone="info">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Approval is bound to this organization and this exact mailbox. The app does not receive your MateMail password.</span>
            </PortalNotice>
          </div>

          <form onSubmit={approve}>
            {error && <div className="mb-4"><PortalNotice tone="danger">{error}</PortalNotice></div>}

            <div className="portal-field">
              <label>Confirm your MateMail password</label>
              <div className="portal-input-with-icon">
                <LockKeyhole className="h-4 w-4" />
                <input
                  type="password"
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
            </div>

            {details.user_2fa_required && (
              <div className="portal-field mt-4">
                <label>Authentication code</label>
                <div className="portal-input-with-icon">
                  <Smartphone className="h-4 w-4" />
                  <input
                    required
                    value={twoFactorCode}
                    onChange={(event) => setTwoFactorCode(event.target.value)}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="Authenticator or backup code"
                  />
                </div>
                <div className="portal-field-hint">Use your authenticator code or a valid backup code.</div>
              </div>
            )}

            {details.mailbox_verification_required && (
              <div className="portal-field mt-4">
                <label>{details.mailbox_verification_label}</label>
                <div className="portal-input-with-icon">
                  <Mail className="h-4 w-4" />
                  <input
                    required
                    value={mailboxFactorCode}
                    onChange={(event) => setMailboxFactorCode(event.target.value)}
                    autoComplete="one-time-code"
                  />
                </div>
              </div>
            )}

            <div className="portal-detail-actions">
              <PortalButton type="submit" disabled={submitting || !password}>
                <KeyRound className="h-4 w-4" />
                {submitting ? "Connecting…" : "Approve connected app"}
              </PortalButton>
              <PortalButton type="button" variant="secondary" onClick={() => window.close()} disabled={submitting}>
                Cancel
              </PortalButton>
            </div>
          </form>
        </PortalCard>
      </div>
    </div>
  );
}

export default function AuthorizationPage() {
  return (
    <Suspense fallback={<div className="portal-page astra-resource-page astra-advanced-page astra-authorization-page"><PortalSkeleton className="mx-auto h-[420px] max-w-[650px]" /></div>}>
      <AuthorizationContent />
    </Suspense>
  );
}
