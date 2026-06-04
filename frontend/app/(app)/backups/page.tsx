"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "@/lib/api";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  DatabaseBackup,
  HardDrive,
  Loader2,
  Play,
  RefreshCw,
  Timer,
  XCircle,
} from "lucide-react";

interface BackupJob {
  id: string;
  scope: "workspace" | "domain" | "mailbox";
  status: "pending" | "running" | "completed" | "failed";
  size_mb: number;
  storage_location: string;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  restore_metadata: Record<string, unknown>;
  duration_seconds: number | null;
}

const SCOPE_LABELS: Record<string, string> = {
  workspace: "Workspace",
  domain: "Domains",
  mailbox: "Mailboxes",
};

const SCOPE_COLORS: Record<string, string> = {
  workspace: "bg-violet-100 text-violet-700",
  domain: "bg-cyan-100 text-cyan-700",
  mailbox: "bg-amber-100 text-amber-700",
};

const STATUS_CONFIG: Record<
  string,
  { label: string; color: string; icon: React.ReactNode }
> = {
  pending: {
    label: "Pending",
    color: "bg-slate-100 text-slate-600",
    icon: <Clock className="h-3 w-3" />,
  },
  running: {
    label: "Running",
    color: "bg-blue-100 text-blue-700",
    icon: <Loader2 className="h-3 w-3 animate-spin" />,
  },
  completed: {
    label: "Completed",
    color: "bg-emerald-100 text-emerald-700",
    icon: <CheckCircle2 className="h-3 w-3" />,
  },
  failed: {
    label: "Failed",
    color: "bg-red-100 text-red-700",
    icon: <XCircle className="h-3 w-3" />,
  },
};

const SCOPE_OPTIONS = ["all", "workspace", "domain", "mailbox"];
const STATUS_OPTIONS = ["all", "pending", "running", "completed", "failed"];

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function formatSize(mb: number): string {
  if (mb === 0) return "—";
  if (mb < 1024) return `${mb} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function BackupsPage() {
  const [jobs, setJobs] = useState<BackupJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [scopeFilter, setScopeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [triggerScope, setTriggerScope] = useState<"workspace" | "domain" | "mailbox">("workspace");
  const [showTrigger, setShowTrigger] = useState(false);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchJobs = useCallback(async () => {
    const params = new URLSearchParams();
    if (scopeFilter !== "all") params.set("scope", scopeFilter);
    if (statusFilter !== "all") params.set("status", statusFilter);
    const r = await apiRequest(`/api/backups/?${params}`);
    if (r.ok) {
      const data: BackupJob[] = await r.json();
      setJobs(data);
    }
    setLoading(false);
  }, [scopeFilter, statusFilter]);

  useEffect(() => {
    setLoading(true);
    fetchJobs();
  }, [fetchJobs]);

  // Auto-poll while any job is pending or running
  useEffect(() => {
    const hasActive = jobs.some(
      (j) => j.status === "pending" || j.status === "running"
    );
    if (hasActive) {
      pollRef.current = setTimeout(fetchJobs, 4000);
    }
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [jobs, fetchJobs]);

  async function handleTrigger() {
    setTriggering(true);
    try {
      const r = await apiRequest("/api/backups/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: triggerScope }),
      });
      if (r.ok) {
        setShowTrigger(false);
        await fetchJobs();
      }
    } finally {
      setTriggering(false);
    }
  }

  const total = jobs.length;
  const completed = jobs.filter((j) => j.status === "completed").length;
  const failed = jobs.filter((j) => j.status === "failed").length;
  const active = jobs.filter(
    (j) => j.status === "pending" || j.status === "running"
  ).length;

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <DatabaseBackup className="h-5 w-5 text-slate-500" />
          <h1 className="text-xl font-semibold text-slate-900">Backups</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setLoading(true); fetchJobs(); }}
            className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
          <button
            onClick={() => setShowTrigger(!showTrigger)}
            className="flex items-center gap-1.5 rounded-md bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-800"
          >
            <Play className="h-3.5 w-3.5" />
            New backup
          </button>
        </div>
      </div>

      {/* Trigger panel */}
      {showTrigger && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="mb-3 text-sm font-semibold text-slate-800">Trigger a backup</p>
          <div className="flex items-center gap-3">
            <div className="flex gap-1">
              {(["workspace", "domain", "mailbox"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setTriggerScope(s)}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold capitalize transition ${
                    triggerScope === s
                      ? "bg-slate-950 text-white"
                      : "border border-slate-200 text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {SCOPE_LABELS[s]}
                </button>
              ))}
            </div>
            <button
              onClick={handleTrigger}
              disabled={triggering}
              className="flex items-center gap-1.5 rounded-md bg-cyan-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-cyan-700 disabled:opacity-50"
            >
              {triggering ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Play className="h-3.5 w-3.5" />
              )}
              Start
            </button>
            <button
              onClick={() => setShowTrigger(false)}
              className="text-xs text-slate-400 hover:text-slate-600"
            >
              Cancel
            </button>
          </div>
          <p className="mt-2 text-xs text-slate-400">
            <span className="font-semibold">Workspace</span> — all workspace configuration and settings.{" "}
            <span className="font-semibold">Domains</span> — DNS records and DKIM keys.{" "}
            <span className="font-semibold">Mailboxes</span> — mailbox metadata and quota snapshots.
          </p>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: "Total", value: total, icon: <HardDrive className="h-4 w-4 text-slate-400" /> },
          { label: "Completed", value: completed, icon: <CheckCircle2 className="h-4 w-4 text-emerald-500" /> },
          { label: "Failed", value: failed, icon: <AlertCircle className="h-4 w-4 text-red-500" /> },
          { label: "In progress", value: active, icon: <Timer className="h-4 w-4 text-blue-500" /> },
        ].map(({ label, value, icon }) => (
          <div key={label} className="rounded-lg border border-slate-200 bg-white px-4 py-3">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">{label}</p>
              {icon}
            </div>
            <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4">
        <div className="flex items-center gap-1">
          <span className="text-xs font-semibold text-slate-500 mr-1">Scope</span>
          {SCOPE_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => setScopeFilter(s)}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold capitalize transition ${
                scopeFilter === s
                  ? "bg-slate-950 text-white"
                  : "border border-slate-200 text-slate-500 hover:bg-slate-50"
              }`}
            >
              {s === "all" ? "All" : SCOPE_LABELS[s] ?? s}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <span className="text-xs font-semibold text-slate-500 mr-1">Status</span>
          {STATUS_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold capitalize transition ${
                statusFilter === s
                  ? "bg-slate-950 text-white"
                  : "border border-slate-200 text-slate-500 hover:bg-slate-50"
              }`}
            >
              {s === "all" ? "All" : STATUS_CONFIG[s]?.label ?? s}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      ) : jobs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 py-16 text-center">
          <DatabaseBackup className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm font-semibold text-slate-500">No backup jobs found</p>
          <p className="mt-1 text-xs text-slate-400">Trigger your first backup using the button above.</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Scope</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Size</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Duration</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Started</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Completed</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {jobs.map((job) => {
                const sc = STATUS_CONFIG[job.status];
                const isExpanded = expandedId === job.id;
                return (
                  <>
                    <tr
                      key={job.id}
                      className="cursor-pointer hover:bg-slate-50"
                      onClick={() => setExpandedId(isExpanded ? null : job.id)}
                    >
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${SCOPE_COLORS[job.scope] ?? ""}`}>
                          {SCOPE_LABELS[job.scope] ?? job.scope}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${sc.color}`}>
                          {sc.icon}
                          {sc.label}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-slate-700">
                        {formatSize(job.size_mb)}
                      </td>
                      <td className="px-4 py-3 text-slate-500">
                        {formatDuration(job.duration_seconds)}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-500">
                        {formatTime(job.started_at)}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-500">
                        {formatTime(job.completed_at)}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-400">
                        {isExpanded ? "▲" : "▼"}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${job.id}-detail`}>
                        <td colSpan={7} className="bg-slate-50 px-4 py-4">
                          <div className="grid gap-4 sm:grid-cols-2">
                            <div>
                              <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-400">
                                Storage location
                              </p>
                              <p className="font-mono text-xs text-slate-600">
                                {job.storage_location || "—"}
                              </p>
                              {job.error_message && (
                                <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2">
                                  <p className="text-xs font-semibold text-red-600">Error</p>
                                  <p className="mt-0.5 text-xs text-red-500">{job.error_message}</p>
                                </div>
                              )}
                            </div>
                            {Object.keys(job.restore_metadata).length > 0 && (
                              <div>
                                <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-400">
                                  Restore metadata
                                </p>
                                <pre className="overflow-auto rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                                  {JSON.stringify(job.restore_metadata, null, 2)}
                                </pre>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
