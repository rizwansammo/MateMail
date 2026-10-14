"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, Inbox, LockKeyhole, RefreshCw, ShieldCheck } from "lucide-react";

import { PageHeader, Card, EmptyState, ErrorNote, Spinner } from "@/components/platform/ui";
import { describeError } from "@/contexts/platform-auth-context";
import { mailCenterApi, type MailCapabilities } from "@/lib/mail-center-api";
import { MailReader } from "@/components/platform/mail-center/operations";
import { MailReports } from "@/components/platform/mail-center/reports";
import { MailSensitiveAccess } from "@/components/platform/mail-center/sensitive";

type Tab = "operations" | "reports" | "access";

export default function PlatformMailCenterPage() {
  const [capabilities, setCapabilities] = useState<MailCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("operations");
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    mailCenterApi.capabilities().then((flags) => {
      if (alive) { setCapabilities(flags); setError(""); }
    }).catch((caught) => {
      if (alive) {
        setCapabilities(null);
        setError(describeError(caught, "Mail Center capabilities could not be checked."));
      }
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tick]);

  const available: Array<{ id: Tab; label: string; enabled: boolean; icon: typeof Inbox }> = [
    { id: "operations", label: "Operations", enabled: !!capabilities?.operations, icon: Inbox },
    { id: "reports", label: "Reporting", enabled: !!capabilities?.reporting, icon: Activity },
    { id: "access", label: "Sensitive Access", enabled: !!capabilities?.sensitive_access, icon: LockKeyhole },
  ];
  const enabled = available.filter((item) => item.enabled);
  const chosen = enabled.some((item) => item.id === tab) ? tab : enabled[0]?.id;

  return (
    <>
      <PageHeader title="Platform Mail Center"
        description="Manage approved system mail operations and reporting in a separate platform workspace."
        actions={<button type="button" className="pf-btn pf-btn-ghost" onClick={refresh}>
          <RefreshCw className="h-4 w-4" /> Recheck access
        </button>} />
      {error && <ErrorNote message={error} onRetry={refresh} />}
      {loading ? <Spinner label="Checking platform mail capabilities" /> : (
        <>
          {enabled.length === 0 ? (
            <Card>
              <EmptyState icon={ShieldCheck} title="Platform Mail Center is not active"
                detail="System mailbox and reporting features remain disabled until the controlled rollout. Existing email delivery is not affected." />
              <p className="pb-3 text-center text-xs pf-muted">
                Operators can enable capabilities separately after production acceptance checks.
              </p>
            </Card>
          ) : (
            <>
              <div role="tablist" aria-label="Mail Center modules" className="mb-4 flex flex-wrap gap-2">
                {enabled.map((item) => (
                  <button key={item.id} type="button" role="tab"
                    aria-selected={chosen === item.id} className="pf-btn pf-btn-ghost flex items-center gap-2"
                    onClick={() => setTab(item.id)}>
                    <item.icon className="h-4 w-4" /> {item.label}
                  </button>
                ))}
              </div>
              <div role="tabpanel" aria-label={enabled.find((item) => item.id === chosen)?.label}>
                {chosen === "operations" &&
                  <MailReader key="operations" role="operations" sending={capabilities?.sending ?? false} />}
                {chosen === "reports" && <MailReports />}
                {chosen === "access" && <MailSensitiveAccess />}
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
