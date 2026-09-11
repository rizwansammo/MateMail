"use client";

import { useState } from "react";
import { AlertTriangle, Check, Copy, RefreshCw, ShieldCheck } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Domain ownership verification.
 *
 * A domain is not provisioned for mail until the customer proves they control
 * it by publishing a TXT record. The exact Host and Value shown here are what
 * the customer types into their DNS provider, so both must be copyable
 * verbatim — a truncated or reformatted value is the most common reason a
 * verification check fails.
 */

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

/**
 * POST the ownership check. Returns 200 when the record matched and 409 when
 * it did not — both carry the same body, so the caller reads `verified`
 * rather than the status code.
 */
export async function verifyDomainOwnership(domainId: string): Promise<VerifyResult> {
  const res = await apiRequest(`/api/domains/${domainId}/verify-ownership/`, { method: "POST" });
  const data = await res.json().catch(() => null);
  if (!data || typeof data.verified !== "boolean") {
    throw new Error("The verification check could not be completed. Please try again.");
  }
  return data as VerifyResult;
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access is unavailable over plain HTTP and in some browsers.
      // The value stays selectable on screen, so this is not worth an error.
    }
  }
  return (
    <div className="min-w-0">
      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </span>
      <div className="flex items-start gap-2 border border-slate-200 bg-white px-3 py-2">
        <code className="min-w-0 flex-1 break-all font-mono text-xs text-slate-800">{value}</code>
        <button
          type="button"
          onClick={copy}
          title={"Copy " + label.toLowerCase()}
          className="shrink-0 text-slate-400 transition hover:text-slate-700"
        >
          {copied ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
        </button>
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
  /** Called once ownership has just been confirmed. */
  onVerified?: (updated: DomainOwnership) => void;
  /** Called with the refreshed domain after any check, verified or not. */
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
    } catch (err) {
      setFailed(true);
      setMessage(err instanceof Error ? err.message : "The verification check failed.");
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
        setMessage(data.detail);
        onChange?.(data.domain);
      } else {
        setFailed(true);
        setMessage(data?.detail ?? "The token could not be replaced. Please try again.");
      }
    } finally {
      setRotating(false);
    }
  }

  if (domain.ownership_verified) {
    return (
      <div
        className={cn(
          "flex items-start gap-2 border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800",
          className
        )}
      >
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          <strong>Ownership verified.</strong>{" "}
          {domain.ownership_verified_at
            ? "Confirmed " + new Date(domain.ownership_verified_at).toLocaleString() + "."
            : "This domain belongs to your workspace."}{" "}
          Leave the verification TXT record in place.
        </span>
      </div>
    );
  }

  // A DNS provider that appends the zone name wants only the leading label.
  const shortHost = domain.verification_record_name.replace("." + domain.domain, "");

  return (
    <div className={cn("border border-amber-200 bg-amber-50/60 p-5", className)}>
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-900">
            Verify that you own {domain.domain}
          </p>
          <p className="mt-1 text-sm text-slate-600">{domain.verification_instructions}</p>

          <div className="mt-4 grid gap-3 md:grid-cols-[6rem_1fr_1.5fr] md:items-start">
            <div className="min-w-0">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-400">
                Type
              </span>
              <div className="border border-slate-200 bg-white px-3 py-2">
                <code className="font-mono text-xs text-slate-800">
                  {domain.verification_record_type}
                </code>
              </div>
            </div>
            <CopyField label="Host" value={domain.verification_record_name} />
            <CopyField label="Value" value={domain.verification_record_value} />
          </div>

          <p className="mt-2 text-xs text-slate-500">
            Some DNS providers add your domain to the host automatically. If yours does, enter{" "}
            <code className="font-mono text-slate-700">{shortHost}</code> as the host instead.
          </p>

          {message && (
            <p
              className={cn(
                "mt-4 border px-3 py-2 text-sm",
                failed
                  ? "border-rose-200 bg-rose-50 text-rose-700"
                  : "border-emerald-200 bg-emerald-50 text-emerald-800"
              )}
            >
              {message}
            </p>
          )}

          {!message && domain.verification_last_error && (
            <p className="mt-4 border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
              Last check
              {domain.verification_last_checked_at
                ? " (" + new Date(domain.verification_last_checked_at).toLocaleString() + ")"
                : ""}
              : {domain.verification_last_error}
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={runCheck}
              disabled={checking}
              className="inline-flex items-center gap-2 bg-cyan-500 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={cn("h-4 w-4", checking && "animate-spin")} />
              {checking ? "Checking DNS…" : "Verify ownership"}
            </button>
            {showRotate && (
              <button
                type="button"
                onClick={rotate}
                disabled={rotating}
                className="text-xs font-medium text-slate-500 underline-offset-2 transition hover:text-slate-800 hover:underline disabled:opacity-60"
              >
                {rotating ? "Issuing…" : "Issue a new token"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Inline notice for places that must explain why an action is unavailable. */
export function OwnershipRequiredNotice({
  domain,
  action,
}: {
  domain: string;
  action: string;
}) {
  return (
    <div className="flex items-start gap-2 border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>
        {action} for <strong>{domain}</strong> becomes available once ownership is verified.
        Publish the verification TXT record, then run the check.
      </span>
    </div>
  );
}
