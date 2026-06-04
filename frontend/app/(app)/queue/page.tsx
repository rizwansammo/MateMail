"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { Clock, RefreshCw, XCircle } from "lucide-react";

interface QueueMessage {
  id: string;
  engine_message_id: string;
  sender: string;
  recipient: string;
  subject: string;
  status: string;
  status_display: string;
  reason: string;
  queued_at: string;
  last_retry: string | null;
  next_retry: string | null;
  retry_count: number;
}

const STATUS_STYLES: Record<string, string> = {
  pending:   "bg-blue-50   text-blue-700   ring-blue-600/20",
  deferred:  "bg-amber-50  text-amber-700  ring-amber-600/20",
  failed:    "bg-red-50    text-red-700    ring-red-600/20",
  delivered: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  cancelled: "bg-slate-100 text-slate-500  ring-slate-400/20",
};

const STATUSES = [
  { value: "", label: "Active (pending + deferred + failed)" },
  { value: "pending",   label: "Pending" },
  { value: "deferred",  label: "Deferred" },
  { value: "failed",    label: "Failed" },
  { value: "delivered", label: "Delivered" },
  { value: "cancelled", label: "Cancelled" },
];

function fmt(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" });
}

export default function QueuePage() {
  const [messages, setMessages] = useState<QueueMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [cancelling, setCancelling] = useState<string | null>(null);

  async function fetchQueue(sf: string) {
    setLoading(true);
    try {
      const params = sf ? `?status=${sf}` : "";
      const r = await apiRequest(`/api/queue/${params}`);
      if (r.ok) setMessages(await r.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchQueue(statusFilter); }, [statusFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleCancel(msg: QueueMessage) {
    setCancelling(msg.id);
    try {
      const r = await apiRequest(`/api/queue/${msg.id}/cancel/`, { method: "POST" });
      if (r.ok) {
        const updated = await r.json();
        setMessages((prev) => prev.map((m) => (m.id === msg.id ? updated : m)));
      }
    } finally {
      setCancelling(null);
    }
  }

  const canCancel = (s: string) => s === "pending" || s === "deferred" || s === "failed";

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Clock className="h-5 w-5 text-slate-400" />
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Mail Queue</h1>
            <p className="text-sm text-slate-500">Outbound messages waiting to be delivered or retried.</p>
          </div>
        </div>
        <button
          onClick={() => fetchQueue(statusFilter)}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-slate-600">Show:</label>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
        >
          {STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </div>

      {/* List */}
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">Loading queue…</div>
      ) : messages.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-16 text-center">
          <Clock className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm text-slate-500">Queue is empty.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full divide-y divide-slate-100 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">From</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">To</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Subject</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Queued</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Retries</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Next retry</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {messages.map((msg) => (
                <tr key={msg.id} className="hover:bg-slate-50/50">
                  <td className="px-4 py-3 text-slate-700 truncate max-w-[140px]">{msg.sender}</td>
                  <td className="px-4 py-3 text-slate-700 truncate max-w-[140px]">{msg.recipient}</td>
                  <td className="px-4 py-3 text-slate-500 truncate max-w-[200px]">{msg.subject || <span className="italic text-slate-300">no subject</span>}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[msg.status] ?? ""}`}>
                      {msg.status_display}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap">{fmt(msg.queued_at)}</td>
                  <td className="px-4 py-3 text-center text-slate-500">{msg.retry_count}</td>
                  <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap">{fmt(msg.next_retry)}</td>
                  <td className="px-4 py-3 text-right">
                    {canCancel(msg.status) && (
                      <button
                        onClick={() => handleCancel(msg)}
                        disabled={cancelling === msg.id}
                        title="Cancel message"
                        className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-red-500 disabled:opacity-40"
                      >
                        <XCircle className="h-4 w-4" />
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {messages.length > 0 && (
        <p className="text-xs text-slate-400">
          Showing up to 200 messages. Delivered and cancelled messages are retained for 30 days.
        </p>
      )}
    </div>
  );
}
