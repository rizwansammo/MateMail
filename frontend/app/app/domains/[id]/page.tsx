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

function recordLabel(record: DNSRecord) {
  if (record.record_type === "SRV") return "Autodiscover (SRV)";
  if (record.record_type === "MX") return "Mail Server (MX)";
  if (record.host.startsWith("_dmarc")) return "DMARC Policy";
  if (record.host.includes("._domainkey")) {
    return `DKIM (${record.host.split("._domainkey")[0]})`;
  }
  if (record.expected_value.startsWith("v=spf1")) return "SPF";
  return record.record_type;
}

function RecordRow({ record }: { record: DNSRecord }) {
  const icon =
    record.status === "verified" ? <CheckCircle2 className="h-4 w-4 text-[var(--portal-success)]" /> :
    record.status === "failed" ? <XCircle className="h-4 w-4 text-[var(--portal-danger)]" /> :
    record.status === "missing" ? <AlertCircle className="h-4 w-4 text-[var(--portal-warning)]" /> :
    <Clock3 className="h-4 w-4 text-[var(--portal-warning)]" />;

  return (
    <article className="portal-dns-record">
      <div className="portal-dns-record-head">
        {icon}
        <span className="portal-record-type">{record.record_type}</span>
        <h3>{recordLabel(record)}</h3>
        <PortalStatus value={pretty(record.status)} />
      </div>

      <div className="portal-dns-fields">
        <div className="portal-dns-field">
          <span>HOST / NAME</span>
          <div className="portal-code-field">
            <code>{record.host}</code>
            <PortalCopyButton value={record.host} label={`Copy ${recordLabel(record)} host`} />
          </div>
        </div>
        <div className="portal-dns-field">
          <span>EXPECTED VALUE</span>
          <div className="portal-code-field">
            <code>{record.expected_value}</code>
            <PortalCopyButton value={record.expected_value} label={`Copy ${recordLabel(record)} value`} />
          </div>
        </div>
      </div>

      {record.detected_value && (
        <p className="portal-detected">
          <strong>Detected:</strong> <code>{record.detected_value}</code>
        </p>
      )}
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
  const [records, setRecords] = useState<DNSRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"dns" | "details">("dns");
  const [checking, setChecking] = useState(false);
  const [checkMessage, setCheckMessage] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [actionFailed, setActionFailed] = useState(false);
  const [provisioning, setProvisioning] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const pollTimers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const role = tenant?.role || "";
  const canAdmin = role === "owner" || role === "admin";
  const canSupportAction = canAdmin || role === "support";

  const fetchData = useCallback(async () => {
    const [domainResponse, recordsResponse] = await Promise.all([
      apiRequest(`/api/domains/${params.id}/`),
      apiRequest(`/api/domains/${params.id}/records/`),
    ]);
    if (domainResponse.ok) setDomain(await domainResponse.json());
    if (recordsResponse.ok) setRecords(await recordsResponse.json());
  }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await fetchData();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchData]);

  useEffect(() => {
    const timers = pollTimers.current;
    return () => {
      timers.forEach(clearTimeout);
      timers.length = 0;
    };
  }, []);

  async function checkDNS() {
    if (!canSupportAction) return;
    setChecking(true);
    setCheckMessage("");
    try {
      const response = await apiRequest(`/api/domains/${params.id}/check/`, { method: "POST" });
      const data = await response.json().catch(() => null);
      if (response.status === 202) {
        setCheckMessage(data?.detail ?? "DNS check started.");
        pollTimers.current.forEach(clearTimeout);
        pollTimers.current = [5000, 15000, 30000].map((delay) =>
          setTimeout(() => {
            fetchData().catch(() => {});
          }, delay)
        );
      } else {
        setCheckMessage(data?.detail ?? "The DNS check could not be started.");
      }
    } catch {
      setCheckMessage("The DNS check could not be started. Please try again.");
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
          <PortalButton type="button" variant="secondary" onClick={checkDNS} disabled={checking}>
            <RefreshCw className={"h-4 w-4 " + (checking ? "animate-spin" : "")} />
            {checking ? "Starting…" : "Re-check DNS"}
          </PortalButton>
        )}
      </div>

      {checkMessage && (
        <div className="mb-4">
          <PortalNotice tone="info">
            <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{checkMessage} Results refresh automatically as the worker completes.</span>
          </PortalNotice>
        </div>
      )}

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
                    ? `${verifiedScored} of ${scoredRecords.length} scored DNS records are currently verified.`
                    : "Run a DNS check to populate the current records."}
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
                <div className="portal-dns-list">
                  {scoredRecords.map((record) => (
                    <RecordRow key={record.id} record={record} />
                  ))}
                </div>

                {discoveryRecords.length > 0 && (
                  <div className="mt-6">
                    <div className="mb-3">
                      <h2 className="text-[13px] font-semibold text-[var(--portal-text-strong)]">Mail client discovery</h2>
                      <p className="mt-1 text-[10px] leading-5 text-[var(--portal-muted)]">
                        Optional discovery records help some mail clients configure themselves. They do not affect the DNS health score.
                      </p>
                    </div>
                    <div className="portal-dns-list">
                      {discoveryRecords.map((record) => (
                        <RecordRow key={record.id} record={record} />
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
                      <PortalButton type="button" onClick={checkDNS} disabled={checking}>
                        <RefreshCw className={"h-4 w-4 " + (checking ? "animate-spin" : "")} />
                        Run DNS check
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
