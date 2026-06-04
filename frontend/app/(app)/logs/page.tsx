"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { FileText, ChevronLeft, ChevronRight } from "lucide-react";

interface LogEntry {
  id: string;
  event_type: string;
  event_type_display: string;
  source: string;
  ip_address: string | null;
  result: string;
  result_display: string;
  domain_name: string | null;
  mailbox_email: string | null;
  created_at: string;
}

const RESULT_STYLES: Record<string, string> = {
  success: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  warning: "bg-amber-50   text-amber-700   ring-amber-600/20",
  blocked: "bg-orange-50  text-orange-700  ring-orange-600/20",
  failed:  "bg-red-50     text-red-700     ring-red-600/20",
};

const EVENT_TYPES = [
  { value: "", label: "All events" },
  { value: "mailbox_created",   label: "Mailbox created" },
  { value: "mailbox_disabled",  label: "Mailbox disabled" },
  { value: "mailbox_deleted",   label: "Mailbox deleted" },
  { value: "domain_added",      label: "Domain added" },
  { value: "domain_deleted",    label: "Domain deleted" },
  { value: "domain_verified",   label: "Domain verified" },
  { value: "dns_check_pass",    label: "DNS check passed" },
  { value: "dns_check_fail",    label: "DNS check failed" },
  { value: "smtp_msg_accepted", label: "Message accepted" },
  { value: "smtp_msg_rejected", label: "Message rejected" },
  { value: "smtp_auth_fail",    label: "SMTP auth failed" },
  { value: "tenant_suspended",  label: "Workspace suspended" },
  { value: "tenant_reactivated","label": "Workspace reactivated" },
  { value: "user_login",        label: "User login" },
  { value: "login_failed",      label: "Login failed" },
  { value: "role_changed",      label: "Role changed" },
];

const RESULTS = [
  { value: "", label: "All results" },
  { value: "success", label: "Success" },
  { value: "warning", label: "Warning" },
  { value: "blocked", label: "Blocked" },
  { value: "failed",  label: "Failed" },
];

function fmt(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" });
}

export default function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [numPages, setNumPages] = useState(1);
  const [count, setCount] = useState(0);

  const [eventType, setEventType] = useState("");
  const [result, setResult] = useState("");
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");

  async function fetchLogs(p: number, et: string, res: string, src: string) {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p) });
      if (et)  params.set("event_type", et);
      if (res) params.set("result", res);
      if (src) params.set("search", src);
      const r = await apiRequest(`/api/logs/?${params}`);
      if (r.ok) {
        const data = await r.json();
        setLogs(data.results);
        setNumPages(data.num_pages);
        setCount(data.count);
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchLogs(page, eventType, result, search); }, [page, eventType, result, search]); // eslint-disable-line react-hooks/exhaustive-deps

  function applyFilters() {
    setSearch(searchInput);
    setPage(1);
  }

  function clearFilters() {
    setEventType(""); setResult(""); setSearch(""); setSearchInput(""); setPage(1);
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <FileText className="h-5 w-5 text-slate-400" />
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Activity Logs</h1>
          <p className="text-sm text-slate-500">Audit trail for your workspace — domains, mailboxes, logins, and more.</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-slate-600">Event type</label>
          <select
            value={eventType}
            onChange={(e) => { setEventType(e.target.value); setPage(1); }}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          >
            {EVENT_TYPES.map((et) => <option key={et.value} value={et.value}>{et.label}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-slate-600">Result</label>
          <select
            value={result}
            onChange={(e) => { setResult(e.target.value); setPage(1); }}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          >
            {RESULTS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1 flex-1 min-w-48">
          <label className="text-xs font-medium text-slate-600">Source (email / system)</label>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="Search by source…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applyFilters()}
              className="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            />
            <button
              onClick={applyFilters}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              Search
            </button>
          </div>
        </div>

        {(eventType || result || search) && (
          <button onClick={clearFilters} className="text-xs text-slate-400 hover:text-slate-600 underline self-end pb-1.5">
            Clear filters
          </button>
        )}
      </div>

      {/* Count */}
      {!loading && (
        <p className="text-xs text-slate-400">{count} event{count !== 1 ? "s" : ""} found</p>
      )}

      {/* Table */}
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">Loading logs…</div>
      ) : logs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-16 text-center">
          <FileText className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm text-slate-500">No log entries match your filters.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full divide-y divide-slate-100 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Time</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Event</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Source</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Target</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Result</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">IP</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {logs.map((log) => (
                <tr key={log.id} className="hover:bg-slate-50/50">
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500 font-mono">{fmt(log.created_at)}</td>
                  <td className="px-4 py-3 text-slate-800">{log.event_type_display}</td>
                  <td className="px-4 py-3 text-slate-500 truncate max-w-[160px]">{log.source || "—"}</td>
                  <td className="px-4 py-3 text-slate-500 truncate max-w-[160px]">
                    {log.mailbox_email || log.domain_name || "—"}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${RESULT_STYLES[log.result] ?? ""}`}>
                      {log.result_display}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-400">{log.ip_address || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {numPages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-3 py-1.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" /> Prev
          </button>
          <span className="text-slate-500">Page {page} of {numPages}</span>
          <button
            onClick={() => setPage((p) => Math.min(numPages, p + 1))}
            disabled={page === numPages}
            className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-3 py-1.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40"
          >
            Next <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}
