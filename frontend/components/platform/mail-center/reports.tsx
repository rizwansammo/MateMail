"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw, ShieldCheck } from "lucide-react";
import { describeError } from "@/contexts/platform-auth-context";
import { mailCenterApi, type MailDaily, type MailReportOverview, type ReceiverState } from "@/lib/mail-center-api";
import { Badge, Card, ErrorNote, Spinner, StatTile } from "@/components/platform/ui";

function pretty(value: number) { return value.toLocaleString(); }

function DailyBars({ points, field, label }: {
  points: MailDaily[]; field: "message_count" | "failed_sessions"; label: string;
}) {
  const max = Math.max(1, ...points.map((d) => d[field] ?? 0));
  const shown = points.length > 30 ? points.filter((_, i) => i % 3 === 0) : points;
  return (
    <div role="img" aria-label={label + " daily trend"} className="flex h-36 items-end gap-1 rounded-md p-2"
      style={{ background: "var(--pf-surface-2)" }}>
      {shown.map((day) => (
        <div key={day.date} className="group relative flex h-full min-w-0 flex-1 items-end"
          title={day.date + ": " + (day[field] ?? 0)}>
          <div className="w-full rounded-t-sm"
            style={{ height: Math.max(2, ((day[field] ?? 0) / max) * 100) + "%",
              background: "var(--pf-accent)" }} />
        </div>
      ))}
    </div>
  );
}

function ReceiverRows({ title, rows }: { title: string; rows: ReceiverState[] }) {
  return (
    <Card>
      <h3 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>{title} receivers</h3>
      <div className="mt-3 space-y-3">
        {rows.map((row) => (
          <div key={row.receiver} className="flex flex-wrap items-center justify-between gap-2 border-t pt-3"
            style={{ borderColor: "var(--pf-border)" }}>
            <div>
              <p className="text-sm font-medium capitalize">{row.receiver}</p>
              <p className="mt-1 text-xs pf-muted">
                Last checked {row.last_checked_at ? new Date(row.last_checked_at).toLocaleString() : "not yet"}
              </p>
            </div>
            <Badge tone={row.status === "operational" ? "ok" : row.status === "waiting" ? "info" : "warn"}>
              {row.status}
            </Badge>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function MailReports() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<MailReportOverview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((x) => x + 1), []);

  useEffect(() => {
    let active = true;
    mailCenterApi.reporting(days).then((payload) => {
      if (active) { setData(payload); setError(""); }
    }).catch((caught) => {
      if (active) { setData(null); setError(describeError(caught, "Report overview unavailable.")); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [days, revision]);

  return (
    <section className="space-y-4" aria-label="DMARC and TLS reporting">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>DMARC &amp; TLS Reporting</h2>
          <p className="mt-1 text-xs pf-muted">Platform sender DMARC and combined TLS operational totals. No customer report details.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {[7, 30, 90].map((value) => (
            <button key={value} type="button" className="pf-btn pf-btn-ghost" aria-pressed={days === value}
              onClick={() => { setLoading(true); setDays(value); }}>{value} days</button>
          ))}
          <button type="button" className="pf-btn pf-btn-ghost" onClick={refresh} aria-label="Refresh report overview">
            <RefreshCw className="h-4 w-4" /> Refresh
          </button>
        </div>
      </div>
      {error && <ErrorNote message={error} onRetry={refresh} />}
      {loading ? <Spinner label="Loading report telemetry" /> : data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="DMARC reports" value={pretty(data.dmarc.report_count)} />
            <StatTile label="Platform messages" value={pretty(data.dmarc.message_count)} />
            <StatTile label="TLS successful sessions" value={pretty(data.tls.successful_sessions)} />
            <StatTile label="TLS failed sessions" value={pretty(data.tls.failed_sessions)}
              tone={data.tls.failed_sessions > 0 ? "warn" : undefined} />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <div className="mb-3 flex items-center gap-2">
                <Activity className="h-4 w-4" />
                <h3 className="text-sm font-semibold">Platform DMARC traffic</h3>
              </div>
              <DailyBars points={data.dmarc.daily} field="message_count" label="Platform DMARC messages" />
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs pf-muted">
                <p>SPF pass: <b>{pretty(data.dmarc.spf_pass)}</b></p>
                <p>DKIM pass: <b>{pretty(data.dmarc.dkim_pass)}</b></p>
                <p>DMARC pass: <b>{pretty(data.dmarc.dmarc_pass)}</b></p>
              </div>
            </Card>
            <Card>
              <div className="mb-3 flex items-center gap-2">
                <Activity className="h-4 w-4" />
                <h3 className="text-sm font-semibold">TLS reporting failures</h3>
              </div>
              <DailyBars points={data.tls.daily} field="failed_sessions" label="TLS failed sessions" />
              <p className="mt-3 text-xs pf-muted">{pretty(data.tls.report_count)} aggregate reports across the platform. No tenant breakdown.</p>
            </Card>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <ReceiverRows title="DMARC" rows={data.receivers.dmarc} />
            <ReceiverRows title="TLS" rows={data.receivers.tls} />
          </div>
          <div className="flex items-start gap-2 rounded-lg p-3 text-xs"
            style={{ background: "var(--pf-info-soft)", color: "var(--pf-info)" }}>
            <ShieldCheck className="h-4 w-4 shrink-0" />
            <p>{data.notice} Date range: {data.window.start} to {data.window.end} UTC.</p>
          </div>
        </>
      )}
    </section>
  );
}
