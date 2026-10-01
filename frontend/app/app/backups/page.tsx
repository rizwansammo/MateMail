"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  DatabaseBackup,
  RefreshCw,
  Search,
  XCircle,
} from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalCard,
  PortalEmptyState,
  PortalMetric,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

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

function formatSize(mb: number) {
  if (!mb) return "—";
  if (mb >= 1024) {
    const gb = mb / 1024;
    return (Number.isInteger(gb) ? gb : gb.toFixed(1)) + " GB";
  }
  return mb + " MB";
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return "—";
  if (seconds < 60) return seconds + "s";
  return Math.floor(seconds / 60) + "m " + (seconds % 60) + "s";
}

function fmt(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export default function BackupsPage() {
  const [jobs, setJobs] = useState<BackupJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [scopeFilter, setScopeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const params = new URLSearchParams();
      if (scopeFilter) params.set("scope", scopeFilter);
      if (statusFilter) params.set("status", statusFilter);
      const suffix = params.toString() ? "?" + params.toString() : "";
      const response = await apiRequest("/api/backups/" + suffix);
      const data = await response.json().catch(() => null);
      if (response.ok && Array.isArray(data)) {
        setJobs(data);
      } else if (response.status === 403) {
        setLoadError("Your workspace role does not permit access to backup job history.");
      } else {
        setLoadError(data?.detail ?? "Backup job history could not be loaded.");
      }
    } catch {
      setLoadError("Backup job history could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [scopeFilter, statusFilter]);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchJobs(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchJobs]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return jobs;
    return jobs.filter((job) =>
      [
        job.id,
        job.scope,
        job.status,
        job.storage_location,
        job.error_message,
      ].some((value) => value.toLowerCase().includes(needle))
    );
  }, [jobs, query]);

  const completed = jobs.filter((job) => job.status === "completed").length;
  const failed = jobs.filter((job) => job.status === "failed").length;
  const active = jobs.filter((job) => job.status === "pending" || job.status === "running").length;

  return (
    <div className="portal-page">
      <PortalPageHeading
        title="Backups"
        description="Visibility into organization-level backup requests recorded by MateMail."
        actions={
          <button type="button" className="portal-button secondary" onClick={fetchJobs} disabled={loading}>
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
            Refresh
          </button>
        }
      />

      <div className="mb-5">
        <PortalNotice tone="warn">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            <strong>Per-organization backup export is not implemented yet.</strong> MateMail’s real disaster-recovery backups currently run at the platform level and are not exposed as downloadable tenant archives. This page therefore does not offer a “Start backup” action.
          </span>
        </PortalNotice>
      </div>

      <div className="portal-metrics-grid">
        <PortalMetric label="Recorded jobs" value={jobs.length} detail="Latest tenant backup requests" icon={<DatabaseBackup className="h-4 w-4" />} />
        <PortalMetric label="Completed" value={completed} detail="Jobs that report a completed archive" icon={<CheckCircle2 className="h-4 w-4" />} />
        <PortalMetric label="Failed" value={failed} detail="Requests that did not create a tenant archive" icon={<XCircle className="h-4 w-4" />} />
        <PortalMetric label="In progress" value={active} detail="Pending or running job records" icon={<RefreshCw className="h-4 w-4" />} />
      </div>

      {loadError && (
        <div className="mb-5"><PortalNotice tone="danger">{loadError}</PortalNotice></div>
      )}

      <PortalCard className="portal-management-card" bodyClassName="!p-0">
        <div className="portal-management-toolbar">
          <div className="portal-management-search">
            <Search className="h-4 w-4" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search backup jobs…"
              aria-label="Search backup jobs"
            />
          </div>
          <div className="portal-inline-actions">
            <select
              className="portal-input !h-[34px] !min-h-[34px] !w-[125px] !py-0 text-[10px]"
              value={scopeFilter}
              onChange={(event) => setScopeFilter(event.target.value)}
              aria-label="Filter backup scope"
            >
              <option value="">All scopes</option>
              <option value="workspace">Workspace</option>
              <option value="domain">Domain</option>
              <option value="mailbox">Mailbox</option>
            </select>
            <select
              className="portal-input !h-[34px] !min-h-[34px] !w-[130px] !py-0 text-[10px]"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              aria-label="Filter backup status"
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : !loadError && filtered.length === 0 ? (
          <PortalEmptyState
            title={jobs.length ? "No matching backup jobs" : "No backup jobs recorded"}
            description={jobs.length ? "Try a different filter or search term." : "Tenant backup requests will appear here if the backend records them."}
          />
        ) : !loadError ? (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Scope</th>
                  <th>Status</th>
                  <th>Size</th>
                  <th>Duration</th>
                  <th>Created</th>
                  <th>Completed</th>
                  <th>Storage location</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <div className="portal-identity-cell">
                        <span className="portal-avatar"><DatabaseBackup className="h-4 w-4" /></span>
                        <span>
                          <strong>{job.scope.charAt(0).toUpperCase() + job.scope.slice(1)}</strong>
                          <small>{job.id}</small>
                        </span>
                      </div>
                    </td>
                    <td>
                      <PortalStatus value={job.status.charAt(0).toUpperCase() + job.status.slice(1)} />
                      {job.error_message && <div className="portal-table-note portal-table-error">{job.error_message}</div>}
                    </td>
                    <td>{formatSize(job.size_mb)}</td>
                    <td>{formatDuration(job.duration_seconds)}</td>
                    <td>{fmt(job.created_at)}</td>
                    <td>{fmt(job.completed_at)}</td>
                    <td>
                      {job.storage_location ? <code>{job.storage_location}</code> : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </PortalCard>

      <div className="mt-4">
        <PortalNotice tone="info">
          <DatabaseBackup className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Existing failed tenant-backup requests remain visible so operators and workspace admins can see that no archive was created. Platform disaster-recovery backups are a separate system.</span>
        </PortalNotice>
      </div>
    </div>
  );
}
