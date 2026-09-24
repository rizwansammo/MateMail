"use client";

/**
 * The mailbox: list, reading pane and the actions over both.
 *
 * One route rather than a route per folder. A mail client is a single view
 * whose state is "which folder, which message, which search" — putting that in
 * the query string means the browser's back button, a bookmark and a shared
 * link all work, without a navigation between every message.
 *
 * Message HTML is rendered with `dangerouslySetInnerHTML`, and that is safe
 * for exactly one reason: the server sanitised it with a real HTML parser
 * before it was sent. Nothing is cleaned here — a second, weaker sanitiser in
 * the browser would be the one people trusted.
 */
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Archive,
  ArrowLeft,
  CheckCircle2,
  CornerUpLeft,
  CornerUpRight,
  Download,
  Eye,
  Forward,
  ImageOff,
  Loader2,
  MailOpen,
  Paperclip,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Star,
  Trash2,
  X,
} from "lucide-react";

import { Compose, type ComposeInitial } from "@/components/postbox/compose";
import { useAsyncData } from "@/components/postbox/use-async";
import { describePostBoxError, usePostBox } from "@/contexts/postbox-context";
import {
  formatBytes,
  formatMessageDate,
  postbox,
  type MessageDetail,
  type MessagePage,
  type MessageSummary,
} from "@/lib/postbox-api";

export default function MailPage() {
  return (
    <Suspense fallback={<CentredSpinner />}>
      <Mailbox />
    </Suspense>
  );
}

/** Stable, so a derived empty selection does not change identity each render. */
const EMPTY_SELECTION: Set<number> = new Set();

function CentredSpinner() {
  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
    </div>
  );
}

function Mailbox() {
  const router = useRouter();
  const params = useSearchParams();
  const { preferences } = usePostBox();

  const folder = params.get("folder") || "INBOX";
  const starredOnly = params.get("starred") === "true";

  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");

  // One key for "which list am I looking at". Paging and selection both reset
  // when it changes, and both derive that from the key rather than having an
  // effect write it — a reset is a consequence of the key, not an event.
  const listKey = `${folder}|${search}|${starredOnly}`;

  const [pageState, setPageState] = useState({ key: listKey, page: 1 });
  const pageNumber = pageState.key === listKey ? pageState.page : 1;
  const setPageNumber = useCallback(
    (next: number | ((current: number) => number)) =>
      setPageState((current) => {
        const base = current.key === listKey ? current.page : 1;
        return {
          key: listKey,
          page: typeof next === "function" ? next(base) : next,
        };
      }),
    [listKey],
  );

  const [selectionState, setSelectionState] = useState<{
    key: string;
    uids: Set<number>;
  }>({ key: listKey, uids: new Set() });
  const selected =
    selectionState.key === listKey ? selectionState.uids : EMPTY_SELECTION;
  const setSelected = useCallback(
    (uids: Set<number>) => setSelectionState({ key: listKey, uids }),
    [listKey],
  );
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showRemote, setShowRemote] = useState(false);

  const [explicitCompose, setExplicitCompose] = useState<ComposeInitial | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [successNotice, setSuccessNotice] = useState<string | null>(null);
  const [successVisible, setSuccessVisible] = useState(false);
  const successToastRef = useRef<HTMLDivElement | null>(null);
  const [busy, setBusy] = useState(false);

  // Debounce the search box so typing does not run an IMAP SEARCH per keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [query]);

  const list = useAsyncData<MessagePage>(
    () =>
      postbox.messages({
        folder,
        page: pageNumber,
        q: search || undefined,
        starred: starredOnly ? "true" : undefined,
      }),
    [folder, pageNumber, search, starredOnly],
    "Your mail could not be loaded.",
  );

  const page = list.data;
  const loading = list.loading;
  const listError = list.error;
  const loadList = list.reload;

  const directory = useAsyncData(
    () => Promise.all([postbox.identities(), postbox.signatures()]),
    [],
    "",
  );
  const identities = directory.data?.[0]?.results ?? [];
  const signatures = directory.data?.[1]?.results ?? [];

  // The composer opened from the rail's link is read from the query string
  // rather than copied into state, so no effect writes state on mount.
  const composeRequested = params.get("compose") === "new";
  const compose = explicitCompose ?? (composeRequested ? { mode: "new" as const } : null);

  const closeCompose = useCallback(() => {
    setExplicitCompose(null);
    if (composeRequested) {
      router.replace(`/postbox?folder=${encodeURIComponent(folder)}`);
    }
  }, [composeRequested, folder, router]);

  const dismissSuccess = useCallback(() => {
    setSuccessVisible(false);
    window.setTimeout(() => setSuccessNotice(null), 180);
  }, []);

  useEffect(() => {
    if (!successNotice) return;

    const autoDismiss = window.setTimeout(dismissSuccess, 6000);
    const dismissOnOutsideClick = (event: PointerEvent) => {
      const toast = successToastRef.current;
      if (toast && !toast.contains(event.target as Node)) dismissSuccess();
    };

    document.addEventListener("pointerdown", dismissOnOutsideClick);
    return () => {
      window.clearTimeout(autoDismiss);
      document.removeEventListener("pointerdown", dismissOnOutsideClick);
    };
  }, [dismissSuccess, successNotice]);

  const openMessage = useCallback(
    async (summary: MessageSummary, remote = false) => {
      setDetailLoading(true);
      setShowRemote(remote);
      try {
        const data = await postbox.message(summary.folder, summary.uid, remote);
        setDetail(data);
        // Marking read is a separate, explicit call — the list does not mark
        // things seen as it scrolls past them.
        if (!summary.seen) {
          await postbox.act("read", summary.folder, [summary.uid]);
          void loadList();
        }
      } catch (caught) {
        setNotice(describePostBoxError(caught, "That message could not be opened."));
      } finally {
        setDetailLoading(false);
      }
    },
    [loadList],
  );

  const act = useCallback(
    async (action: Parameters<typeof postbox.act>[0], uids?: number[], extra = {}) => {
      const targets = uids ?? Array.from(selected);
      if (targets.length === 0) return;
      setBusy(true);
      try {
        await postbox.act(action, folder, targets, extra);
        setSelected(new Set());
        if (detail && targets.includes(detail.uid)) setDetail(null);
        await loadList();
        setNotice(null);
      } catch (caught) {
        setNotice(describePostBoxError(caught, "That action could not be completed."));
      } finally {
        setBusy(false);
      }
    },
    // `setSelected` is deliberately absent, and this is the one place it is
    // omitted. Adding it — or writing the state inline instead — makes the
    // React Compiler give up memoizing this callback entirely, which is a
    // real cost for a hypothetical one: a stale wrapper writes the empty set
    // under the previous list key, and a selection stored under any key but
    // the current one already reads back as empty. Either way the selection
    // is cleared, which is the whole intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selected, folder, detail, loadList],
  );

  const openReply = useCallback(
    async (mode: "reply" | "reply-all" | "forward") => {
      if (!detail) return;
      try {
        const context = await postbox.replyContext(detail.folder, detail.uid, mode);
        setExplicitCompose({
          mode,
          to: context.to,
          cc: context.cc,
          subject: context.subject,
          text: context.text,
          from_address: context.from_address,
          in_reply_to: context.in_reply_to,
          references: context.references,
        });
      } catch (caught) {
        setNotice(describePostBoxError(caught, "That reply could not be prepared."));
      }
    },
    [detail],
  );

  const rows = page?.results ?? [];
  const allSelected = rows.length > 0 && selected.size === rows.length;
  const paneRight = preferences.reading_pane === "right";

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ── toolbar ────────────────────────────────────────────────────── */}
      <div
        className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2"
        style={{ borderColor: "var(--pb-border)", background: "var(--pb-bg)" }}
      >
        <input
          type="checkbox"
          aria-label="Select all messages"
          checked={allSelected}
          onChange={(event) =>
            setSelected(
              event.target.checked ? new Set(rows.map((r) => r.uid)) : new Set(),
            )
          }
        />

        <button
          type="button"
          className="pb-btn pb-btn-plain"
          aria-label="Refresh"
          onClick={() => void loadList()}
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
        </button>

        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <ToolbarButton label="Mark read" icon={MailOpen} busy={busy}
              onClick={() => void act("read")} />
            <ToolbarButton label="Star" icon={Star} busy={busy}
              onClick={() => void act("star")} />
            <ToolbarButton label="Archive" icon={Archive} busy={busy}
              onClick={() => void act("archive")} />
            <ToolbarButton label="Spam" icon={ShieldAlert} busy={busy}
              onClick={() => void act("spam")} />
            <ToolbarButton label="Delete" icon={Trash2} busy={busy}
              onClick={() => void act("trash")} />
            <span className="ml-1 text-xs pb-subtle pb-num">
              {selected.size} selected
            </span>
          </div>
        )}

        <div className="relative ml-auto min-w-0 flex-1 sm:max-w-xs">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
            style={{ color: "var(--pb-subtle)" }}
            aria-hidden="true"
          />
          <input
            className="pb-input"
            style={{ paddingLeft: "1.9rem" }}
            placeholder={`Search ${folder}`}
            aria-label="Search mail"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>

      {notice && (
        <div
          className="shrink-0 px-3 py-2 text-xs"
          role="status"
          style={{ background: "var(--pb-warn-soft)", color: "var(--pb-warn)" }}
        >
          {notice}
        </div>
      )}

      {successNotice && (
        <div
          ref={successToastRef}
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className={`fixed left-1/2 top-1/2 z-[70] flex max-w-[min(90vw,30rem)] -translate-x-1/2 -translate-y-1/2 items-center gap-2 border px-3 py-2 text-sm shadow-2xl transition-all duration-200 ${
            successVisible ? "scale-100 opacity-100" : "scale-95 opacity-0"
          }`}
          style={{
            background: "var(--pb-success-soft)",
            borderColor: "var(--pb-success)",
            color: "var(--pb-fg)",
            borderLeftWidth: "3px",
            boxShadow: "0 16px 42px rgb(0 0 0 / 0.28)",
          }}
        >
          <CheckCircle2
            className="h-4 w-4 shrink-0"
            style={{ color: "var(--pb-success)" }}
            aria-hidden="true"
          />
          <span className="min-w-0 flex-1 truncate font-medium">{successNotice}</span>
          <button
            type="button"
            className="pb-btn pb-btn-plain -mr-1"
            aria-label="Dismiss confirmation"
            onClick={dismissSuccess}
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      )}

      {/* ── list + reader ──────────────────────────────────────────────── */}
      {/*
        The list/reader split. `overflow-hidden` so neither pane can force this
        row (or column, with the reading pane at the bottom) taller than the
        height it was given — the two panes divide a fixed space and scroll
        inside it. Works for both orientations: `min-h-0` releases the
        auto-minimum on whichever axis is the main one.
      */}
      <div
        className={`flex min-h-0 flex-1 overflow-hidden ${
          paneRight ? "flex-row" : "flex-col"
        }`}
      >
        <div
          className={`pb-scroll min-h-0 ${
            detail ? "hidden md:block" : "block"
          } ${
            paneRight
              ? // Steps rather than one width: 22rem truncated subject lines
                // on a large display while the reader had room to spare, and
                // a single larger value would crowd a 1280px laptop. The
                // reader takes whatever is left at every step.
                "w-full md:w-[24rem] lg:w-[27rem] xl:w-[28rem] md:shrink-0 md:border-r"
              : "flex-1 border-b"
          }`}
          style={{ borderColor: "var(--pb-border)" }}
        >
          {loading ? (
            <CentredSpinner />
          ) : listError ? (
            <EmptyState title="Mail unavailable" detail={listError} />
          ) : rows.length === 0 ? (
            <EmptyState
              title={search ? "No messages match" : "Nothing here"}
              detail={
                search
                  ? "Try a different search."
                  : "Messages will appear here as they arrive."
              }
            />
          ) : (
            <>
              {rows.map((row) => (
                <button
                  key={`${row.uid_validity}-${row.uid}`}
                  type="button"
                  className="pb-row"
                  data-unread={!row.seen}
                  data-selected={detail?.uid === row.uid}
                  onClick={() => void openMessage(row)}
                >
                  <input
                    type="checkbox"
                    aria-label={`Select message from ${row.from.address}`}
                    checked={selected.has(row.uid)}
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) => {
                      const next = new Set(selected);
                      if (event.target.checked) next.add(row.uid);
                      else next.delete(row.uid);
                      setSelected(next);
                    }}
                  />
                  <span
                    role="img"
                    aria-label={row.flagged ? "Starred" : "Not starred"}
                    onClick={(event) => {
                      event.stopPropagation();
                      void act(row.flagged ? "unstar" : "star", [row.uid]);
                    }}
                  >
                    <Star
                      className="h-3.5 w-3.5"
                      style={{
                        color: row.flagged ? "var(--pb-warn)" : "var(--pb-subtle)",
                        fill: row.flagged ? "currentColor" : "none",
                      }}
                      aria-hidden="true"
                    />
                  </span>
                  <span className="min-w-0">
                    <span className="pb-row-from block truncate">
                      {row.from.name || row.from.address || "(unknown sender)"}
                    </span>
                    <span className="pb-row-subject block truncate">
                      {row.subject || "(no subject)"}
                    </span>
                  </span>
                  <span className="flex shrink-0 flex-col items-end gap-1">
                    <span className="text-xs pb-subtle whitespace-nowrap">
                      {formatMessageDate(row.date)}
                    </span>
                    {row.has_attachments && (
                      <Paperclip className="h-3 w-3" style={{ color: "var(--pb-subtle)" }}
                        aria-label="Has attachments" />
                    )}
                  </span>
                </button>
              ))}

              {page && page.total > page.page_size && (
                <div className="flex items-center justify-between px-3 py-2 text-xs pb-subtle">
                  <span className="pb-num">
                    {(page.page - 1) * page.page_size + 1}–
                    {Math.min(page.page * page.page_size, page.total)} of {page.total}
                  </span>
                  <span className="flex gap-1">
                    <button type="button" className="pb-btn pb-btn-ghost"
                      disabled={page.page <= 1}
                      onClick={() => setPageNumber((n) => n - 1)}>
                      Newer
                    </button>
                    <button type="button" className="pb-btn pb-btn-ghost"
                      disabled={!page.has_next}
                      onClick={() => setPageNumber((n) => n + 1)}>
                      Older
                    </button>
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        <div className={`pb-scroll min-h-0 flex-1 ${detail ? "block" : "hidden md:block"}`}>
          {detailLoading ? (
            <CentredSpinner />
          ) : !detail ? (
            <EmptyState
              title="No message selected"
              detail="Choose a message to read it here."
            />
          ) : (
            <Reader
              detail={detail}
              showRemote={showRemote}
              onBack={() => setDetail(null)}
              onLoadRemote={() =>
                void openMessage(
                  { ...detail, seen: true } as unknown as MessageSummary,
                  true,
                )
              }
              onReply={openReply}
              onAction={(action) => void act(action, [detail.uid])}
            />
          )}
        </div>
      </div>

      {compose && (
        <Compose
          initial={compose}
          identities={identities}
          signatures={signatures}
          onClose={closeCompose}
          onSent={(message) => {
            setNotice(null);
            setSuccessNotice(message);
            setSuccessVisible(true);
            void loadList();
          }}
        />
      )}
    </div>
  );
}

function Reader({
  detail,
  showRemote,
  onBack,
  onLoadRemote,
  onReply,
  onAction,
}: {
  detail: MessageDetail;
  showRemote: boolean;
  onBack: () => void;
  onLoadRemote: () => void;
  onReply: (mode: "reply" | "reply-all" | "forward") => void;
  onAction: (action: Parameters<typeof postbox.act>[0]) => void;
}) {
  return (
    <article className="flex h-full min-h-0 flex-col">
      <header
        className="shrink-0 border-b px-4 py-3"
        style={{ borderColor: "var(--pb-border)" }}
      >
        <div className="mb-2 flex items-center gap-1">
          {/*
            Wrapped for the same reason as the sidebar's close button:
            `md:hidden` on a `.pb-btn` does not work. Tailwind v4 emits
            utilities into `@layer utilities` and `.pb-btn` is unlayered, so
            its `display:inline-flex` beats the utility's `display:none` and
            this back arrow was showing on desktop, where there is no list to
            go back to.
          */}
          <div className="md:hidden">
            <button
              type="button"
              className="pb-btn pb-btn-plain"
              aria-label="Back to list"
              onClick={onBack}
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
          <ToolbarButton label="Reply" icon={CornerUpLeft}
            onClick={() => onReply("reply")} />
          <ToolbarButton label="Reply all" icon={CornerUpRight}
            onClick={() => onReply("reply-all")} />
          <ToolbarButton label="Forward" icon={Forward}
            onClick={() => onReply("forward")} />
          <ToolbarButton label="Archive" icon={Archive}
            onClick={() => onAction("archive")} />
          <ToolbarButton label="Not spam" icon={ShieldCheck}
            onClick={() => onAction("not-spam")} />
          <ToolbarButton label="Delete" icon={Trash2}
            onClick={() => onAction("trash")} />
          <a
            className="pb-btn pb-btn-plain ml-auto"
            href={postbox.rawUrl(detail.folder, detail.uid)}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Eye className="h-3.5 w-3.5" aria-hidden="true" />
            Original
          </a>
        </div>

        <h1 className="text-base font-semibold">{detail.subject || "(no subject)"}</h1>
        <p className="mt-1 text-xs pb-muted">
          <span className="font-medium" style={{ color: "var(--pb-fg)" }}>
            {detail.from.name || detail.from.address}
          </span>{" "}
          &lt;{detail.from.address}&gt; · {formatMessageDate(detail.date)}
        </p>
        <p className="text-xs pb-subtle">To: {detail.to.join(", ") || "—"}</p>
        {detail.cc.length > 0 && (
          <p className="text-xs pb-subtle">Cc: {detail.cc.join(", ")}</p>
        )}
      </header>

      {detail.remote_images_blocked && !showRemote && (
        <div
          className="flex shrink-0 flex-wrap items-center gap-2 px-4 py-2 text-xs"
          style={{ background: "var(--pb-warn-soft)", color: "var(--pb-warn)" }}
        >
          <ImageOff className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="flex-1">
            Remote images were blocked. Loading them tells the sender you opened
            this message.
          </span>
          <button type="button" className="pb-btn pb-btn-ghost" onClick={onLoadRemote}>
            Display images
          </button>
        </div>
      )}

      <div className="pb-scroll min-h-0 flex-1 px-4 py-4">
        {detail.html ? (
          // Safe because the server sanitised this with a real HTML parser.
          // Nothing is cleaned here on purpose: a second sanitiser in the
          // browser would become the one people trusted.
          <div
            className="pb-message-body"
            dangerouslySetInnerHTML={{ __html: detail.html }}
          />
        ) : (
          <pre className="pb-message-body whitespace-pre-wrap text-sm">
            {detail.text || "(This message has no readable content.)"}
          </pre>
        )}

        {detail.attachments.length > 0 && (
          <section className="mt-6 border-t pt-3" style={{ borderColor: "var(--pb-border)" }}>
            <p className="pb-label mb-2">
              {detail.attachments.length} attachment
              {detail.attachments.length === 1 ? "" : "s"}
            </p>
            <ul className="flex flex-wrap gap-2">
              {detail.attachments.map((attachment) => (
                <li key={attachment.part_id}>
                  <a
                    className="pb-btn pb-btn-ghost"
                    href={postbox.attachmentUrl(
                      detail.folder, detail.uid, attachment.part_id,
                    )}
                    download={attachment.filename}
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    <span className="max-w-[14rem] truncate">{attachment.filename}</span>
                    <span className="pb-subtle pb-num">
                      {formatBytes(attachment.size)}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </article>
  );
}

function ToolbarButton({
  label,
  icon: Icon,
  onClick,
  busy,
}: {
  label: string;
  icon: typeof Archive;
  onClick: () => void;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      className="pb-btn pb-btn-plain"
      title={label}
      aria-label={label}
      disabled={busy}
      onClick={onClick}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1 px-6 py-16 text-center">
      <p className="text-sm font-medium">{title}</p>
      {detail && <p className="max-w-xs text-xs pb-muted">{detail}</p>}
    </div>
  );
}
