"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Activity, ArrowLeft, ShieldCheck } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { PortalCard, PortalSkeleton } from "@/components/workspace/premium-ui";

type TlsReport = {
  id: string;
  reporter: string;
  policy_type: string;
  period_end: string;
  successful_sessions: number;
  failed_sessions: number;
};
type TlsSummary = {
  domain: string;
  days: number;
  report_count: number;
  successful_sessions: number;
  failed_sessions: number;
  failure_types: { result_type: string; count: number }[];
  reports: TlsReport[];
  notice: string;
};

export default function DomainTlsReportsPage() {
  const params = useParams<{ id: string }>();
  const [days, setDays] = useState<7 | 30 | 90>(30);
  const [data, setData] = useState<TlsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    apiRequest("/api/domains/" + params.id + "/tls-reports/?days=" + days, { signal: controller.signal })
      .then(async (response) => {
        const result = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(response.status === 403
            ? "Only workspace owners and administrators can view TLS reports."
            : "TLS reporting data is not available.");
        }
        if (!controller.signal.aborted) setData(result as TlsSummary);
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Cannot load TLS reports.");
          setData(null);
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); };
  }, [params.id, days]);

  const num = (value: number) => value.toLocaleString();
  return (
    <div className="portal-page space-y-5">
      <div>
        <Link className="portal-back-link" href={"/app/domains/" + params.id}>
          <ArrowLeft className="h-3.5 w-3.5" /> Domain configuration
        </Link>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="portal-domain-title">TLS failure reporting</h1>
            <p className="mt-1 text-sm text-[var(--portal-muted)]">
              {data?.domain ?? "Domain"} · Aggregated reports from receiving mail providers
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs text-[var(--portal-muted)]">
            Period
            <select className="rounded-md border border-[var(--portal-border)] bg-[var(--portal-surface)] px-3 py-2 text-[var(--portal-text)]"
              value={days} onChange={(event) => { setLoading(true); setError(""); setDays(Number(event.target.value) as 7 | 30 | 90); }}>
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
          </label>
        </div>
      </div>
      {loading ? (
        <PortalSkeleton className="h-64 w-full" />
      ) : error ? (
        <PortalCard><p role="alert" className="text-sm text-[var(--portal-danger)]">{error}</p></PortalCard>
      ) : data ? (
        <>
          <PortalCard>
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-[var(--portal-text)]">
              <ShieldCheck className="h-4 w-4" /> Transport security signal
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                ["Reports received", data.report_count],
                ["Successful TLS sessions", data.successful_sessions],
                ["Reported failed sessions", data.failed_sessions],
              ].map(([label, value]) => (
                <div key={label} className="rounded-md border border-[var(--portal-border)] p-4">
                  <p className="text-xs text-[var(--portal-muted)]">{label}</p>
                  <p className="mt-2 text-xl font-semibold tabular-nums text-[var(--portal-text)]">{num(value as number)}</p>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs leading-5 text-[var(--portal-muted)]">{data.notice}</p>
          </PortalCard>
          {data.report_count === 0 ? (
            <PortalCard>
              <div className="py-8 text-center">
                <Activity className="mx-auto mb-3 h-6 w-6 text-[var(--portal-muted)]" />
                <h2 className="text-base font-semibold text-[var(--portal-text)]">No TLS reports yet</h2>
                <p className="mx-auto mt-2 max-w-md text-sm text-[var(--portal-muted)]">
                  Report intake is optional. A working report receiver and published TLS-RPT DNS record
                  must be verified separately. Your existing email setup is not affected.
                </p>
              </div>
            </PortalCard>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              <PortalCard title="Reported failure categories">
                <div className="space-y-2">
                  {data.failure_types.length ? data.failure_types.map((row) => (
                    <div className="flex justify-between gap-3 border-b border-[var(--portal-border)] py-2 text-xs" key={row.result_type}>
                      <span className="min-w-0 break-all text-[var(--portal-text)]">{row.result_type.replaceAll("-", " ")}</span>
                      <span className="tabular-nums text-[var(--portal-muted)]">{num(row.count)}</span>
                    </div>
                  )) : <p className="text-sm text-[var(--portal-muted)]">No failure breakdown available.</p>}
                </div>
              </PortalCard>
              <PortalCard title="Recent reports">
                <div className="space-y-2">
                  {data.reports.map((row) => (
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--portal-border)] py-2" key={row.id}>
                      <div>
                        <p className="text-xs font-medium text-[var(--portal-text)]">{row.reporter}</p>
                        <p className="text-[11px] text-[var(--portal-muted)]">
                          {row.policy_type.toUpperCase()} · {new Date(row.period_end).toLocaleDateString()}
                        </p>
                      </div>
                      <span className="text-xs tabular-nums text-[var(--portal-muted)]">
                        {num(row.successful_sessions)} success · {num(row.failed_sessions)} failed
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
