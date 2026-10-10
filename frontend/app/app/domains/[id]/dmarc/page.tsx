"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Activity, ArrowLeft, Globe2, RefreshCw, ShieldCheck } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { PortalCard, PortalSkeleton } from "@/components/workspace/premium-ui";

type ReportRow = {
  id: string;
  policy_domain: string;
  reporter: string;
  period_start: string;
  period_end: string;
  message_count: number;
  spf_pass: number;
  dkim_pass: number;
  dmarc_pass: number;
};
type ReportSummary = {
  domain: string;
  days: number;
  report_count: number;
  message_count: number;
  spf_pass: number;
  dkim_pass: number;
  dmarc_pass: number;
  top_sources: { ip: string; messages: number }[];
  reports: ReportRow[];
  notice: string;
};

function count(value: number) {
  return Number(value).toLocaleString();
}
function rate(part: number, total: number) {
  return total > 0 ? (part * 100 / total).toFixed(1) + "%" : "—";
}
function date(value: string) {
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

export default function DomainDmarcReportsPage() {
  const params = useParams<{ id: string }>();
  const [days, setDays] = useState<7 | 30 | 90>(30);
  const [data, setData] = useState<ReportSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const response = await apiRequest(
          `/api/domains/${params.id}/dmarc-reports/?days=${days}`
        );
        if (!response.ok) {
          if (response.status === 403) {
            throw new Error("Only workspace owners and administrators can view DMARC reports.");
          }
          throw new Error("The report summary is not available right now.");
        }
        const result = await response.json() as ReportSummary;
        if (!cancelled) setData(result);
      } catch (exc) {
        if (!cancelled) {
          setData(null);
          setError(exc instanceof Error ? exc.message : "Unable to load reports.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [params.id, days]);

  return (
    <div className="portal-page astra-resource-page astra-advanced-page astra-report-page space-y-5">
      <div>
        <Link href={`/app/domains/${params.id}`} className="portal-back-link">
          <ArrowLeft className="h-3.5 w-3.5" /> Domain configuration
        </Link>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="portal-domain-title">DMARC reporting</h1>
            <p className="mt-1 text-sm text-[var(--portal-muted)]">
              {data?.domain ?? "Sender authentication"} · Aggregate receiver reports
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs text-[var(--portal-muted)]">
            Reporting window
            <select
              className="rounded-md border border-[var(--portal-border)] bg-[var(--portal-surface)] px-3 py-2 text-[var(--portal-text)]"
              value={days}
              onChange={(event) => setDays(Number(event.target.value) as 7 | 30 | 90)}
            >
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
          </label>
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          <PortalSkeleton className="h-28 w-full" />
          <PortalSkeleton className="h-64 w-full" />
        </div>
      ) : error ? (
        <PortalCard>
          <p role="alert" className="text-sm text-[var(--portal-danger)]">{error}</p>
        </PortalCard>
      ) : data ? (
        <>
          <PortalCard>
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--portal-text)]">
              <ShieldCheck className="h-4 w-4" /> Authentication results
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                { label: "Reported messages", value: count(data.message_count) },
                { label: "DMARC alignment pass", value: rate(data.dmarc_pass, data.message_count) },
                { label: "Aligned SPF pass", value: rate(data.spf_pass, data.message_count) },
                { label: "Aligned DKIM pass", value: rate(data.dkim_pass, data.message_count) },
              ].map((item) => (
                <div className="rounded-md border border-[var(--portal-border)] px-4 py-3" key={item.label}>
                  <p className="text-xs text-[var(--portal-muted)]">{item.label}</p>
                  <p className="mt-2 text-xl font-semibold tabular-nums text-[var(--portal-text)]">{item.value}</p>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-[var(--portal-muted)]">
              {count(data.report_count)} aggregate reports in this period. These results do not guarantee Inbox placement.
            </p>
          </PortalCard>
          {data.report_count === 0 ? (
            <PortalCard>
              <div className="py-8 text-center">
                <Activity className="mx-auto mb-3 h-6 w-6 text-[var(--portal-muted)]" />
                <h2 className="text-base font-semibold text-[var(--portal-text)]">No DMARC reports yet</h2>
                <p className="mx-auto mt-2 max-w-lg text-sm text-[var(--portal-muted)]">
                  Aggregate reporting requires a verified report receiver and a DMARC rua DNS record.
                  Your existing SPF, DKIM and mail routing do not need to change.
                </p>
              </div>
            </PortalCard>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              <PortalCard>
                <div className="mb-4 flex items-center gap-2 font-semibold text-[var(--portal-text)]">
                  <Globe2 className="h-4 w-4" /> Leading sending IPs
                </div>
                <div className="space-y-2">
                  {data.top_sources.map((source) => (
                    <div key={source.ip} className="flex justify-between gap-3 border-b border-[var(--portal-border)] py-2 text-sm">
                      <code className="text-[var(--portal-text)]">{source.ip}</code>
                      <span className="tabular-nums text-[var(--portal-muted)]">{count(source.messages)} messages</span>
                    </div>
                  ))}
                </div>
              </PortalCard>
              <PortalCard>
                <div className="mb-4 flex items-center gap-2 font-semibold text-[var(--portal-text)]">
                  <RefreshCw className="h-4 w-4" /> Recent reporter summaries
                </div>
                <div className="space-y-2">
                  {data.reports.slice(0, 15).map((report) => (
                    <div key={report.id} className="flex items-center justify-between gap-3 border-b border-[var(--portal-border)] py-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-[var(--portal-text)]">{report.reporter}</p>
                        <p className="text-xs text-[var(--portal-muted)]">{date(report.period_end)}</p>
                      </div>
                      <span className="shrink-0 text-xs tabular-nums text-[var(--portal-muted)]">
                        {count(report.message_count)} · {rate(report.dmarc_pass, report.message_count)}
                      </span>
                    </div>
                  ))}
                </div>
              </PortalCard>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
