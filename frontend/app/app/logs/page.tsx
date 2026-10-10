"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Globe2, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { apiRequest } from "@/lib/api";
import {
  PortalCard,
  PortalEmptyState,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
  PortalStatus,
} from "@/components/workspace/premium-ui";

interface AuditEvent {
  id: string;
  event_type: string;
  source: string;
  result: string;
  ip_address: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

function pretty(value?: string | null) {
  if (!value) return "Unknown";
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function targetFor(event: AuditEvent) {
  const meta = event.metadata || {};
  const candidates = [
    meta.domain,
    meta.mailbox,
    meta.email,
    meta.target,
    meta.name,
    meta.key_prefix,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value) return value;
  }
  return event.source || "Workspace";
}

function metadataSummary(event: AuditEvent) {
  const meta = event.metadata || {};
  const keys = Object.keys(meta).filter((key) => {
    const value = meta[key];
    return value !== null && value !== undefined && value !== "";
  });
  if (!keys.length) return "";
  return keys.slice(0, 3).map((key) => {
    const value = meta[key];
    if (Array.isArray(value)) return pretty(key) + ": " + value.join(", ");
    if (typeof value === "object") return pretty(key);
    return pretty(key) + ": " + String(value);
  }).join(" · ");
}

export default function LogsPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [resultFilter, setResultFilter] = useState("");

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await apiRequest("/api/logs/");
      const data = await response.json().catch(() => null);
      if (response.ok && Array.isArray(data)) {
        setEvents(data);
      } else if (response.status === 403) {
        setLoadError("Your workspace role does not permit access to activity logs.");
      } else {
        setLoadError(data?.detail ?? "Activity logs could not be loaded.");
      }
    } catch {
      setLoadError("Activity logs could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchEvents(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchEvents]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return events.filter((event) => {
      const resultMatches = !resultFilter || event.result.toLowerCase() === resultFilter;
      if (!resultMatches) return false;
      if (!needle) return true;
      return [
        event.event_type,
        event.source,
        event.result,
        event.ip_address || "",
        targetFor(event),
        metadataSummary(event),
      ].some((value) => value.toLowerCase().includes(needle));
    });
  }, [events, query, resultFilter]);

  const resultOptions = useMemo(
    () => Array.from(new Set(events.map((event) => event.result).filter(Boolean))).sort(),
    [events]
  );

  return (
    <div className="portal-page astra-resource-page astra-advanced-page astra-operations-page">
      <PortalPageHeading
        title="Activity logs"
        description="A tenant-scoped record of important workspace and security events."
        actions={
          <button type="button" className="portal-button secondary" onClick={fetchEvents} disabled={loading}>
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
            Refresh
          </button>
        }
      />

      <div className="mb-5">
        <PortalNotice tone="info">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Logs are read-only. MateMail returns up to the latest 200 tenant events and never exposes another workspace’s records.</span>
        </PortalNotice>
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
              placeholder="Search activity…"
              aria-label="Search activity logs"
            />
          </div>
          <select
            className="portal-input !h-[34px] !min-h-[34px] !w-[150px] !py-0 text-[10px]"
            value={resultFilter}
            onChange={(event) => setResultFilter(event.target.value)}
            aria-label="Filter activity result"
          >
            <option value="">All results</option>
            {resultOptions.map((result) => (
              <option key={result} value={result.toLowerCase()}>{pretty(result)}</option>
            ))}
          </select>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : !loadError && filtered.length === 0 ? (
          <PortalEmptyState
            title={events.length ? "No matching activity" : "No activity yet"}
            description={events.length ? "Try a different search or result filter." : "Important workspace events will appear here as they occur."}
          />
        ) : !loadError ? (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Target</th>
                  <th>Result</th>
                  <th>Source</th>
                  <th>IP address</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <div className="portal-identity-cell">
                        <span className="portal-avatar"><Activity className="h-4 w-4" /></span>
                        <span>
                          <strong>{pretty(event.event_type)}</strong>
                          <small>{metadataSummary(event) || "Recorded by MateMail"}</small>
                        </span>
                      </div>
                    </td>
                    <td><code>{targetFor(event)}</code></td>
                    <td><PortalStatus value={pretty(event.result || "Recorded")} /></td>
                    <td>{event.source || "MateMail"}</td>
                    <td>
                      <span className="inline-flex items-center gap-1.5">
                        <Globe2 className="h-3.5 w-3.5 text-[var(--portal-faint)]" />
                        {event.ip_address || "—"}
                      </span>
                    </td>
                    <td>{new Date(event.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </PortalCard>
    </div>
  );
}
