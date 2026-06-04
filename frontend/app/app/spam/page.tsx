"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { Shield, RefreshCw, CheckCircle2, Trash2 } from "lucide-react";

interface QuarantineMessage {
  id: string;
  engine_message_id: string;
  sender: string;
  recipient: string;
  subject: string;
  spam_score: string;
  status: string;
  status_display: string;
  received_at: string;
  actioned_at: string | null;
}

const STATUS_STYLES: Record<string, string> = {
  held:           "bg-amber-50  text-amber-700  ring-amber-600/20",
  released:       "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  deleted:        "bg-slate-100 text-slate-500  ring-slate-400/20",
  sender_blocked: "bg-red-50    text-red-700    ring-red-600/20",
};

const STATUSES = [
  { value: "",         label: "Held (default)" },
  { value: "held",     label: "Held" },
  { value: "released", label: "Released" },
  { value: "deleted",  label: "Deleted" },
];

function fmt(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" });
}

function spamScoreColor(score: string) {
  const n = parseFloat(score);
  if (n >= 15) return "text-red-600 font-semibold";
  if (n >= 8)  return "text-amber-600 font-medium";
  return "text-slate-500";
}

export default function SpamPage() {
  const [messages, setMessages] = useState<QuarantineMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [senderSearch, setSenderSearch] = useState("");
  const [senderInput, setSenderInput] = useState("");
  const [releasing, setReleasing] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  async function fetchQuarantine(sf: string, sender: string) {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (sf) params.set("status", sf);
      if (sender) params.set("sender", sender);
      const r = await apiRequest(`/api/quarantine/?${params}`);
      if (r.ok) setMessages(await r.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchQuarantine(statusFilter, senderSearch); }, [statusFilter, senderSearch]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleRelease(msg: QuarantineMessage) {
    setReleasing(msg.id);
    try {
      const r = await apiRequest(`/api/quarantine/${msg.id}/release/`, { method: "POST" });
      if (r.ok) {
        const updated = await r.json();
        setMessages((prev) => prev.map((m) => (m.id === msg.id ? updated : m)));
      }
    } finally {
      setReleasing(null);
    }
  }

  async function handleDelete(msg: QuarantineMessage) {
    setDeleting(msg.id);
    try {
      const r = await apiRequest(`/api/quarantine/${msg.id}/`, { method: "DELETE" });
      if (r.status === 204) {
        setMessages((prev) => prev.filter((m) => m.id !== msg.id));
      }
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Shield className="h-5 w-5 text-slate-400" />
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Spam &amp; Quarantine</h1>
            <p className="text-sm text-slate-500">Messages held by the spam filter. Release legitimate mail or delete spam.</p>
          </div>
        </div>
        <button
          onClick={() => fetchQuarantine(statusFilter, senderSearch)}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-slate-600">Status</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          >
            {STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1 flex-1 min-w-48">
          <label className="text-xs font-medium text-slate-600">Filter by sender</label>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="sender@example.com"
              value={senderInput}
              onChange={(e) => setSenderInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { setSenderSearch(senderInput); } }}
              className="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            />
            <button
              onClick={() => setSenderSearch(senderInput)}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              Search
            </button>
            {senderSearch && (
              <button
                onClick={() => { setSenderSearch(""); setSenderInput(""); }}
                className="text-xs text-slate-400 hover:text-slate-600 underline self-center"
              >
                Clear
              </button>
            )}
          </div>
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div className="py-16 text-center text-sm text-slate-400">Loading quarantine…</div>
      ) : messages.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 py-16 text-center">
          <Shield className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm text-slate-500">No quarantined messages.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full divide-y divide-slate-100 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">From</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">To</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Subject</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Score</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Received</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {messages.map((msg) => (
                <tr key={msg.id} className="hover:bg-slate-50/50">
                  <td className="px-4 py-3 text-slate-700 truncate max-w-[140px]">{msg.sender}</td>
                  <td className="px-4 py-3 text-slate-500 truncate max-w-[140px]">{msg.recipient}</td>
                  <td className="px-4 py-3 text-slate-500 truncate max-w-[180px]">{msg.subject || <span className="italic text-slate-300">no subject</span>}</td>
                  <td className={`px-4 py-3 font-mono text-xs ${spamScoreColor(msg.spam_score)}`}>{msg.spam_score}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset capitalize ${STATUS_STYLES[msg.status] ?? ""}`}>
                      {msg.status_display}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap">{fmt(msg.received_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-3">
                      {msg.status === "held" && (
                        <button
                          onClick={() => handleRelease(msg)}
                          disabled={releasing === msg.id}
                          title="Release to inbox"
                          className="inline-flex items-center gap-1 text-xs text-emerald-600 hover:text-emerald-700 disabled:opacity-40"
                        >
                          <CheckCircle2 className="h-4 w-4" />
                          Release
                        </button>
                      )}
                      {msg.status === "held" && (
                        <button
                          onClick={() => handleDelete(msg)}
                          disabled={deleting === msg.id}
                          title="Delete permanently"
                          className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-red-500 disabled:opacity-40"
                        >
                          <Trash2 className="h-4 w-4" />
                          Delete
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {messages.length > 0 && (
        <p className="text-xs text-slate-400">
          Showing up to 200 messages. Held messages older than 30 days are automatically deleted.
        </p>
      )}
    </div>
  );
}
