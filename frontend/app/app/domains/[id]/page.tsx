"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock3,
  Globe2,
  RefreshCw,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import { AdvancedTransportSecurity } from "@/components/domains/advanced-transport-security";
import {
  DomainOwnership,
  DomainOwnershipCard,
} from "@/components/domain-ownership";
import {
  PortalButton,
  PortalCard,
  PortalCopyButton,
  PortalNotice,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface Domain extends DomainOwnership {
  status: string;
  dns_health_score: number;
  dkim_selector: string;
  dkim_public_key: string;
  mail_service_ready: boolean;
  mail_service_message: string;
  added_at: string;
  verified_at: string | null;
}

interface DNSRecord {
  id: string;
  record_type: string;
  host: string;
  expected_value: string;
  detected_value: string;
  status: "verified" | "pending" | "missing" | "failed";
  last_checked: string | null;
  is_scored: boolean;
}

function pretty(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function shortDnsHost(host: string, domain: string) {
  const cleanHost = host.trim().replace(/\.$/, "");
  const cleanDomain = domain.trim().replace(/\.$/, "");
  if (cleanHost === cleanDomain) return "@";
  const suffix = `.${cleanDomain}`;
  return cleanHost.endsWith(suffix) ? cleanHost.slice(0, -suffix.length) : cleanHost;
}

function stripTrailingDot(value: string) {
  return value.trim().replace(/\.$/, "");
}

function recordLabel(record: DNSRecord) {
  if (record.record_type === "SRV") return "Automatic mail app setup";
  if (record.record_type === "MX") return "Mail server";
  if (record.host.startsWith("_dmarc")) return "Domain protection (DMARC)";
  if (record.host.includes("._domainkey")) return "Email authentication (DKIM)";
  if (record.expected_value.startsWith("v=spf1")) return "Sender authorization (SPF)";
  return record.record_type;
}

function recordHelp(record: DNSRecord) {
  if (record.record_type === "SRV") {
    return "Optional. Helps Outlook and other mail apps configure the mailbox automatically. Email still sends and receives without this record.";
  }
  if (record.record_type === "MX") return "Routes incoming email for this domain to MateMail. A working legacy .online MX can remain during the transition; replace it only after the new mail host is verified.";
  if (record.host.startsWith("_dmarc")) return "Publishes the policy receiving servers use for email authentication results.";
  if (record.host.includes("._domainkey")) {
    return record.expected_value.includes("<pending>")
      ? "MateMail is preparing the DKIM value. You do not need to add this record until the value is ready."
      : "Publishes the public key used to verify signed outgoing email.";
  }
  if (record.expected_value.startsWith("v=spf1")) return "Authorizes MateMail to send email for this domain. Edit the existing SPF TXT record; never publish two separate v=spf1 records.";
  return "";
}

type DisplayField = {
  label: string;
  value: string;
  copyLabel: string;
  copy?: boolean;
};

function displayFields(record: DNSRecord, domain: string): DisplayField[] {
  const host = shortDnsHost(record.host, domain);

  if (record.record_type === "MX") {
    const [priority = "10", ...serverParts] = record.expected_value.trim().split(/\s+/);
    return [
      { label: "Host / Name", value: host, copyLabel: "Copy MX host" },
      { label: "Mail server / Value", value: stripTrailingDot(serverParts.join(" ")), copyLabel: "Copy mail server" },
      { label: "Priority", value: priority, copyLabel: "Copy MX priority" },
    ];
  }

  if (record.record_type === "SRV") {
    const [priority = "0", weight = "0", port = "443", ...targetParts] =
      record.expected_value.trim().split(/\s+/);
    const [service = "_autodiscover", protocol = "_tcp"] = host.split(".");
    return [
      { label: "Service", value: service, copyLabel: "Copy SRV service" },
      { label: "Protocol", value: protocol, copyLabel: "Copy SRV protocol" },
      { label: "Priority", value: priority, copyLabel: "Copy SRV priority" },
      { label: "Weight", value: weight, copyLabel: "Copy SRV weight" },
      { label: "Port", value: port, copyLabel: "Copy SRV port" },
      { label: "Target / Value", value: stripTrailingDot(targetParts.join(" ")), copyLabel: "Copy SRV target" },
    ];
  }

  const dkimPending =
    record.host.includes("._domainkey") && record.expected_value.includes("<pending>");

  return [
    { label: "Host / Name", value: host, copyLabel: `Copy ${recordLabel(record)} host` },
    {
      label: "Value",
      value: dkimPending ? "MateMail is generating this value" : record.expected_value,
      copyLabel: `Copy ${recordLabel(record)} value`,
      copy: !dkimPending,
    },
  ];
}

function dnsMigrationState(record: DNSRecord): string | null {
  const canonical = record.expected_value.toLowerCase();
  const detected = record.detected_value.toLowerCase();
  const relevant = record.record_type === "MX" ||
    record.record_type === "SRV" ||
    (record.record_type === "TXT" && canonical.startsWith("v=spf1"));
  if (!relevant || !canonical.includes(".matemail.pro")) return null;
  if (detected.includes(".matemail.online") && !detected.includes(".matemail.pro") &&
      record.status === "verified") return "Old detected — still working. Migrate when ready.";
  if (record.status === "verified") return "New verified";
  if (record.status === "failed" || record.status === "missing") return "Missing / mismatched";
  return "Pending DNS verification";
}

function displayStatus(record: DNSRecord) {
  if (record.status === "verified") return "Verified";
  if (record.status === "failed") return "Failed";
  return "Pending";
}

function formatWait(totalSeconds: number) {
  const seconds = Math.max(0, Math.ceil(totalSeconds));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function RecordRow({ record, domain }: { record: DNSRecord; domain: string }) {
  const icon =
    record.status === "verified" ? <CheckCircle2 className="h-4 w-4 text-[var(--portal-success)]" /> :
    record.status === "failed" ? <XCircle className="h-4 w-4 text-[var(--portal-danger)]" /> :
    <AlertCircle className="h-4 w-4 text-[var(--portal-warning)]" />;

  const fields = displayFields(record, domain);
  const shortHost = shortDnsHost(record.host, domain);

  return (
    <article className="portal-dns-record">
      <div className="portal-dns-record-head">
        {icon}
        <span className="portal-record-type">{record.record_type}</span>
        <div className="portal-dns-record-title">
          <h3>{recordLabel(record)}</h3>
          <p>{recordHelp(record)}</p>
        </div>
        <span className="portal-dns-requirement" data-optional={!record.is_scored}>
          {record.is_scored ? "Required" : "Optional"}
        </span>
        <PortalStatus value={displayStatus(record)} />
      </div>

      {dnsMigrationState(record) && (
        <p className="portal-detected" role="status">
          DNS migration: {dnsMigrationState(record)}
        </p>
      )}

      <div className="portal-dns-fields">
        {fields.map((field) => (
          <div className="portal-dns-field" key={field.label}>
            <span>{field.label.toUpperCase()}</span>
            <div className="portal-code-field">
              <code>{field.value}</code>
              {field.copy !== false && (
                <PortalCopyButton value={field.value} label={field.copyLabel} />
              )}
            </div>
          </div>
        ))}
      </div>

      {fields.some((field) => field.value === "@") && (
        <p className="portal-dns-provider-note">
          <code>@</code> means the root domain. Some DNS providers display the root as your
          full domain name or let you leave the Host field blank.
        </p>
      )}

      {record.record_type === "SRV" && (
        <p className="portal-dns-provider-note">
          If your DNS provider has one <strong>Name / Host</strong> field instead of separate
          Service and Protocol fields, use <code>{shortHost}</code>.
        </p>
      )}

      <details className="portal-dns-advanced">
        <summary>Advanced DNS details</summary>
        <div>
          <span>Full hostname</span>
          <code>{record.host}</code>
        </div>
        <div>
          <span>Raw record value</span>
          <code>{record.expected_value}</code>
        </div>
        {record.detected_value && (
          <div>
            <span>Detected value</span>
            <code>{record.detected_value}</code>
          </div>
        )}
      </details>
      {record.last_checked && (
        <p className="portal-detected">
          Last checked {new Date(record.last_checked).toLocaleString()}
        </p>
      )}
    </article>
  );
}

export default function DomainDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { tenant, user } = useAuth();
  const [domain, setDomain] = useState<Domain | null>(null);
  const [myRole, setMyRole] = useState("");
  const [records, setRecords] = useState<DNSRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"dns" | "details" | "transport">("dns");
  const [checking, setChecking] = useState(false);
  const [checkMessage, setCheckMessage] = useState(
    "DNS results stay visible while checks run and refresh automatically when new results arrive."
  );
  const [retryUntil, setRetryUntil] = useState<number | null>(null);
  const [retryClock, setRetryClock] = useState(() => Date.now());
  const [actionMessage, setActionMessage] = useState("");
  const [actionFailed, setActionFailed] = useState(false);
  const [provisioning, setProvisioning] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const pollTimers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const role = myRole || tenant?.role || "";
  const canAdmin = role === "owner" || role === "admin";
  const canSupportAction = canAdmin || role === "support";

  const fetchData = useCallback(async () => {
    const [domainResponse, recordsResponse] = await Promise.all([
      apiRequest(`/api/domains/${params.id}/`),
      apiRequest(`/api/domains/${params.id}/records/`),
    ]);
    if (domainResponse.ok) {
      setDomain(await domainResponse.json());
      window.dispatchEvent(new Event("matemail:workspace-onboarding-updated"));
    }
    if (recordsResponse.ok) setRecords(await recordsResponse.json());
  }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await Promise.all([
          fetchData(),
          tenant?.id
            ? apiRequest(`/api/workspaces/${tenant.id}/stats/`)
                .then(async (res) => res.ok ? res.json() : null)
                .then((data) => {
                  if (!cancelled && data?.my_role) setMyRole(data.my_role);
                })
            : Promise.resolve(),
        ]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchData, tenant?.id]);

  useEffect(() => {
    const timers = pollTimers.current;
    return () => {
      timers.forEach(clearTimeout);
      timers.length = 0;
    };
  }, []);

  const retrySeconds = retryUntil
    ? Math.max(0, Math.ceil((retryUntil - retryClock) / 1000))
    : 0;

  useEffect(() => {
    if (!retryUntil) return;

    const timer = window.setInterval(() => {
      const now = Date.now();
      setRetryClock(now);
      if (now >= retryUntil) window.clearInterval(timer);
    }, 1000);

    return () => window.clearInterval(timer);
  }, [retryUntil]);

  async function checkDNS() {
    if (!canSupportAction || retrySeconds > 0) return;
    setChecking(true);
    setCheckMessage("Checking DNS now. Your current results stay visible while this runs.");
    try {
      const response = await apiRequest(`/api/domains/${params.id}/check/`, { method: "POST" });
      const data = await response.json().catch(() => null);

      if (response.status === 202) {
        setCheckMessage("DNS check started. New results will appear here automatically.");
        pollTimers.current.forEach(clearTimeout);
        pollTimers.current = [5000, 15000, 30000].map((delay) =>
          setTimeout(() => {
            fetchData().catch(() => {});
          }, delay)
        );
        return;
      }

      if (response.status === 429) {
        const retryHeader = response.headers.get("Retry-After");
        const headerSeconds = retryHeader ? Number.parseInt(retryHeader, 10) : Number.NaN;
        const detail = typeof data?.detail === "string" ? data.detail : "";
        const detailMatch = detail.match(/(\d+)\s+seconds?/i);
        const detailSeconds = detailMatch ? Number.parseInt(detailMatch[1], 10) : Number.NaN;
        const waitSeconds = Number.isFinite(headerSeconds)
          ? headerSeconds
          : Number.isFinite(detailSeconds)
            ? detailSeconds
            : 60;

        if (waitSeconds > 0) {
          const now = Date.now();
          setRetryClock(now);
          setRetryUntil(now + waitSeconds * 1000);
        }
        setCheckMessage(
          "Manual DNS checks are temporarily paused. Current results stay visible and automatic refresh continues."
        );
        return;
      }

      setCheckMessage(
        data?.detail
          ? `DNS check could not be started: ${data.detail}`
          : "DNS check could not be started. Your current results are unchanged."
      );
    } catch {
      setCheckMessage("DNS check could not be started. Your current results are unchanged.");
    } finally {
      setChecking(false);
    }
  }

  async function retryProvisioning() {
    if (!canAdmin) return;
    setProvisioning(true);
    setActionMessage("");
    setActionFailed(false);
    try {
      const response = await apiRequest(`/api/domains/${params.id}/provision/`, { method: "POST" });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setActionFailed(true);
        setActionMessage(data?.detail ?? "Provisioning could not be started.");
      } else {
        setActionMessage("Mail service provisioning was queued.");
        window.setTimeout(() => fetchData().catch(() => {}), 3500);
      }
    } catch {
      setActionFailed(true);
      setActionMessage("Provisioning could not be started.");
    } finally {
      setProvisioning(false);
    }
  }

  async function rotateOwnershipToken() {
    if (!canAdmin || !domain) return;
    setRotating(true);
    setActionMessage("");
    setActionFailed(false);
    try {
      const response = await apiRequest(`/api/domains/${params.id}/rotate-verification-token/`, {
        method: "POST",
      });
      const data = await response.json().catch(() => null);
      if (response.ok && data?.domain) {
        setDomain((current) => current ? { ...current, ...data.domain } : data.domain);
        setActionMessage(data.detail ?? "A new ownership verification token was issued.");
        setConfirmRotate(false);
        setTab("dns");
      } else {
        setActionFailed(true);
        setActionMessage(data?.detail ?? "A new verification token could not be issued.");
      }
    } catch {
      setActionFailed(true);
      setActionMessage("A new verification token could not be issued.");
    } finally {
      setRotating(false);
    }
  }

  async function deleteDomain() {
    if (!canAdmin) return;
    setDeleting(true);
    setActionMessage("");
    setActionFailed(false);
    try {
      const response = await apiRequest(`/api/domains/${params.id}/`, { method: "DELETE" });
      if (response.ok || response.status === 204) {
        router.push("/app/domains");
        return;
      }
      const data = await response.json().catch(() => null);
      setActionFailed(true);
      setActionMessage(data?.detail ?? "This domain could not be removed.");
    } catch {
      setActionFailed(true);
      setActionMessage("This domain could not be removed.");
    } finally {
      setDeleting(false);
    }
  }

  if (loading) {
    return (
      <div className="portal-page">
        <PortalSkeleton className="mb-5 h-20 w-full" />
        <PortalSkeleton className="h-[480px] w-full" />
      </div>
    );
  }

  if (!domain) {
    return (
      <div className="portal-page">
        <PortalCard>
          <div className="portal-empty">
            <div>
              <Globe2 className="mx-auto h-7 w-7 text-[var(--portal-faint)]" />
              <h3>Domain not found</h3>
              <p>This domain is not available in the current workspace.</p>
              <div className="mt-4">
                <Link href="/app/domains" className="portal-button secondary">Back to domains</Link>
              </div>
            </div>
          </div>
        </PortalCard>
      </div>
    );
  }

  const scoredRecords = records.filter((record) => record.is_scored);
  const discoveryRecords = records.filter((record) => !record.is_scored);
  const verifiedScored = scoredRecords.filter((record) => record.status === "verified").length;

  return (
    <div className="portal-page">
      <div className="portal-domain-detail-head">
        <div>
          <Link href="/app/domains" className="portal-back-link">
            <ArrowLeft className="h-3.5 w-3.5" />
            Domains
          </Link>
          <h1 className="portal-domain-title">{domain.domain}</h1>
          <div className="portal-domain-subline">
            <PortalStatus value={pretty(domain.status)} />
            <span className="text-[11px] text-[var(--portal-muted)]">
              DNS health {domain.dns_health_score}/100
            </span>
            <span className="text-[11px] text-[var(--portal-muted)]">
              {domain.ownership_verified ? "Ownership verified" : "Ownership pending"}
            </span>
          </div>
        </div>

        {canSupportAction && (
          <PortalButton
            type="button"
            variant="secondary"
            onClick={checkDNS}
            disabled={checking || retrySeconds > 0}
          >
            <RefreshCw className={"h-4 w-4 " + (checking ? "animate-spin" : "")} />
            {checking
              ? "Checking…"
              : retrySeconds > 0
                ? `Re-check in ${formatWait(retrySeconds)}`
                : "Re-check DNS"}
          </PortalButton>
        )}
      </div>

      <div className="portal-dns-check-slot">
        <PortalNotice tone={retrySeconds > 0 ? "warn" : "info"}>
          <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {retrySeconds > 0 ? (
              <>
                Manual DNS checks are paused. Try again in <strong>{formatWait(retrySeconds)}</strong>.
                {" "}Current results stay visible and automatic refresh continues.
              </>
            ) : (
              checkMessage
            )}
          </span>
        </PortalNotice>
      </div>

      {actionMessage && (
        <div className="mb-4">
          <PortalNotice tone={actionFailed ? "danger" : "success"}>{actionMessage}</PortalNotice>
        </div>
      )}

      <DomainOwnershipCard
        domain={domain}
        showRotate={canAdmin}
        onChange={(updated) => setDomain((current) => current ? { ...current, ...updated } : current)}
        onVerified={() => {
          fetchData().catch(() => {});
        }}
      />

      <PortalCard bodyClassName="!pt-0">
        <div className="portal-domain-tabs">
          <button
            type="button"
            className="portal-domain-tab"
            data-active={tab === "dns"}
            onClick={() => setTab("dns")}
          >
            DNS & verification
          </button>
          <button
            type="button"
            className="portal-domain-tab"
            data-active={tab === "details"}
            onClick={() => setTab("details")}
          >
            Domain details
          </button>
          <button
            type="button"
            className="portal-domain-tab"
            data-active={tab === "transport"}
            onClick={() => setTab("transport")}
          >
            Advanced security
          </button>
          {canAdmin && (
            <Link href={`/app/domains/${params.id}/dmarc`} className="portal-domain-tab">
              DMARC reports
            </Link>
          )}
        </div>

        {tab === "dns" ? (
          <div className="pt-5">
            <div className="portal-readiness">
              <div>
                <strong>
                  {domain.dns_health_score === 100
                    ? "Your mail DNS is fully healthy"
                    : domain.ownership_verified
                      ? "Finish configuring your mail DNS"
                      : "Verify ownership before mail setup can complete"}
                </strong>
                <p>
                  {scoredRecords.length
                    ? `${verifiedScored} of ${scoredRecords.length} required DNS records are connected.`
                    : "Run a DNS check after adding the records below."}
                </p>
              </div>
              <div className="portal-readiness-score">
                {domain.dns_health_score}<small>/100</small>
              </div>
            </div>

            {!user?.email_verified && (
              <div className="mb-4">
                <PortalNotice tone="warn">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>Verify your account email before provisioning actions can run.</span>
                </PortalNotice>
              </div>
            )}

            {records.length ? (
              <>
                <p className="portal-dns-setup-tip">
                  Copy each field exactly as shown. For TTL, keep your DNS provider’s default or Auto value.
                </p>
                <div className="portal-dns-list">
                  {scoredRecords.map((record) => (
                    <RecordRow key={record.id} record={record} domain={domain.domain} />
                  ))}
                </div>

                {discoveryRecords.length > 0 && (
                  <div className="mt-6">
                    <div className="mb-3">
                      <h2 className="text-[13px] font-semibold text-[var(--portal-text-strong)]">Optional mail app setup</h2>
                      <p className="mt-1 text-[10px] leading-5 text-[var(--portal-muted)]">
                        Helps Outlook and some mail apps find the right settings automatically. Email works normally without this record.
                      </p>
                    </div>
                    <div className="portal-dns-list">
                      {discoveryRecords.map((record) => (
                        <RecordRow key={record.id} record={record} domain={domain.domain} />
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="portal-empty">
                <div>
                  <Globe2 className="mx-auto h-7 w-7 text-[var(--portal-faint)]" />
                  <h3>No DNS results yet</h3>
                  <p>Run a DNS check after publishing the required records at your registrar.</p>
                  {canSupportAction && (
                    <div className="mt-4">
                      <PortalButton type="button" onClick={checkDNS} disabled={checking || retrySeconds > 0}>
                        <RefreshCw className={"h-4 w-4 " + (checking ? "animate-spin" : "")} />
                        {checking
                          ? "Checking…"
                          : retrySeconds > 0
                            ? `Check again in ${formatWait(retrySeconds)}`
                            : "Run DNS check"}
                      </PortalButton>
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="mt-5">
              <PortalNotice tone="info">
                <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
                <span>DNS changes can take time to propagate. MateMail’s check is asynchronous and displays only real resolver results returned by the backend.</span>
              </PortalNotice>
            </div>

            <div className="mt-5">
              <PortalCard
                title="Mail service"
                subtitle="Provisioning status for this domain."
                bodyClassName="!pt-4"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    {domain.mail_service_ready ? (
                      <CheckCircle2 className="h-4 w-4 text-[var(--portal-success)]" />
                    ) : (
                      <AlertCircle className="h-4 w-4 text-[var(--portal-warning)]" />
                    )}
                    <span className="text-[12px] font-semibold text-[var(--portal-text-strong)]">
                      {domain.mail_service_ready
                        ? "Mail service active"
                        : domain.ownership_verified
                          ? "Mail service setup not yet ready"
                          : "Mail service starts after ownership verification"}
                    </span>
                  </div>
                  {canAdmin && domain.ownership_verified && !domain.mail_service_ready && (
                    <PortalButton
                      type="button"
                      variant="secondary"
                      onClick={retryProvisioning}
                      disabled={provisioning || !user?.email_verified}
                    >
                      <RefreshCw className={"h-4 w-4 " + (provisioning ? "animate-spin" : "")} />
                      {provisioning ? "Queueing…" : "Retry provisioning"}
                    </PortalButton>
                  )}
                </div>
                {domain.mail_service_message && (
                  <div className="mt-3">
                    <PortalNotice tone="danger">{domain.mail_service_message}</PortalNotice>
                  </div>
                )}
              </PortalCard>
            </div>
          </div>
        ) : tab === "transport" ? (
          <AdvancedTransportSecurity
            key={params.id}
            domainId={params.id}
            domainName={domain.domain}
            ownershipVerified={domain.ownership_verified}
            canAdmin={canAdmin}
            emailVerified={Boolean(user?.email_verified)}
          />
        ) : (
          <div className="pt-5">
            <div className="portal-detail-grid">
              <div className="portal-fact">
                <span>Domain</span>
                <strong>{domain.domain}</strong>
              </div>
              <div className="portal-fact">
                <span>Ownership</span>
                <strong>{domain.ownership_verified ? "Verified" : "Pending"}</strong>
              </div>
              <div className="portal-fact">
                <span>DNS status</span>
                <strong>{pretty(domain.status)} · {domain.dns_health_score}/100</strong>
              </div>
              <div className="portal-fact">
                <span>Mail service</span>
                <strong>{domain.mail_service_ready ? "Ready" : "Not ready"}</strong>
              </div>
              <div className="portal-fact">
                <span>DKIM selector</span>
                <code>{domain.dkim_selector || "Not generated yet"}</code>
              </div>
              <div className="portal-fact">
                <span>Added</span>
                <strong>{new Date(domain.added_at).toLocaleString()}</strong>
              </div>
              <div className="portal-fact">
                <span>Ownership verified</span>
                <strong>{domain.ownership_verified_at ? new Date(domain.ownership_verified_at).toLocaleString() : "Not yet"}</strong>
              </div>
              <div className="portal-fact">
                <span>Mail DNS first verified</span>
                <strong>{domain.verified_at ? new Date(domain.verified_at).toLocaleString() : "Not yet"}</strong>
              </div>
            </div>

            {canAdmin && (
              <div className="mt-5">
                <PortalCard
                  title="Domain actions"
                  subtitle="Administrative operations backed by MateMail’s existing domain APIs."
                >
                  <div className="portal-detail-actions !mt-0">
                    {domain.ownership_verified && !domain.mail_service_ready && (
                      <PortalButton
                        type="button"
                        variant="secondary"
                        onClick={retryProvisioning}
                        disabled={provisioning || !user?.email_verified}
                      >
                        <RefreshCw className={"h-4 w-4 " + (provisioning ? "animate-spin" : "")} />
                        Retry provisioning
                      </PortalButton>
                    )}
                    <PortalButton
                      type="button"
                      variant="secondary"
                      onClick={() => setConfirmRotate(true)}
                    >
                      Issue new ownership token
                    </PortalButton>
                  </div>

                  {confirmRotate && (
                    <div className="mt-4">
                      <PortalNotice tone="warn">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                        <div className="flex-1">
                          <strong>This invalidates the current ownership TXT token.</strong>{" "}
                          You will need to publish the new value and verify ownership again.
                        </div>
                        <div className="flex gap-2">
                          <PortalButton type="button" variant="secondary" onClick={() => setConfirmRotate(false)}>Cancel</PortalButton>
                          <PortalButton type="button" onClick={rotateOwnershipToken} disabled={rotating}>
                            {rotating ? "Issuing…" : "Issue token"}
                          </PortalButton>
                        </div>
                      </PortalNotice>
                    </div>
                  )}
                </PortalCard>
              </div>
            )}

            {canAdmin && (
              <div className="portal-danger-zone">
                <strong>Remove domain</strong>
                <p>MateMail queues mail-engine cleanup before deleting the local domain record. If cleanup cannot be safely queued, the backend refuses the deletion.</p>
                {!confirmDelete ? (
                  <PortalButton type="button" variant="danger" onClick={() => setConfirmDelete(true)}>
                    <Trash2 className="h-4 w-4" />
                    Delete domain
                  </PortalButton>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <PortalButton type="button" variant="secondary" onClick={() => setConfirmDelete(false)} disabled={deleting}>
                      Cancel
                    </PortalButton>
                    <PortalButton type="button" variant="danger" onClick={deleteDomain} disabled={deleting}>
                      {deleting ? "Removing…" : "Confirm removal"}
                    </PortalButton>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </PortalCard>
    </div>
  );
}
