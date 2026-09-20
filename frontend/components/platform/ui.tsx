"use client";

/**
 * The console's shared parts.
 *
 * Small and deliberately plain. An operations console is scanned, not read, so
 * these favour density and a consistent shape over decoration: one badge
 * vocabulary, one table, one empty state, one confirmation dialog that always
 * asks for a reason before anything destructive happens.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Inbox, Loader2, RefreshCw, Search, X } from "lucide-react";

// ── status vocabulary ───────────────────────────────────────────────────────

type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

/**
 * One mapping from a backend status string to a colour, used by every table.
 *
 * Centralised so "suspended" is the same red on the organizations page and the
 * mailboxes page. Anything unrecognised is neutral rather than green — an
 * unknown state must never read as healthy.
 */
const TONES: Record<string, Tone> = {
  active: "ok",
  ok: "ok",
  approved: "ok",
  verified: "ok",
  delivered: "ok",
  released: "ok",
  completed: "ok",

  trial: "info",
  pending: "warn",
  pending_approval: "warn",
  held: "warn",
  deferred: "warn",
  paused: "warn",
  unknown: "warn",

  suspended: "danger",
  rejected: "danger",
  disabled: "danger",
  failed: "danger",
  error: "danger",
  cancelled: "neutral",
  deleted: "neutral",
  inactive: "neutral",
};

export function toneFor(status: string | null | undefined): Tone {
  if (!status) return "neutral";
  return TONES[status.toLowerCase()] ?? "neutral";
}

const TONE_STYLE: Record<Tone, { background: string; color: string }> = {
  ok: { background: "var(--pf-ok-soft)", color: "var(--pf-ok)" },
  warn: { background: "var(--pf-warn-soft)", color: "var(--pf-warn)" },
  danger: { background: "var(--pf-danger-soft)", color: "var(--pf-danger)" },
  info: { background: "var(--pf-info-soft)", color: "var(--pf-info)" },
  neutral: { background: "var(--pf-neutral-soft)", color: "var(--pf-neutral)" },
};

export function Badge({
  children,
  tone,
  status,
}: {
  children?: React.ReactNode;
  tone?: Tone;
  status?: string | null;
}) {
  const resolved = tone ?? toneFor(status);
  return (
    <span className="pf-badge" style={TONE_STYLE[resolved]}>
      {children ?? humanise(status ?? "")}
    </span>
  );
}

export function humanise(value: string): string {
  if (!value) return "—";
  return value.replace(/[_.]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── page furniture ──────────────────────────────────────────────────────────

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold" style={{ color: "var(--pf-text)" }}>
          {title}
        </h1>
        {description && (
          <p className="mt-1 max-w-2xl text-sm pf-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  children,
  className = "",
  padded = true,
}: {
  children: React.ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <div className={`pf-card ${padded ? "p-4" : ""} ${className}`}>{children}</div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: Tone;
}) {
  return (
    <div className="pf-card p-4">
      <p className="pf-label">{label}</p>
      <p
        className="pf-num mt-1.5 text-2xl font-semibold"
        style={{ color: tone ? TONE_STYLE[tone].color : "var(--pf-text)" }}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 text-xs pf-faint">{hint}</p>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div
      className="flex items-center justify-center gap-2 py-10 text-sm pf-muted"
      role="status"
    >
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      {label}…
    </div>
  );
}

export function EmptyState({
  title,
  detail,
  icon: Icon = Inbox,
}: {
  title: string;
  detail?: string;
  icon?: typeof Inbox;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-12 text-center">
      <Icon className="h-6 w-6" style={{ color: "var(--pf-text-faint)" }} aria-hidden="true" />
      <p className="text-sm font-medium" style={{ color: "var(--pf-text)" }}>
        {title}
      </p>
      {detail && <p className="max-w-sm text-xs pf-muted">{detail}</p>}
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-lg px-3 py-2.5 text-sm"
      role="alert"
      style={{ background: "var(--pf-danger-soft)", color: "var(--pf-danger)" }}
    >
      <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button type="button" className="pf-btn pf-btn-ghost" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          Retry
        </button>
      )}
    </div>
  );
}

export function SuccessNote({ message }: { message: string }) {
  return (
    <div
      className="rounded-lg px-3 py-2.5 text-sm"
      role="status"
      style={{ background: "var(--pf-ok-soft)", color: "var(--pf-ok)" }}
    >
      {message}
    </div>
  );
}

/**
 * A note about something the application genuinely cannot determine.
 *
 * Distinct from an error on purpose. "We could not check this" and "this is
 * broken" are different facts, and a console that renders them identically
 * teaches operators to ignore both.
 */
export function UnknownNote({ message }: { message: string }) {
  return (
    <div
      className="rounded-lg px-3 py-2.5 text-sm"
      style={{ background: "var(--pf-warn-soft)", color: "var(--pf-warn)" }}
    >
      {message}
    </div>
  );
}

// ── filters ─────────────────────────────────────────────────────────────────

export function SearchInput({
  value,
  onChange,
  placeholder = "Search…",
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="relative min-w-0 flex-1 sm:max-w-xs">
      <Search
        className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
        style={{ color: "var(--pf-text-faint)" }}
        aria-hidden="true"
      />
      <input
        id="pf-search"
        className="pf-input"
        style={{ paddingLeft: "1.9rem" }}
        value={value}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

export function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  const id = `pf-filter-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="pf-label whitespace-nowrap">
        {label}
      </label>
      <select
        id={id}
        className="pf-select"
        style={{ width: "auto" }}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function Toolbar({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">{children}</div>
  );
}

// ── tables ──────────────────────────────────────────────────────────────────

export function DataTable({
  columns,
  children,
}: {
  columns: string[];
  children: React.ReactNode;
}) {
  return (
    // Wide operational tables scroll inside their own container so the page
    // body never scrolls sideways on a phone.
    <div className="pf-scroll-x pf-card" style={{ overflow: "auto" }}>
      <table className="pf-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Pagination({
  page,
  pageSize,
  total,
  hasNext,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  hasNext: boolean;
  onPage: (next: number) => void;
}) {
  if (total === 0) return null;
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs pf-muted">
      <span className="pf-num">
        {first}–{last} of {total.toLocaleString()}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="pf-btn pf-btn-ghost"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </button>
        <button
          type="button"
          className="pf-btn pf-btn-ghost"
          disabled={!hasNext}
          onClick={() => onPage(page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
}

// ── confirmation ────────────────────────────────────────────────────────────

/**
 * The dialog in front of every state-changing action.
 *
 * It always collects a reason. The backend requires one for the oversight
 * actions, and the reason is what the audit trail is read for six months later
 * — "who suspended this" is usually answerable from the row; "why" is not.
 */
interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  requireReason?: boolean;
  busy?: boolean;
  error?: string | null;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

/**
 * Mounts the dialog only while it is open.
 *
 * That is what clears the reason between two different actions: the inner
 * component's state goes away with it. The earlier version kept the component
 * mounted and blanked the field from an effect, which both tripped
 * react-hooks/set-state-in-effect and, more importantly, meant a reason typed
 * for one organization could survive into the dialog for another.
 */
export function ConfirmDialog(props: ConfirmDialogProps) {
  if (!props.open) return null;
  return <ConfirmDialogBody {...props} />;
}

function ConfirmDialogBody({
  title,
  body,
  confirmLabel,
  destructive = false,
  requireReason = true,
  busy = false,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [reason, setReason] = useState("");
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    // Focus lands where the operator has to type.
    const timer = window.setTimeout(() => inputRef.current?.focus(), 30);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  const blocked = busy || (requireReason && reason.trim().length === 0);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center p-4 sm:items-center"
      style={{ background: "rgb(2 6 23 / 0.55)" }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="pf-card w-full max-w-md p-4">
        <div className="mb-2 flex items-start justify-between gap-3">
          <h2 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            {title}
          </h2>
          <button
            type="button"
            aria-label="Close"
            className="pf-btn pf-btn-ghost"
            style={{ padding: "0.2rem 0.35rem" }}
            onClick={onCancel}
            disabled={busy}
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>

        <div className="mb-3 text-sm pf-muted">{body}</div>

        {requireReason && (
          <div className="mb-3">
            <label htmlFor="pf-reason" className="pf-label">
              Reason (recorded in the audit log)
            </label>
            <textarea
              id="pf-reason"
              ref={inputRef}
              className="pf-input mt-1"
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Ticket reference or short explanation"
            />
          </div>
        )}

        {error && (
          <p className="mb-3 text-xs" style={{ color: "var(--pf-danger)" }} role="alert">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="pf-btn pf-btn-ghost"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className={`pf-btn ${destructive ? "pf-btn-danger" : "pf-btn-primary"}`}
            onClick={() => onConfirm(reason.trim())}
            disabled={blocked}
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── formatting ──────────────────────────────────────────────────────────────

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";

  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return formatDateTime(value);
}

export function formatBytes(megabytes: number): string {
  if (!megabytes) return "0 MB";
  if (megabytes < 1024) return `${megabytes.toLocaleString()} MB`;
  return `${(megabytes / 1024).toFixed(1)} GB`;
}

/** Debounce a changing value, so typing in a filter does not fire a request per keystroke. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/**
 * The fetch-list-with-filters pattern every table page uses.
 *
 * Returns the data, the loading and error states, and a `reload` the action
 * handlers call so a table refreshes the moment something changes rather than
 * leaving a stale row on screen.
 */
export function useListData<T>(
  loader: () => Promise<T>,
  deps: React.DependencyList,
): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [nonce, setNonce] = useState(0);

  // One key per request. Loading is then *derived* — "the answer on screen is
  // not the answer to the question currently being asked" — instead of being a
  // third piece of state set synchronously at the top of an effect.
  const key = `${JSON.stringify(deps)}::${nonce}`;
  const [settled, setSettled] = useState<{
    key: string;
    data: T | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;

    loader()
      .then((result) => {
        if (!cancelled) setSettled({ key, data: result, error: null });
      })
      .catch(() => {
        if (!cancelled) {
          setSettled({
            key,
            data: null,
            error: "Could not load this data. Please try again.",
          });
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const loading = settled.key !== key;

  return {
    // While a new request is in flight the previous answer is withheld, so a
    // table never shows one organization's rows under another's filter.
    data: loading ? null : settled.data,
    error: loading ? null : settled.error,
    loading,
    reload,
  };
}
