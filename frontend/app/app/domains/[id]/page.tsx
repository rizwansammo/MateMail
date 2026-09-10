"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, RefreshCw, Copy, CheckCircle2, XCircle, Clock, AlertCircle } from "lucide-react";
import { apiRequest } from "@/lib/api";

interface Domain {
  id: string;
  domain: string;
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
}

const RECORD_STATUS_CONFIG = {
  verified: { icon: CheckCircle2, color: "text-emerald-600", bg: "bg-emerald-50", label: "Verified" },
  pending:  { icon: Clock,        color: "text-amber-500",   bg: "bg-amber-50",   label: "Pending"  },
  missing:  { icon: AlertCircle,  color: "text-orange-500",  bg: "bg-orange-50",  label: "Missing"  },
  failed:   { icon: XCircle,      color: "text-red-500",     bg: "bg-red-50",     label: "Failed"   },
};

const DOMAIN_STATUS_STYLES: Record<string, string> = {
  active:  "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  pending: "bg-amber-50  text-amber-700  ring-amber-600/20",
  warning: "bg-orange-50 text-orange-700 ring-orange-600/20",
  failed:  "bg-red-50    text-red-700    ring-red-600/20",
  paused:  "bg-slate-100 text-slate-600  ring-slate-500/20",
};

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return (
    <button onClick={copy} className="ml-2 shrink-0 text-slate-400 hover:text-slate-600" title="Copy">
      {copied ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
    </button>
  );
}

function RecordRow({ rec }: { rec: DNSRecord }) {
  const cfg = RECORD_STATUS_CONFIG[rec.status] ?? RECORD_STATUS_CONFIG.pending;
  const Icon = cfg.icon;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs font-medium text-slate-700">
            {rec.record_type}
          </span>
          <span className="text-sm font-medium text-slate-800">{_recordLabel(rec)}</span>
        </div>
        <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.color}`}>
          <Icon className="h-3.5 w-3.5" />
          {cfg.label}
        </span>
      </div>

      <div className="space-y-2 text-xs">
        <div className="grid grid-cols-[80px_1fr] gap-x-2">
          <span className="text-slate-400 pt-0.5">Host</span>
          <div className="flex items-start">
            <span className="break-all font-mono text-slate-600">{rec.host}</span>
            <CopyButton value={rec.host} />
          </div>
        </div>
        <div className="grid grid-cols-[80px_1fr] gap-x-2">
          <span className="text-slate-400 pt-0.5">Expected</span>
          <div className="flex items-start">
            <span className="break-all font-mono text-slate-600">{rec.expected_value}</span>
            <CopyButton value={rec.expected_value} />
          </div>
        </div>
        {rec.detected_value && (
          <div className="grid grid-cols-[80px_1fr] gap-x-2">
            <span className="text-slate-400 pt-0.5">Detected</span>
            <span className="break-all font-mono text-slate-500">{rec.detected_value}</span>
          </div>
        )}
        {rec.last_checked && (
          <div className="grid grid-cols-[80px_1fr] gap-x-2">
            <span className="text-slate-400">Checked</span>
            <span className="text-slate-400">{new Date(rec.last_checked).toLocaleString()}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function _recordLabel(rec: DNSRecord): string {
  if (rec.record_type === "MX") return "Mail Server (MX)";
  if (rec.host.startsWith("_dmarc")) return "DMARC Policy";
  if (rec.host.includes("._domainkey")) return `DKIM (${rec.host.split("._domainkey")[0]})`;
  if (rec.expected_value.startsWith("v=spf1")) return "SPF";
  return rec.record_type;
}

function HealthBar({ score }: { score: number }) {
  const color =
    score >= 75 ? "bg-emerald-500" : score >= 50 ? "bg-amber-500" : score > 0 ? "bg-orange-500" : "bg-slate-200";
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-32 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className="text-sm tabular-nums text-slate-500">{score}/100</span>
    </div>
  );
}

export default function DomainDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [domain, setDomain] = useState<Domain | null>(null);
  const [records, setRecords] = useState<DNSRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [dRes, rRes] = await Promise.all([
        apiRequest(`/api/domains/${params.id}/`),
        apiRequest(`/api/domains/${params.id}/records/`),
      ]);
      if (dRes.ok) setDomain(await dRes.json());
      if (rRes.ok) setRecords(await rRes.json());
    } finally {
      setLoading(false);
    }
  }, [params.id]);

  useEffect(() => { fetchData(); }, [fetchData]);

  async function checkDNS() {
    setChecking(true);
    try {
      const res = await apiRequest(`/api/domains/${params.id}/check/`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setDomain(data.domain);
        setRecords(data.records);
      }
    } finally {
      setChecking(false);
    }
  }

  if (loading) {
    return (
      <div className="py-24 text-center text-sm text-slate-400">Loading domain…</div>
    );
  }

  if (!domain) {
    return (
      <div className="py-24 text-center">
        <p className="text-sm text-slate-500">Domain not found.</p>
        <button onClick={() => router.push("/app/domains")} className="mt-3 text-sm text-cyan-600 hover:text-cyan-700">
          Back to domains
        </button>
      </div>
    );
  }

  const statusCfg = RECORD_STATUS_CONFIG[domain.status as keyof typeof RECORD_STATUS_CONFIG];
  const noRecordsYet = records.length === 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <button
            onClick={() => router.push("/app/domains")}
            className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Domains
          </button>
          <h1 className="text-xl font-semibold text-slate-900">{domain.domain}</h1>
          <div className="flex items-center gap-3">
            <span
              className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${DOMAIN_STATUS_STYLES[domain.status] ?? DOMAIN_STATUS_STYLES.pending}`}
            >
              {domain.status}
            </span>
            <HealthBar score={domain.dns_health_score} />
          </div>
        </div>
        <button
          onClick={checkDNS}
          disabled={checking}
          className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-60"
        >
          <RefreshCw className={`h-4 w-4 ${checking ? "animate-spin" : ""}`} />
          {checking ? "Checking…" : "Check DNS"}
        </button>
      </div>

      {/* No records yet */}
      {noRecordsYet && (
        <div className="rounded-lg border border-dashed border-slate-300 py-10 text-center">
          <p className="text-sm text-slate-500">DNS check not run yet.</p>
          <button
            onClick={checkDNS}
            disabled={checking}
            className="mt-3 text-sm font-medium text-cyan-600 hover:text-cyan-700 disabled:opacity-50"
          >
            Run check now →
          </button>
        </div>
      )}

      {/* DNS Records */}
      {!noRecordsYet && (
        <div className="space-y-3">
          <h2 className="text-sm font-medium text-slate-700">DNS Records</h2>
          {records.map((rec) => (
            <RecordRow key={rec.id} rec={rec} />
          ))}
        </div>
      )}

      {/* Mail service status */}
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {domain.mail_service_ready ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            ) : (
              <AlertCircle className="h-4 w-4 text-amber-400" />
            )}
            <span className="text-sm font-medium text-slate-800">
              {domain.mail_service_ready ? "Mail service active" : "Mail service setup in progress"}
            </span>
          </div>
          {!domain.mail_service_ready && (
            <button
              onClick={async () => {
                await apiRequest(`/api/domains/${params.id}/provision/`, { method: "POST" });
                await fetchData();
              }}
              className="text-xs font-medium text-cyan-600 hover:text-cyan-700"
            >
              Retry provisioning
            </button>
          )}
        </div>
        {domain.mail_service_message && (
          <p className="mt-2 rounded bg-red-50 px-3 py-2 text-xs text-red-700 font-mono">
            {domain.mail_service_message}
          </p>
        )}
      </div>

      {/* Setup instructions */}
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-5 text-sm text-slate-600 space-y-2">
        <p className="font-medium text-slate-800">How to configure your domain</p>
        <ol className="list-decimal list-inside space-y-1 text-slate-500">
          <li>Log in to your domain registrar&apos;s DNS settings.</li>
          <li>Add the MX, SPF, DKIM, and DMARC records shown above.</li>
          <li>Click <strong>Check DNS</strong> once records are published (may take up to 48 h to propagate).</li>
          <li>Domain status becomes <strong>Active</strong> once MX and SPF are verified.</li>
        </ol>
      </div>

      {domain.verified_at && (
        <p className="text-xs text-slate-400">
          First verified: {new Date(domain.verified_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
