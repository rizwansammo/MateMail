"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, LockKeyhole, RefreshCw, ShieldCheck } from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalButton, PortalCard, PortalCopyButton, PortalNotice, PortalSkeleton, PortalStatus,
} from "@/components/workspace/premium-ui";

type RecordInstruction = {
  type: "CNAME" | "TXT";
  host: string;
  value: string | null;
  publish_ready: boolean;
  requirement: string;
};
type SecurityInfo = {
  domain: string;
  ownership_verified: boolean;
  optional: boolean;
  enabled: boolean;
  self_service_available: boolean;
  lifecycle: string;
  certificate_status: string;
  policy_mode: string;
  mx: string;
  max_age_seconds: number;
  policy_url: string | null;
  edge_configured: boolean;
  dns_records: RecordInstruction[];
  dns_verified_at: string | null;
  cert_verified_at: string | null;
  activated_at: string | null;
  last_error: string;
  can_publish_dns: boolean;
  detail: string;
};

function friendly(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function timestamp(value: string | null) {
  return value ? new Date(value).toLocaleString() : "Not verified";
}

function operationError(status: number, data: { detail?: unknown } | null, fallback: string) {
  if (status === 429) return "Too many verification attempts. Try again after the rate limit resets.";
  if (typeof data?.detail === "string") return data.detail;
  return fallback;
}

export function AdvancedTransportSecurity({
  domainId, domainName, ownershipVerified, canAdmin, emailVerified,
}: {
  domainId: string;
  domainName: string;
  ownershipVerified: boolean;
  canAdmin: boolean;
  emailVerified: boolean;
}) {
  const [data, setData] = useState<SecurityInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmAction, setConfirmAction] = useState<"enable" | "disable" | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const response = await apiRequest("/api/domains/" + domainId + "/transport-security/", { signal });
    const result = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(operationError(response.status, result, "Transport security details are unavailable."));
    }
    return result as SecurityInfo;
  }, [domainId]);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "Unable to load transport security.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);

  async function reload() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setData(await refresh());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to refresh.");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(enabled: boolean) {
    if (!data?.self_service_available || !canAdmin || !emailVerified ||
        (enabled && !ownershipVerified)) return;
    setBusy(true);
    setError("");
    setNotice("");
    setConfirmAction(null);
    try {
      const response = await apiRequest("/api/domains/" + domainId + "/transport-security/", {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(operationError(response.status, result, "Unable to update transport security."));
      }
      setData(result as SecurityInfo);
      setNotice(enabled
        ? "Advanced transport security requested. No DNS record is active until its individual readiness check passes."
        : "Optional transport security has been disabled. Standard mail remains unchanged.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to update transport security.");
    } finally {
      setBusy(false);
    }
  }

  async function verifyDNS() {
    if (!data?.enabled || !canAdmin || !emailVerified ||
        !["pending_dns", "error"].includes(data.lifecycle)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiRequest("/api/domains/" + domainId + "/transport-security/verify-dns/", {
        method: "POST",
      });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result?.verified) {
        throw new Error(operationError(response.status, result, "DNS verification did not pass."));
      }
      setNotice(result.detail || "Current ownership, MX and policy CNAME were verified.");
      setData(await refresh());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to verify DNS.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="space-y-3 pt-5"><PortalSkeleton className="h-28 w-full" /><PortalSkeleton className="h-64 w-full" /></div>;

  if (!data) {
    return (
      <div className="pt-5">
        <PortalNotice tone="danger">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span role="alert">{error || "Transport security details could not be loaded."}</span>
        </PortalNotice>
        <div className="mt-3"><PortalButton variant="secondary" onClick={reload} disabled={busy}>Retry</PortalButton></div>
      </div>
    );
  }

  const readyForCname = data.dns_records.some((record) =>
    record.type === "CNAME" && record.publish_ready);
  const canEnable = canAdmin && emailVerified && ownershipVerified &&
    data.self_service_available && !busy;
  const removable = data.enabled && ["disabled", "pending_dns"].includes(data.lifecycle);
  const canVerify = canAdmin && emailVerified && data.enabled &&
    ["pending_dns", "error"].includes(data.lifecycle) && readyForCname && !busy;
  const sslReady = !!data.cert_verified_at && data.certificate_status === "active";
  const status = !data.enabled ? "Not enabled"
    : data.lifecycle === "active" && sslReady ? "Policy active (testing)"
    : data.lifecycle === "ready" && sslReady ? "HTTPS ready (testing)"
    : friendly(data.lifecycle);

  return (
    <div className="space-y-4 pt-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-[var(--portal-border)] text-[var(--portal-text)]">
            <ShieldCheck className="h-5 w-5" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold text-[var(--portal-text-strong)]">Advanced transport security</h2>
            <p className="mt-1 max-w-2xl text-[12px] leading-5 text-[var(--portal-muted)]">
              Optional MTA-STS and TLS reporting for {domainName}. Standard email setup, MX routing,
              SPF, DKIM and DMARC work independently of these features.
            </p>
          </div>
        </div>
        <PortalButton variant="secondary" onClick={reload} disabled={busy}>
          <RefreshCw className={"h-4 w-4 " + (busy ? "animate-spin" : "")} /> Refresh
        </PortalButton>
      </div>

      {!data.self_service_available && (
        <PortalNotice tone="info">
          <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Self-service activation is not released yet. The upcoming reporting and production
            verification phases must finish first. Do not publish any draft DNS records.</span>
        </PortalNotice>
      )}
      {error && <PortalNotice tone="danger"><span role="alert">{error}</span></PortalNotice>}
      {notice && <PortalNotice tone="success"><span role="status">{notice}</span></PortalNotice>}
      {data.last_error && data.enabled && (
        <PortalNotice tone="warn"><span>Provisioning issue: {data.last_error}</span></PortalNotice>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        {[
          { label: "MTA-STS policy", value: status, sub: "Mode: testing only" },
          { label: "HTTPS certificate", value: sslReady ? "Verified" : friendly(data.certificate_status), sub: timestamp(data.cert_verified_at) },
          { label: "DNS ownership & gateway", value: data.dns_verified_at ? "Verified" : "Not verified", sub: timestamp(data.dns_verified_at) },
        ].map((item) => (
          <div key={item.label} className="min-w-0 rounded-md border border-[var(--portal-border)] bg-[var(--portal-surface)] p-4">
            <p className="text-[11px] font-medium text-[var(--portal-muted)]">{item.label}</p>
            <div className="mt-2"><PortalStatus value={item.value} /></div>
            <p className="mt-2 break-words text-[11px] text-[var(--portal-muted)]">{item.sub}</p>
          </div>
        ))}
      </div>

      <PortalCard title="Policy controls" subtitle="Only workspace owners and administrators may change this setting.">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <p className="text-[12px] font-semibold text-[var(--portal-text-strong)]">
              {data.enabled ? "Advanced security requested" : "Optional protection is off"}
            </p>
            <p className="text-[11px] leading-5 text-[var(--portal-muted)]">
              Enabling starts a controlled setup, not immediate enforcement. The policy is
              fixed to testing mode. HTTPS certificates and reporting require independent checks.
            </p>
          </div>
          {canAdmin && (data.enabled ? removable : true) && (
            <PortalButton
              type="button"
              variant="secondary"
              disabled={!canEnable}
              onClick={() => setConfirmAction(data.enabled ? "disable" : "enable")}
            >{data.enabled ? "Disable request" : "Enable advanced security"}</PortalButton>
          )}
        </div>
        {!ownershipVerified && <p className="mt-3 text-xs text-[var(--portal-warning)]">Verify domain ownership before enabling.</p>}
        {!emailVerified && <p className="mt-3 text-xs text-[var(--portal-warning)]">Verify your account email before making changes.</p>}
        {data.enabled && !removable && (
          <p className="mt-3 text-xs text-[var(--portal-muted)]">
            Managed removal is required once edge provisioning starts. Contact platform support instead of deleting cached policy hosting.
          </p>
        )}
        {confirmAction && (
          <div className="mt-4 rounded-md border border-[var(--portal-border)] p-3">
            <p className="text-xs leading-5 text-[var(--portal-text)]">
              {confirmAction === "enable"
                ? "Request optional testing-mode MTA-STS setup? You must verify DNS manually, and only individually approved records can be published."
                : "Cancel this pending security request? This is allowed only before edge provisioning has started."}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <PortalButton variant="secondary" disabled={busy} onClick={() => setConfirmAction(null)}>Cancel</PortalButton>
              <PortalButton disabled={!canEnable} onClick={() => toggle(confirmAction === "enable")}>
                {busy ? "Saving…" : "Confirm"}
              </PortalButton>
            </div>
          </div>
        )}
      </PortalCard>

      {data.enabled && (
        <>
          <PortalCard title="Security DNS records" subtitle="Each record is unlocked separately, only after the backend approves publication.">
            <div className="mb-4"><PortalNotice tone={data.can_publish_dns ? "success" : "warn"}>
              <span>{data.can_publish_dns
                ? "All currently listed records have passed their publication gates."
                : "Not all records are publish-ready. Only copy and publish a record explicitly marked Ready to publish."}</span>
            </PortalNotice></div>
            <div className="space-y-3">
              {data.dns_records.map((record) => (
                <article key={record.type + record.host} className="rounded-md border border-[var(--portal-border)] p-3 sm:p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="shrink-0 whitespace-nowrap rounded border border-[var(--portal-border)] px-2 py-1 text-[10px] font-semibold tracking-wide text-[var(--portal-text)]">{record.type}</span>
                    <span className="min-w-0 flex-1 text-[12px] font-semibold text-[var(--portal-text)]">
                      {record.host.startsWith("mta-sts.") ? "MTA-STS policy gateway"
                        : record.host.startsWith("_mta-sts.") ? "MTA-STS announcement" : "TLS failure reporting"}
                    </span>
                    <PortalStatus value={record.publish_ready ? "Ready to publish" : "Not ready"} />
                  </div>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <div className="min-w-0">
                      <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--portal-muted)]">Full host / name</span>
                      <div className="mt-1 flex min-w-0 items-center gap-1 border border-[var(--portal-border)] bg-[var(--portal-bg)] px-2 py-2">
                        <code className="min-w-0 flex-1 break-all text-[11px] text-[var(--portal-text)]">{record.host}</code>
                        {record.publish_ready && <PortalCopyButton label={"Copy " + record.type + " host"} value={record.host} />}
                      </div>
                    </div>
                    <div className="min-w-0">
                      <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--portal-muted)]">Target / value</span>
                      <div className="mt-1 flex min-w-0 items-center gap-1 border border-[var(--portal-border)] bg-[var(--portal-bg)] px-2 py-2">
                        <code className="min-w-0 flex-1 break-all text-[11px] text-[var(--portal-text)]">
                          {record.publish_ready && record.value ? record.value : "Withheld until verified"}
                        </code>
                        {record.publish_ready && record.value && <PortalCopyButton label={"Copy " + record.type + " value"} value={record.value} />}
                      </div>
                    </div>
                  </div>
                  <p className="mt-2 text-[11px] leading-5 text-[var(--portal-muted)]">
                    {record.publish_ready ? record.requirement : "Blocked: " + record.requirement}
                  </p>
                </article>
              ))}
            </div>
            <p className="mt-3 text-[11px] leading-5 text-[var(--portal-muted)]">
              DNS dashboards differ: some accept full hostnames, others require a name relative to
              the exact hosted DNS zone. Do not assume the mail domain is the DNS zone apex.
              Keep standard mail setup records unchanged.
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-[var(--portal-border)] pt-4">
              <PortalButton variant="secondary" onClick={verifyDNS} disabled={!canVerify}>
                <RefreshCw className={"h-4 w-4 " + (busy ? "animate-spin" : "")} />
                {busy ? "Checking…" : "Verify policy DNS"}
              </PortalButton>
              <p className="text-xs text-[var(--portal-muted)]">
                Verification checks current ownership TXT, exact CNAME and all MX records.
              </p>
            </div>
          </PortalCard>

          <PortalCard title="MTA-STS policy" subtitle="Informational preview, not an instruction to enforce transport restrictions.">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="portal-fact"><span>Policy mode</span><strong>Testing (fixed)</strong></div>
              <div className="portal-fact"><span>Expected MX</span><code className="break-all">{data.mx}</code></div>
              <div className="portal-fact"><span>Cache duration</span><strong>{data.max_age_seconds} seconds</strong></div>
              <div className="portal-fact"><span>TLS reporting</span><strong>{data.dns_records.find((record) => record.host.startsWith("_smtp._tls."))?.publish_ready ? "Reporting DNS ready" : "Reporting setup pending"}</strong></div>
            </div>
            {data.policy_url && (
              <p className="mt-3 break-all text-[11px] text-[var(--portal-muted)]">
                Policy URL: <code>{data.policy_url}</code>
                {!sslReady && " (not yet HTTPS verified)"}
              </p>
            )}
            {sslReady && <p className="mt-2 flex items-center gap-2 text-xs text-[var(--portal-muted)]">
              <CheckCircle2 className="h-4 w-4 text-[var(--portal-success)]" />
              The policy certificate has been verified by the backend.
            </p>}
          </PortalCard>
        </>
      )}
    </div>
  );
}
