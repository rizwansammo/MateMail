"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { PortalButton, PortalCard, PortalNotice, PortalStatus } from "@/components/workspace/premium-ui";

type Policy = "off" | "encourage" | "require";
interface PolicyStatus {
  policy: Policy;
  effective_policy: Policy;
  configurable: boolean;
  require_ready: boolean;
}
const POLICY_LABELS: Record<Policy, string> = {
  off: "No organization requirement",
  encourage: "Enabled (optional)",
  require: "Enforced (required)",
};

export function OrganizationTwoFactorPolicy({
  tenantId,
  canEdit,
}: { tenantId: string; canEdit: boolean }) {
  const [status, setStatus] = useState<PolicyStatus | null>(null);
  const [selected, setSelected] = useState<Policy>("off");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiRequest(`/api/workspaces/${tenantId}/security/2fa/`);
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Security policy is unavailable.");
      setStatus(data as PolicyStatus);
      setSelected(data.policy as Policy);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Security policy could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 0);
    return () => window.clearTimeout(timer);
  }, [reload]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!status || !canEdit || !status.configurable || (selected === "require" && !status.require_ready)) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await apiRequest(`/api/workspaces/${tenantId}/security/2fa/`, {
        method: "PATCH",
        body: JSON.stringify({ policy: selected, current_password: password }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Security policy could not be saved.");
      setStatus((previous) => previous ? { ...previous, policy: data.policy, effective_policy: data.effective_policy } : previous);
      setPassword("");
      setMessage("Organization policy saved.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Security policy could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <PortalCard className="mt-5" title="Organization two-factor policy" subtitle="Controls the extra sign-in step for organization members. Hub accounts and PostBox mailboxes remain separate identities.">
      {loading ? <p className="portal-field-hint">Loading organization security…</p> : (
        <>
          {status && (
            <div className="portal-security-action">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
              <div>
                <strong>Current policy: {POLICY_LABELS[status.policy]}</strong>
                <span>Effective enforcement: {POLICY_LABELS[status.effective_policy]}</span>
              </div>
              <PortalStatus value={status.configurable ? "Available" : "Not activated"} />
            </div>
          )}
          {!status?.configurable && <PortalNotice tone="info">Organization 2FA policy changes will become available after the controlled security release. Current mail access remains unchanged.</PortalNotice>}
          {status && !status.require_ready && <div className="mt-3"><PortalNotice tone="info">Require stays locked until server-side enforcement and recovery are approved for production. No UI setting can override this restriction.</PortalNotice></div>}
          {status && (
            <form onSubmit={save} className="mt-4">
              <div className="portal-field">
                <label htmlFor="organization-2fa-policy">Member sign-in policy</label>
                <select id="organization-2fa-policy" value={selected} onChange={(event) => setSelected(event.target.value as Policy)} disabled={!canEdit || !status.configurable || saving}>
                  <option value="off">Off — no organization-wide requirement; individual 2FA remains available</option>
                  <option value="encourage">Enable — optional 2FA for members, recommended</option>
                  <option value="require" disabled={!status.require_ready}>Enforce — require 2FA for organization access</option>
                </select>
              </div>
              {selected === "require" && status.require_ready && <div className="mt-3"><PortalNotice tone="info">Enforcing 2FA requires immediate authenticator enrollment for members. Existing external SMTP/IMAP sign-ins will be disconnected; users must configure dedicated app passwords before reconnecting their mail clients. Notify your team before saving.</PortalNotice></div>}
              {canEdit && status.configurable && selected !== status.policy && (
                <>
                  <div className="portal-field mt-4">
                    <label htmlFor="organization-2fa-password">Current Hub account password</label>
                    <input id="organization-2fa-password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
                  </div>
                  <div className="portal-detail-actions">
                    <PortalButton type="submit" disabled={saving || !password || (selected === "require" && !status.require_ready)}>{saving ? "Saving…" : "Save organization policy"}</PortalButton>
                  </div>
                </>
              )}
              {!canEdit && <p className="portal-field-hint">Only organization owners and administrators may change this policy.</p>}
            </form>
          )}
          {message && <div className="mt-3"><PortalNotice tone="success">{message}</PortalNotice></div>}
        </>
      )}
      {error && <div className="mt-3"><PortalNotice tone="danger">{error}</PortalNotice></div>}
    </PortalCard>
  );
}
