"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Building2,
  Check,
  Globe2,
  LifeBuoy,
  Mail,
  ShieldCheck,
  Users,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";
import {
  PortalButton,
  PortalCard,
  PortalNotice,
  PortalPageHeading,
  PortalProgress,
  PortalSkeleton,
} from "@/components/workspace/premium-ui";

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
  ownership_verified: boolean;
  dns_health_score: number;
}

interface MemberSummary {
  id: string;
  status: string;
}

interface WorkspaceStats {
  tenant_status: string;
}

export default function OnboardingPage() {
  const { tenant, user } = useAuth();
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [domains, setDomains] = useState<DomainSummary[]>([]);
  const [members, setMembers] = useState<MemberSummary[]>([]);
  const [workspaceStatus, setWorkspaceStatus] = useState(tenant?.status || "");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!tenant?.id) return;
    let cancelled = false;

    Promise.allSettled([
      apiRequest(`/api/workspaces/${tenant.id}/onboarding/`).then(async (res) => res.ok ? res.json() : null),
      apiRequest("/api/domains/").then(async (res) => res.ok ? res.json() : []),
      apiRequest(`/api/workspaces/${tenant.id}/members/`).then(async (res) => res.ok ? res.json() : []),
      apiRequest(`/api/workspaces/${tenant.id}/stats/`).then(async (res) => res.ok ? res.json() : null),
    ]).then((results) => {
      if (cancelled) return;
      const [onboardingResult, domainsResult, membersResult, statsResult] = results;
      if (onboardingResult.status === "fulfilled" && onboardingResult.value) setStatus(onboardingResult.value);
      if (domainsResult.status === "fulfilled" && Array.isArray(domainsResult.value)) setDomains(domainsResult.value);
      if (membersResult.status === "fulfilled" && Array.isArray(membersResult.value)) setMembers(membersResult.value);
      if (statsResult.status === "fulfilled" && statsResult.value) {
        setWorkspaceStatus((statsResult.value as WorkspaceStats).tenant_status);
      }
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [tenant?.id]);

  const domainForReview =
    domains.find((domain) => !domain.ownership_verified || domain.status !== "active") ?? domains[0];

  const steps = useMemo(() => {
    const teamReady = members.filter((member) => member.status === "active").length > 1;
    return [
      {
        title: "Create your workspace",
        description: `${tenant?.name || "Your workspace"} is ready for your organization.`,
        done: status?.workspace_created ?? true,
        icon: Building2,
        href: "/app/settings",
        label: "Workspace settings",
      },
      {
        title: "Connect your email domain",
        description: "Bring your business identity to MateMail.",
        done: status?.domain_added ?? false,
        icon: Globe2,
        href: "/app/domains",
        label: "Add a domain",
      },
      {
        title: "Verify ownership & DNS",
        description: "Prove domain control, then verify MX, SPF, DKIM and DMARC.",
        done: status?.dns_verified ?? false,
        icon: ShieldCheck,
        href: domainForReview ? `/app/domains/${domainForReview.id}` : "/app/domains",
        label: "Review DNS records",
      },
      {
        title: "Create your first mailbox",
        description: "Give someone on your team their new email address.",
        done: status?.first_mailbox_created ?? false,
        icon: Mail,
        href: "/app/mailboxes",
        label: "Create mailbox",
      },
      {
        title: "Welcome your team",
        description: "Add another administrator or support teammate to your workspace.",
        done: teamReady,
        icon: Users,
        href: "/app/team",
        label: "Manage team",
      },
    ];
  }, [domainForReview, members, status, tenant?.name]);

  const completed = steps.filter((step) => step.done).length;
  const percentage = completed * 20;
  const mailActionsAvailable = workspaceStatus === "active" && !!user?.email_verified;

  if (loading) {
    return (
      <div className="portal-page">
        <PortalSkeleton className="mb-6 h-20 w-full" />
        <PortalSkeleton className="h-[420px] w-full" />
      </div>
    );
  }

  return (
    <div className="portal-page">
      <PortalPageHeading
        eyebrow="Getting started"
        title={`Make yourself at home, ${tenant?.name?.split(" ")[0] || "there"}.`}
        description="A few simple steps to get your organization’s email up and running."
      />

      {!user?.email_verified && (
        <div className="mb-5">
          <PortalNotice tone="warn">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
            <span><strong>Email verification required.</strong> Verify your account email before domain provisioning actions can run.</span>
          </PortalNotice>
        </div>
      )}

      {workspaceStatus !== "active" && (
        <div className="mb-5">
          <PortalNotice tone="info">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <strong>Workspace approval is {workspaceStatus.replaceAll("_", " ")}.</strong>{" "}
              You can review setup, but backend mail provisioning remains blocked until the workspace is active.
            </span>
          </PortalNotice>
        </div>
      )}

      <div className="portal-onboarding-layout">
        <div>
          <div className="portal-onboarding-progress">
            <div className="portal-onboarding-progress-row">
              <span><strong>{completed} of 5</strong> steps complete</span>
              <span>{percentage}%</span>
            </div>
            <PortalProgress value={percentage} />
          </div>

          <div className="portal-setup-steps">
            {steps.map((step, index) => {
              const Icon = step.icon;
              const blockedByApproval = index === 1 && !mailActionsAvailable && !step.done;
              return (
                <section className={"portal-setup-step " + (step.done ? "done" : "")} key={step.title}>
                  <span className="portal-step-number">
                    {step.done ? <Check className="h-4 w-4" /> : index + 1}
                  </span>
                  <div className="portal-step-copy">
                    <h3>{step.title}</h3>
                    <p>{step.description}</p>
                    {!step.done && !blockedByApproval && (
                      <Link href={step.href} className="portal-button primary">
                        {step.label}
                      </Link>
                    )}
                    {blockedByApproval && (
                      <p className="mt-2 font-semibold text-[var(--portal-warning)]">
                        Available after email verification and workspace approval.
                      </p>
                    )}
                  </div>
                  {step.done && (
                    <Link href={step.href} className="auth-text-button">Manage</Link>
                  )}
                </section>
              );
            })}
          </div>

          <div className="mt-5">
            <PortalNotice tone={completed === 5 ? "success" : "info"}>
              <Check className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="flex-1">
                <strong>{completed === 5 ? "You’re ready for business." : "Your setup progress is saved."}</strong>{" "}
                {completed === 5
                  ? "Your core workspace setup is complete."
                  : "You can return and finish the remaining steps whenever you’re ready."}
              </div>
              <Link href="/app" className="portal-button secondary">Go to overview</Link>
            </PortalNotice>
          </div>
        </div>

        <aside>
          <PortalCard className="portal-onboarding-tip">
            <span className="portal-metric-icon"><ShieldCheck className="h-5 w-5" /></span>
            <h2 className="mt-4 text-[15px] font-semibold text-[var(--portal-text-strong)]">Your email. Your identity.</h2>
            <p className="mt-2 text-[12px] leading-7 text-[var(--portal-muted)]">
              Connect a domain you already own. You’ll continue managing it with your current registrar.
            </p>
            <ul className="portal-tip-list">
              <li><Check className="h-4 w-4" />Access to your domain’s DNS settings</li>
              <li><Check className="h-4 w-4" />Your preferred mailbox names</li>
              <li><Check className="h-4 w-4" />Time for DNS changes to propagate</li>
            </ul>
            <div className="mt-5">
              <Link href="/app/domains" className="portal-button secondary">
                <Globe2 className="h-4 w-4" />
                Open domains
              </Link>
            </div>
          </PortalCard>

          <div className="mt-4">
            <PortalNotice tone="info">
              <LifeBuoy className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Every DNS value shown in the domain detail comes from the real MateMail backend, not prototype sample data.</span>
            </PortalNotice>
          </div>
        </aside>
      </div>
    </div>
  );
}
