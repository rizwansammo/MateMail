"use client";

import { useState } from "react";
import { AlertTriangle, RefreshCw, ShieldCheck } from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCopyButton,
  PortalNotice,
} from "@/components/workspace/premium-ui";
import { cn } from "@/lib/utils";

export interface DomainOwnership {
  id: string;
  domain: string;
  ownership_status: "pending" | "verified";
  ownership_verified: boolean;
  ownership_verified_at: string | null;
  verification_record_type: string;
  verification_record_name: string;
  verification_record_value: string;
  verification_instructions: string;
  verification_last_checked_at: string | null;
  verification_last_error: string;
}

export interface VerifyResult {
  verified: boolean;
  detail: string;
  domain: DomainOwnership;
}

export async function verifyDomainOwnership(domainId: string): Promise<VerifyResult> {
  const res = await apiRequest(`/api/domains/${domainId}/verify-ownership/`, { method: "POST" });
  const data = await res.json().catch(() => null);
  if (!data || typeof data.verified !== "boolean") {
    throw new Error(data?.detail ?? "The verification check could not be completed. Please try again.");
  }
  return data as VerifyResult;
}

function RecordField({ label, value }: { label: string; value: string }) {
  return (
    <div className="portal-dns-field">
      <span>{label}</span>
      <div className="portal-code-field">
        <code>{value}</code>
        <PortalCopyButton value={value} label={`Copy ${label.toLowerCase()}`} />
      </div>
    </div>
  );
}

export function DomainOwnershipCard({
  domain,
  onVerified,
  onChange,
  showRotate = false,
  className,
}: {
  domain: DomainOwnership;
  onVerified?: (updated: DomainOwnership) => void;
  onChange?: (updated: DomainOwnership) => void;
  showRotate?: boolean;
  className?: string;
}) {
  const [checking, setChecking] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);

  async function runCheck() {
    setChecking(true);
    setMessage("");
    try {
      const result = await verifyDomainOwnership(domain.id);
      setMessage(result.detail);
      setFailed(!result.verified);
      onChange?.(result.domain);
      if (result.verified) onVerified?.(result.domain);
    } catch (caught) {
      setFailed(true);
      setMessage(caught instanceof Error ? caught.message : "The verification check failed.");
    } finally {
      setChecking(false);
    }
  }

  async function rotate() {
    setRotating(true);
    setMessage("");
    try {
      const res = await apiRequest(`/api/domains/${domain.id}/rotate-verification-token/`, {
        method: "POST",
      });
      const data = await res.json().catch(() => null);
      if (res.ok && data?.domain) {
        setFailed(false);
        setMessage(data.detail ?? "A new verification token was issued.");
        onChange?.(data.domain);
      } else {
        setFailed(true);
        setMessage(data?.detail ?? "The token could not be replaced. Please try again.");
      }
    } catch {
      setFailed(true);
      setMessage("The token could not be replaced. Please try again.");
    } finally {
      setRotating(false);
    }
  }

  if (domain.ownership_verified) {
    return (
      <div className={cn("portal-ownership-card verified", className)}>
        <div className="portal-ownership-title">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-[var(--portal-success)]" />
          <div>
            <strong>Ownership verified</strong>
            <p>
              {domain.ownership_verified_at
                ? `Confirmed ${new Date(domain.ownership_verified_at).toLocaleString()}. `
                : "This domain belongs to your workspace. "}
              Leave the verification TXT record in place so periodic re-verification can continue.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const shortHost = domain.verification_record_name.replace("." + domain.domain, "");

  return (
    <div className={cn("portal-ownership-card pending", className)}>
      <div className="portal-ownership-title">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[var(--portal-warning)]" />
        <div>
          <strong>Verify that you own {domain.domain}</strong>
          <p>{domain.verification_instructions}</p>
        </div>
      </div>

      <div className="portal-ownership-record">
        <RecordField label="Type" value={domain.verification_record_type} />
        <RecordField label="Host / name" value={domain.verification_record_name} />
        <RecordField label="Value" value={domain.verification_record_value} />
      </div>

      <p className="mt-2 text-[10px] leading-5 text-[var(--portal-muted)]">
        If your DNS provider automatically appends the zone name, use{" "}
        <code className="font-mono text-[var(--portal-text-strong)]">{shortHost}</code> as the host.
      </p>

      {message && (
        <div className="mt-4">
          <PortalNotice tone={failed ? "danger" : "success"}>{message}</PortalNotice>
        </div>
      )}

      {!message && domain.verification_last_error && (
        <div className="mt-4">
          <PortalNotice tone="warn">
            Last check
            {domain.verification_last_checked_at
              ? ` (${new Date(domain.verification_last_checked_at).toLocaleString()})`
              : ""}
            : {domain.verification_last_error}
          </PortalNotice>
        </div>
      )}

      <div className="portal-detail-actions">
        <PortalButton type="button" onClick={runCheck} disabled={checking}>
          <RefreshCw className={"h-4 w-4 " + (checking ? "animate-spin" : "")} />
          {checking ? "Checking DNS…" : "Verify ownership"}
        </PortalButton>
        {showRotate && (
          <PortalButton type="button" variant="secondary" onClick={rotate} disabled={rotating}>
            {rotating ? "Issuing…" : "Issue a new token"}
          </PortalButton>
        )}
      </div>
    </div>
  );
}

export function OwnershipRequiredNotice({
  domain,
  action,
}: {
  domain: string;
  action: string;
}) {
  return (
    <PortalNotice tone="warn">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>
        {action} for <strong>{domain}</strong> becomes available once ownership is verified.
        Publish the verification TXT record, then run the ownership check.
      </span>
    </PortalNotice>
  );
}
