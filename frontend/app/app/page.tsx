"use client";

import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import Link from "next/link";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Globe2,
  HardDrive,
  Mail,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { api, apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalEmptyState,
  PortalMetric,
  PortalNotice,
  PortalPageHeading,
  PortalProgress,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface WorkspaceStats {
  domain_count: number;
  active_domain_count: number;
  mailbox_count: number;
  active_mailbox_count: number;
  storage_used_mb: number;
  storage_quota_mb: number;
  member_count: number;
  my_role: string;
  tenant_status: string;
  tenant_plan: string;
}

interface OnboardingStatus {
  workspace_created: boolean;
  domain_added: boolean;
  dns_verified: boolean;
  first_mailbox_created: boolean;
}

interface DomainSummary {
  id: string;
  domain: string;
  status: string;
  dns_health_score: number;
  ownership_verified: boolean;
  mail_service_ready: boolean;
}

interface AuditEvent {
  id: string;
  event_type: string;
  source: string;
  result: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

function pretty(value?: string | null) {
  if (!value) return "Unknown";
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function eventLabel(value: string) {
  return pretty(value);
}

function relativeTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const diff = Math.max(0, Date.now() - date.getTime());
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function metadataTarget(event: AuditEvent) {
  const metadata = event.metadata || {};
  const target =
    metadata.domain ??
    metadata.mailbox ??
    metadata.email ??
    metadata.target ??
    metadata.name;
  return typeof target === "string" && target ? target : event.source || "Workspace";
}

function ApprovalNotice({ status }: { status: string }) {
  if (status === "active") return null;
  const content: Record<string, { tone: "info" | "warn" | "danger"; title: string; text: string }> = {
    pending_approval: {
      tone: "info",
      title: "Private Beta approval pending",
      text: "You can review your workspace while approval is pending. Mail provisioning actions become available after platform approval.",
    },
    rejected: {
      tone: "danger",
      title: "Workspace application was not approved",
      text: "Mail provisioning is unavailable for this workspace.",
    },
    suspended: {
      tone: "danger",
      title: "Workspace suspended",
      text: "Mail sending, receiving and provisioning actions are currently unavailable.",
    },
    cancelled: {
      tone: "danger",
      title: "Workspace cancelled",
      text: "This workspace can no longer provision or operate mail services.",
    },
  };
  const item = content[status] ?? {
    tone: "warn" as const,
    title: pretty(status),
    text: "Some mail operations may be unavailable in the current workspace state.",
  };
  return (
    <div className="portal-approval-banner">
      <PortalNotice tone={item.tone}>
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
        <span><strong>{item.title}.</strong> {item.text}</span>
      </PortalNotice>
    </div>
  );
}

export default function DashboardPage() {
  const { user, tenant } = useAuth();
  const [stats, setStats] = useState<WorkspaceStats | null>(null);
  const [onboarding, setOnboarding] = useState<OnboardingStatus | null>(null);
  const [domains, setDomains] = useState<DomainSummary[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [verifyMessage, setVerifyMessage] = useState("");
  const [verifyBusy, setVerifyBusy] = useState(false);

  useEffect(() => {
    if (!tenant?.id) return;
    let cancelled = false;

    Promise.allSettled([
      apiRequest(`/api/workspaces/${tenant.id}/stats/`).then(async (res) => res.ok ? res.json() : null),
      apiRequest(`/api/workspaces/${tenant.id}/onboarding/`).then(async (res) => res.ok ? res.json() : null),
      apiRequest("/api/domains/").then(async (res) => res.ok ? res.json() : []),
      apiRequest("/api/logs/").then(async (res) => res.ok ? res.json() : []),
    ]).then((results) => {
      if (cancelled) return;
      const [statsResult, onboardingResult, domainResult, logResult] = results;
      if (statsResult.status === "fulfilled" && statsResult.value) setStats(statsResult.value);
      if (onboardingResult.status === "fulfilled" && onboardingResult.value) setOnboarding(onboardingResult.value);
      if (domainResult.status === "fulfilled" && Array.isArray(domainResult.value)) setDomains(domainResult.value);
      if (logResult.status === "fulfilled" && Array.isArray(logResult.value)) setEvents(logResult.value.slice(0, 4));
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [tenant?.id]);

  const setupSteps = useMemo(() => {
    if (!onboarding) return 0;
    return [
      onboarding.workspace_created,
      onboarding.domain_added,
      onboarding.dns_verified,
      onboarding.first_mailbox_created,
    ].filter(Boolean).length;
  }, [onboarding]);

  const setupComplete = setupSteps === 4;
  const storageUsedGb = stats ? stats.storage_used_mb / 1024 : 0;
  const storageQuotaGb = stats ? stats.storage_quota_mb / 1024 : 0;

  const healthScore = useMemo(() => {
    if (!stats?.domain_count) return null;
    if (domains.length) {
      const total = domains.reduce((sum, domain) => {
        const ownership = domain.ownership_verified ? 20 : 0;
        const dns = Math.round((domain.dns_health_score || 0) * 0.6);
        const service = domain.mail_service_ready ? 20 : 0;
        return sum + ownership + dns + service;
      }, 0);
      return Math.round(total / domains.length);
    }
    return Math.round((stats.active_domain_count / Math.max(stats.domain_count, 1)) * 100);
  }, [domains, stats]);

  async function resendVerification() {
    setVerifyBusy(true);
    setVerifyMessage("");
    try {
      const result = await api.post<{ detail?: string }>("/api/auth/resend-verification/");
      setVerifyMessage(result.detail ?? "Verification email sent.");
    } catch {
      setVerifyMessage("Could not send the verification email right now.");
    } finally {
      setVerifyBusy(false);
    }
  }

  if (loading && !stats) {
    return (
      <div className="portal-page">
        <PortalSkeleton className="mb-5 h-16 w-full" />
        <div className="portal-metrics-grid">
          {Array.from({ length: 4 }).map((_, index) => (
            <PortalSkeleton key={index} className="h-32 w-full" />
          ))}
        </div>
        <PortalSkeleton className="h-72 w-full" />
      </div>
    );
  }

  return (
    <div className="portal-page">
      <PortalPageHeading
        title="Workspace overview"
        description="A clear view of your organization’s email."
        actions={
          <Link href="/app/mailboxes" className="portal-button primary">
            <Mail className="h-4 w-4" />
            Create mailbox
          </Link>
        }
      />

      <ApprovalNotice status={stats?.tenant_status || tenant?.status || "active"} />

      {user && !user.email_verified && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="flex-1">
              <strong>Verify your email address.</strong> Domain and provisioning actions require a verified account.
              {verifyMessage && <span className="ml-1">{verifyMessage}</span>}
            </div>
            <PortalButton variant="secondary" type="button" disabled={verifyBusy} onClick={resendVerification}>
              {verifyBusy ? "Sending…" : "Resend"}
            </PortalButton>
          </PortalNotice>
        </div>
      )}

      <div className="portal-metrics-grid">
        <PortalMetric
          icon={<Globe2 className="h-4 w-4" />}
          label="Connected domains"
          value={String(stats?.domain_count ?? 0).padStart(2, "0")}
          detail={
            stats?.domain_count
              ? stats.active_domain_count === stats.domain_count
                ? "All domains healthy"
                : `${stats.domain_count - stats.active_domain_count} need attention`
              : "Connect your first domain"
          }
          href="/app/domains"
        />
        <PortalMetric
          icon={<Mail className="h-4 w-4" />}
          label="Total mailboxes"
          value={String(stats?.mailbox_count ?? 0).padStart(2, "0")}
          detail={stats ? `${stats.active_mailbox_count} active` : "—"}
          href="/app/mailboxes"
        />
        <PortalMetric
          icon={<Users className="h-4 w-4" />}
          label="Team members"
          value={String(stats?.member_count ?? 0).padStart(2, "0")}
          detail={stats ? `Your role: ${pretty(stats.my_role)}` : "—"}
          href="/app/team"
        />
        <PortalMetric
          icon={<HardDrive className="h-4 w-4" />}
          label="Storage used"
          value={<>{storageUsedGb.toFixed(1)}<small>GB</small></>}
          detail={storageQuotaGb > 0 ? `of ${storageQuotaGb.toFixed(storageQuotaGb % 1 ? 1 : 0)} GB allocated` : "No mailbox quota allocated yet"}
          href="/app/mailboxes"
        />
      </div>

      {!setupComplete && (
        <div className="portal-setup-banner">
          <span className="portal-setup-icon"><Sparkles className="h-5 w-5" /></span>
          <div className="portal-setup-copy">
            <h3>{stats?.domain_count ? "You’re almost set up" : "Let’s get your email workspace ready"}</h3>
            <p>Connect your domain, verify DNS, and create your first mailbox.</p>
          </div>
          <div className="portal-setup-progress">
            <span>{setupSteps} of 4 core steps complete</span>
            <PortalProgress value={setupSteps * 25} />
          </div>
          <Link href="/app/onboarding" className="portal-button secondary">Continue setup</Link>
        </div>
      )}

      <div className="portal-overview-grid">
        <PortalCard
          title="Workspace readiness"
          subtitle="Real configuration status from your workspace."
          action={<ShieldCheck className="h-4 w-4 text-[var(--portal-muted)]" />}
        >
          {stats?.domain_count ? (
            <>
              <div
                className="portal-health-score"
                style={{ "--health-angle": `${Math.round((healthScore ?? 0) * 3.6)}deg` } as CSSProperties}
              >
                <span className="portal-health-ring" />
                <div>
                  <strong>{healthScore ?? 0}%</strong>
                  <small>configuration health</small>
                </div>
              </div>
              <div className="portal-health-rows">
                <div className="portal-health-row">
                  <span><Globe2 className="h-4 w-4" />Domain readiness</span>
                  <span>{stats.active_domain_count}/{stats.domain_count} active</span>
                </div>
                <div className="portal-health-row">
                  <span><ShieldCheck className="h-4 w-4" />Ownership</span>
                  <span>{domains.filter((item) => item.ownership_verified).length}/{stats.domain_count} verified</span>
                </div>
                <div className="portal-health-row">
                  <span><Mail className="h-4 w-4" />Mail services</span>
                  <span>{domains.filter((item) => item.mail_service_ready).length}/{stats.domain_count} ready</span>
                </div>
              </div>
            </>
          ) : (
            <PortalEmptyState
              title="Workspace health starts with a domain"
              description="Connect your business domain to begin ownership, DNS and mail-service checks."
              action={<Link href="/app/domains" className="portal-button primary">Add domain</Link>}
            />
          )}
        </PortalCard>

        <PortalCard
          title="Workspace status"
          subtitle="Approval and resource state."
        >
          <div className="portal-detail-grid">
            <div className="portal-fact">
              <span>Workspace</span>
              <strong>{tenant?.name || "—"}</strong>
            </div>
            <div className="portal-fact">
              <span>Status</span>
              <div className="mt-2"><PortalStatus value={pretty(stats?.tenant_status || tenant?.status)} /></div>
            </div>
            <div className="portal-fact">
              <span>Plan</span>
              <strong>{pretty(stats?.tenant_plan)}</strong>
            </div>
            <div className="portal-fact">
              <span>Your role</span>
              <strong>{pretty(stats?.my_role)}</strong>
            </div>
          </div>
          <div className="mt-4">
            <PortalNotice tone={stats?.tenant_status === "active" ? "success" : "info"}>
              {stats?.tenant_status === "active"
                ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                : <Activity className="mt-0.5 h-4 w-4 shrink-0" />}
              <span>
                {stats?.tenant_status === "active"
                  ? "Workspace mail operations are approved."
                  : "Approval state is enforced by the backend; unavailable mail actions stay blocked."}
              </span>
            </PortalNotice>
          </div>
        </PortalCard>
      </div>

      <div className="portal-overview-bottom">
        <PortalCard
          title="Your domains"
          subtitle="Connected to this workspace."
          action={<Link href="/app/domains" className="auth-text-button">View all domains</Link>}
        >
          {domains.length ? (
            <>
              <div className="portal-domain-mini-head">
                <span>Domain</span>
                <span>Health</span>
                <span>Status</span>
              </div>
              {domains.slice(0, 3).map((domain) => (
                <Link className="portal-domain-mini" key={domain.id} href={`/app/domains/${domain.id}`}>
                  <span className="portal-domain-identity">
                    <span className="portal-domain-symbol"><Globe2 className="h-4 w-4" /></span>
                    <span>
                      <strong>{domain.domain}</strong>
                      <small>{domain.ownership_verified ? "Ownership verified" : "Ownership pending"}</small>
                    </span>
                  </span>
                  <span>{domain.dns_health_score}%</span>
                  <PortalStatus value={pretty(domain.status)} />
                </Link>
              ))}
              <div className="mt-3">
                <Link href="/app/domains" className="portal-button secondary">
                  Manage domains
                </Link>
              </div>
            </>
          ) : (
            <PortalEmptyState
              title="Connect your first domain"
              description="Use your own domain for professional email."
              action={<Link href="/app/domains" className="portal-button primary">Add domain</Link>}
            />
          )}
        </PortalCard>

        <PortalCard title="Recent activity" subtitle="Latest workspace audit events available to your role.">
          {events.length ? (
            <div className="portal-activity-list">
              {events.map((event) => (
                <div className="portal-activity-row" key={event.id}>
                  <span className="portal-activity-icon"><Activity className="h-3.5 w-3.5" /></span>
                  <span className="portal-activity-copy">
                    <strong>{eventLabel(event.event_type)}</strong>
                    <small>{metadataTarget(event)} · {pretty(event.result)}</small>
                  </span>
                  <time>{relativeTime(event.created_at)}</time>
                </div>
              ))}
            </div>
          ) : (
            <PortalEmptyState
              title="No activity to show"
              description="Audit events will appear here when available to your workspace role."
            />
          )}
        </PortalCard>
      </div>
    </div>
  );
}
