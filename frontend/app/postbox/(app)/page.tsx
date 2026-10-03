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
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Archive,
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  CornerUpLeft,
  CornerUpRight,
  Download,
  Eye,
  Forward,
  ImageOff,
  Loader2,
  Mail,
  MailOpen,
  MoreHorizontal,
  Paperclip,
  RefreshCw,
  RotateCcw,
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
  type Folder,
  type MessageDetail,
  type MessagePage,
  type MessageSummary,
  type ScheduledRow,
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

/**
 * A browser does not understand RFC 2392 cid: URLs by itself. Map only CIDs
 * that the server reported as safe, previewable image parts to authenticated
 * same-origin preview URLs. The message HTML itself was already sanitised by
 * the backend; this function only resolves its inert inline-image references.
 */
function resolveInlineImageReferences(detail: MessageDetail): string {
  if (!detail.html || !detail.html.toLowerCase().includes("cid:")) {
    return detail.html;
  }

  const inlineImages = new Map(
    detail.attachments
      .filter(
        (attachment) =>
          attachment.content_id &&
          attachment.previewable &&
          attachment.content_type.toLowerCase().startsWith("image/"),
      )
      .map((attachment) => [
        attachment.content_id.trim().replace(/^<|>$/g, "").toLowerCase(),
        postbox.attachmentPreviewUrl(
          detail.folder,
          detail.uid,
          attachment.part_id,
        ),
      ]),
  );

  if (inlineImages.size === 0) return detail.html;

  return detail.html.replace(
    /(\bsrc\s*=\s*["'])cid:([^"']+)(["'])/gi,
    (match, prefix: string, rawCid: string, suffix: string) => {
      let cid = rawCid.trim();
      try {
        cid = decodeURIComponent(cid);
      } catch {
        // A malformed percent escape is just a CID that will not match.
      }
      const url = inlineImages.get(
        cid.replace(/^<|>$/g, "").toLowerCase(),
      );
      return url ? `${prefix}${url}${suffix}` : match;
    },
  );
}

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
  const requestedFilter = params.get("filter");
  const filterMode: "all" | "unread" | "starred" =
    starredOnly || requestedFilter === "starred"
      ? "starred"
      : requestedFilter === "unread"
        ? "unread"
        : "all";
  const unreadOnly = filterMode === "unread";
  const filteredStarredOnly = filterMode === "starred";
  const urlQuery = params.get("q") || "";
  const searchScope =
    params.get("scope") === "all_with_spam_trash"
      ? "all_with_spam_trash"
      : params.get("scope") === "all"
        ? "all"
        : "folder";
  const sortMode = params.get("sort") === "oldest" ? "oldest" : "newest";

  const [queryState, setQueryState] = useState({ key: urlQuery, value: urlQuery });
  const query = queryState.key === urlQuery ? queryState.value : urlQuery;
  const setQuery = useCallback(
    (value: string) => setQueryState({ key: urlQuery, value }),
    [urlQuery],
  );
  // Avoid an unfiltered IMAP list request before the first debounced
  // search when the page is opened from a URL that already contains q=.
  const [search, setSearch] = useState(urlQuery.trim());

  // One key for "which list am I looking at". Paging and selection both reset
  // when it changes, and both derive that from the key rather than having an
  // effect write it — a reset is a consequence of the key, not an event.
  const listKey = `${folder}|${search}|${filterMode}|${searchScope}|${sortMode}`;

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

  useEffect(() => {
    const returnToList = () => {
      setDetail(null);
      setShowRemote(false);
    };
    window.addEventListener("postbox:return-to-list", returnToList);
    return () =>
      window.removeEventListener("postbox:return-to-list", returnToList);
  }, []);

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
        scope:
          search || (filteredStarredOnly && searchScope !== "folder")
            ? searchScope
            : "folder",
        sort: sortMode,
        unread: unreadOnly ? "true" : undefined,
        starred: filteredStarredOnly ? "true" : undefined,
      }),
    [
      folder,
      pageNumber,
      search,
      searchScope,
      sortMode,
      unreadOnly,
      filteredStarredOnly,
    ],
    "Your mail could not be loaded.",
  );

  const page = list.data;
  const loading = list.loading;
  const listError = list.error;
  const loadList = list.reload;

  const directory = useAsyncData(
    () => Promise.all([postbox.identities(), postbox.signatures(), postbox.folders()]),
    [],
    "",
  );
  const identities = directory.data?.[0]?.results ?? [];
  const signatures = directory.data?.[1]?.results ?? [];
  const mailFolders = useMemo(
    () => directory.data?.[2]?.results ?? [],
    [directory.data],
  );

  const scheduledData = useAsyncData(
    () => postbox.scheduled(),
    [],
    "",
  );
  const scheduledRows = scheduledData.data?.results ?? [];

  // Query-driven compose supports both the sidebar Compose link and
  // Contacts -> Send message. Keep this declarative so route navigation does
  // not need an effect that copies URL state into React state.
  const composeRequested = params.get("compose") === "new";
  const composeRecipient = params.get("to")?.trim() || "";
  const compose =
    explicitCompose ??
    (composeRequested
      ? {
          mode: "new" as const,
          ...(composeRecipient ? { to: [composeRecipient] } : {}),
        }
      : null);

  const closeCompose = useCallback(() => {
    setExplicitCompose(null);
    if (composeRequested) {
      const next = new URLSearchParams(params.toString());
      next.delete("compose");
      next.delete("to");
      const queryString = next.toString();
      router.replace(queryString ? `/postbox?${queryString}` : "/postbox");
    }
  }, [composeRequested, params, router]);

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
        const role = mailFolders.find((item) => item.name === summary.folder)?.role;

        if (role === "drafts") {
          setDetail(null);
          setExplicitCompose({
            mode: "draft",
            to: data.to,
            cc: data.cc,
            bcc: data.bcc ?? [],
            subject: data.subject,
            text: data.text,
            html: data.html,
            quoted_text: data.quoted_text ?? "",
            from_address: data.from.address,
            in_reply_to: data.in_reply_to,
            references: data.references,
            draft_uid: data.uid,
            signature_id: data.signature_id ?? null,
            signature_missing: data.signature_missing ?? false,
            existing_attachments: data.attachments.map((attachment) => ({
              folder: data.folder,
              uid: data.uid,
              uid_validity: data.uid_validity,
              part_id: attachment.part_id,
              filename: attachment.filename,
              content_type: attachment.content_type,
              size: attachment.size,
            })),
          });
          return;
        }

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
    [loadList, mailFolders],
  );

  const act = useCallback(
    async (
      action: Parameters<typeof postbox.act>[0],
      uids?: number[],
      extra = {},
      sourceFolder = folder,
    ) => {
      const targets = uids ?? Array.from(selected);
      if (targets.length === 0) return;
      setBusy(true);
      try {
        await postbox.act(action, sourceFolder, targets, extra);
        setSelected(new Set());
        const removesFromCurrentView =
          new Set([
            "archive",
            "trash",
            "spam",
            "not-spam",
            "move",
            "restore",
            "delete",
          ]).has(action) ||
          (unreadOnly && action === "read") ||
          (filteredStarredOnly && action === "unstar");
        if (removesFromCurrentView && detail && targets.includes(detail.uid)) {
          setDetail(null);
        }
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
    [selected, folder, detail, loadList, unreadOnly, filteredStarredOnly],
  );

  const trustRemoteSender = useCallback(async () => {
    if (!detail) return;
    setDetailLoading(true);
    try {
      const trusted = await postbox.trustRemoteImages(
        detail.folder,
        detail.uid,
        detail.uid_validity,
      );
      const data = await postbox.message(detail.folder, detail.uid);
      setDetail(data);
      setShowRemote(false);
      setNotice(null);
      setSuccessNotice(`Images will always display from ${trusted.sender}.`);
      setSuccessVisible(true);
    } catch (caught) {
      setNotice(
        describePostBoxError(
          caught,
          "That sender preference could not be saved.",
        ),
      );
    } finally {
      setDetailLoading(false);
    }
  }, [detail]);

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
          quoted_text: context.quoted_text,
          from_address: context.from_address,
          in_reply_to: context.in_reply_to,
          references: context.references,
          existing_attachments:
            mode === "forward" ? context.attachments : [],
        });
      } catch (caught) {
        setNotice(describePostBoxError(caught, "That reply could not be prepared."));
      }
    },
    [detail],
  );

  const rescheduleScheduled = useCallback(
    async (row: ScheduledRow, scheduledAt: string) => {
      try {
        await postbox.rescheduleMessage(row.id, scheduledAt);
        await scheduledData.reload();
        setNotice(null);
        setSuccessNotice("Scheduled time updated.");
        setSuccessVisible(true);
      } catch (caught) {
        setNotice(
          describePostBoxError(caught, "That scheduled message could not be updated."),
        );
      }
    },
    [scheduledData],
  );

  const cancelScheduled = useCallback(
    async (row: ScheduledRow) => {
      try {
        await postbox.cancelScheduled(row.id);
        setDetail(null);
        await Promise.all([scheduledData.reload(), loadList()]);
        setNotice(null);
        setSuccessNotice("Scheduled message moved back to Drafts.");
        setSuccessVisible(true);
      } catch (caught) {
        setNotice(
          describePostBoxError(caught, "That scheduled message could not be cancelled."),
        );
      }
    },
    [loadList, scheduledData],
  );

  const rows = page?.results ?? [];
  const isCrossFolderView =
    searchScope !== "folder" && (Boolean(search) || filteredStarredOnly);
  const allSelected =
    !isCrossFolderView && rows.length > 0 && selected.size === rows.length;
  const paneRight = preferences.reading_pane === "right";
  const paneOff = preferences.reading_pane === "off";
  const folderIsSpam = /(^|[./_-])(spam|junk)($|[./_-])/i.test(folder);
  const folderIsTrash = /(^|[./_-])trash($|[./_-])/i.test(folder);
  const activeSummary =
    detail
      ? rows.find(
          (row) => row.uid === detail.uid && row.folder === detail.folder,
        ) ?? null
      : null;
  const activeScheduled =
    detail
      ? scheduledRows.find(
          (row) => row.uid === detail.uid && row.folder === detail.folder,
        ) ?? null
      : null;

  const applyFilter = (nextFilter: "all" | "unread" | "starred") => {
    const next = new URLSearchParams(params.toString());
    next.delete("starred");
    if (nextFilter === "all") next.delete("filter");
    else next.set("filter", nextFilter);
    if (!search && nextFilter !== "starred") next.delete("scope");
    next.delete("page");
    setSelected(new Set());
    setDetail(null);
    router.push(`/postbox?${next.toString()}`);
  };

  const applySort = (nextSort: "newest" | "oldest") => {
    const next = new URLSearchParams(params.toString());
    if (nextSort === "newest") next.delete("sort");
    else next.set("sort", nextSort);
    next.delete("page");
    setSelected(new Set());
    setDetail(null);
    router.push(`/postbox?${next.toString()}`);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="pb-premium-mail-toolbar">
          {!isCrossFolderView && (
            <input
              className="pb-premium-select-all"
              type="checkbox"
              aria-label="Select all visible messages"
              checked={allSelected}
              onChange={(event) =>
                setSelected(
                  event.target.checked ? new Set(rows.map((row) => row.uid)) : new Set(),
                )
              }
            />
          )}

          {selected.size > 0 ? (
            <div className="pb-premium-selection-actions">
              <span>{selected.size} selected</span>
              <ToolbarButton label="Mark read" icon={MailOpen} busy={busy}
                onClick={() => void act("read")} />
              <ToolbarButton label="Mark unread" icon={Mail} busy={busy}
                onClick={() => void act("unread")} />
              <ToolbarButton label="Star" icon={Star} busy={busy}
                onClick={() => void act("star")} />
              <ToolbarButton label="Archive" icon={Archive} busy={busy}
                onClick={() => void act("archive")} />
              <MoveMenu
                folders={mailFolders}
                currentFolder={folder}
                disabled={busy}
                onMove={(destination) => void act("move", undefined, { destination })}
              />
              {folderIsSpam ? (
                <ToolbarButton label="Not spam" icon={ShieldCheck} busy={busy}
                  onClick={() => void act("not-spam")} />
              ) : (
                <ToolbarButton label="Spam" icon={ShieldAlert} busy={busy}
                  onClick={() => void act("spam")} />
              )}
              {folderIsTrash ? (
                <ToolbarButton label="Restore" icon={RotateCcw} busy={busy}
                  onClick={() => void act("restore")} />
              ) : (
                <ToolbarButton label="Trash" icon={Trash2} busy={busy}
                  onClick={() => void act("trash")} />
              )}
              <button
                type="button"
                className="pb-premium-icon-button"
                aria-label="Clear selection"
                title="Clear selection"
                onClick={() => setSelected(new Set())}
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          ) : (
            <div className="pb-premium-mail-tabs" role="tablist" aria-label="Mailbox filter">
              {(["all", "unread", "starred"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  role="tab"
                  aria-selected={filterMode === mode}
                  data-active={filterMode === mode ? "true" : "false"}
                  onClick={() => applyFilter(mode)}
                >
                  {mode === "all" ? "All mail" : mode === "unread" ? "Unread" : "Starred"}
                </button>
              ))}
            </div>
          )}

          <div className="pb-premium-mail-toolbar-actions">
            <select
              className="pb-premium-sort-select"
              aria-label="Message order"
              value={sortMode}
              onChange={(event) =>
                applySort(event.target.value === "oldest" ? "oldest" : "newest")
              }
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
            <button
              type="button"
              className="pb-premium-icon-button"
              aria-label="Refresh mailbox"
              title="Refresh mailbox"
              disabled={loading}
              onClick={() => void loadList()}
            >
              <RefreshCw className={`h-[18px] w-[18px] ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            </button>
            <details className="pb-premium-mail-more">
              <summary className="pb-premium-icon-button" aria-label="Mailbox actions" title="Mailbox actions">
                <MoreHorizontal className="h-5 w-5" aria-hidden="true" />
              </summary>
              <div className="pb-premium-mail-menu">
                {!isCrossFolderView && (
                  <button
                    type="button"
                    disabled={rows.length === 0}
                    onClick={() => void act("read", rows.map((row) => row.uid))}
                  >
                    Mark visible messages as read
                  </button>
                )}
                <a href="/postbox/settings">Change reading layout</a>
              </div>
            </details>
          </div>
        </div>

      {/* ── toolbar ────────────────────────────────────────────────────── */}
      <div
        className="pb-mail-legacy-toolbar flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2"
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

        <div className="pb-legacy-mail-search relative ml-auto min-w-0 flex-1 sm:max-w-xs">
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
        className={`pb-mail-layout flex min-h-0 flex-1 overflow-hidden ${
          paneRight ? "flex-row" : "flex-col"
        } ${paneOff ? "pb-layout-full" : paneRight ? "pb-layout-right" : "pb-layout-bottom"}`}
      >
        <div
          className={`pb-mail-list-pane pb-scroll min-h-0 ${
            detail ? (paneOff ? "hidden" : "hidden md:block") : "block"
          } ${
            paneOff
              ? "w-full flex-1"
              : paneRight
                ? "w-full md:w-[24rem] lg:w-[27rem] xl:w-[28rem] md:shrink-0 md:border-r"
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
                  <div
                    key={`${row.uid_validity}-${row.uid}`}
                    className="pb-row pb-premium-message-row"
                    data-unread={!row.seen}
                    data-selected={detail?.uid === row.uid}
                  >
                    {!isCrossFolderView && (
                      <input
                        type="checkbox"
                        aria-label={`Select message from ${row.from.address}`}
                        checked={selected.has(row.uid)}
                        onChange={(event) => {
                          const next = new Set(selected);
                          if (event.target.checked) next.add(row.uid);
                          else next.delete(row.uid);
                          setSelected(next);
                        }}
                      />
                    )}
                    <button
                      type="button"
                      className="pb-premium-row-star"
                      aria-label={row.flagged ? "Unstar message" : "Star message"}
                      onClick={() =>
                        void act(
                          row.flagged ? "unstar" : "star",
                          [row.uid],
                          {},
                          row.folder,
                        )
                      }
                    >
                      <Star
                        className="h-[17px] w-[17px]"
                        style={{
                          color: row.flagged ? "var(--pb-warn)" : "var(--pb-subtle)",
                          fill: row.flagged ? "currentColor" : "none",
                        }}
                        aria-hidden="true"
                      />
                    </button>
                    <button
                      type="button"
                      className="pb-premium-message-open"
                      onClick={() => void openMessage(row)}
                    >
                      <span className="pb-premium-row-avatar" aria-hidden="true">
                        {senderInitials(row.from.name || row.from.address)}
                      </span>
                      <span className="pb-row-from">
                        {row.from.name || row.from.address || "(unknown sender)"}
                      </span>
                      <span className="pb-premium-row-copy">
                        <span className="pb-row-subject">
                          {row.subject || "(no subject)"}
                        </span>
                      </span>
                      <span className="pb-premium-row-indicators">
                        {row.has_attachments && (
                          <Paperclip className="h-3.5 w-3.5" aria-label="Has attachments" />
                        )}
                      </span>
                      <time>{formatMessageDate(row.date)}</time>
                      {!row.seen && <span className="pb-premium-unread-dot" aria-hidden="true" />}
                    </button>
                  </div>

              ))}

              {page && page.total > page.page_size && (
                <div className="flex items-center justify-between px-3 py-2 text-xs pb-subtle">
                  <span className="pb-num">
                    {(page.page - 1) * page.page_size + 1}–
                    {Math.min(page.page * page.page_size, page.total)} of {page.total}
                  </span>
                  <span className="flex gap-1">
                    <button type="button" className="pb-btn pb-btn-ghost"
                      aria-label="Previous page"
                      disabled={page.page <= 1}
                      onClick={() => setPageNumber((n) => n - 1)}>
                      <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                      Newer
                    </button>
                    <button type="button" className="pb-btn pb-btn-ghost"
                      aria-label="Next page"
                      disabled={!page.has_next}
                      onClick={() => setPageNumber((n) => n + 1)}>
                      Older
                      <ChevronRight className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        <div className={`pb-mail-reader-pane pb-scroll min-h-0 flex-1 ${
          detail ? "block" : paneOff ? "hidden" : "hidden md:block"
        }`}>
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
              summary={activeSummary}
              showRemote={showRemote}
              onBack={() => setDetail(null)}
              onLoadRemote={() =>
                void openMessage(
                  { ...detail, seen: true } as unknown as MessageSummary,
                  true,
                )
              }
              onTrustRemote={() => void trustRemoteSender()}
              onReply={openReply}
              folders={mailFolders}
              scheduledRow={activeScheduled}
              onRescheduleScheduled={rescheduleScheduled}
              onCancelScheduled={cancelScheduled}
              onMove={(destination) =>
                void act("move", [detail.uid], { destination }, detail.folder)
              }
              onAction={(action) =>
                void act(action, [detail.uid], {}, detail.folder)
              }
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
            void scheduledData.reload();
          }}
        />
      )}
    </div>
  );
}

function Reader({
  detail,
  summary,
  showRemote,
  onBack,
  onLoadRemote,
  onTrustRemote,
  onReply,
  folders,
  scheduledRow,
  onRescheduleScheduled,
  onCancelScheduled,
  onMove,
  onAction,
}: {
  detail: MessageDetail;
  summary: MessageSummary | null;
  showRemote: boolean;
  onBack: () => void;
  onLoadRemote: () => void;
  onTrustRemote: () => void;
  onReply: (mode: "reply" | "reply-all" | "forward") => void;
  folders: Folder[];
  scheduledRow: ScheduledRow | null;
  onRescheduleScheduled: (row: ScheduledRow, scheduledAt: string) => Promise<void>;
  onCancelScheduled: (row: ScheduledRow) => Promise<void>;
  onMove: (destination: string) => void;
  onAction: (action: Parameters<typeof postbox.act>[0]) => void;
}) {
  const isSpam = /(^|[./_-])(spam|junk)($|[./_-])/i.test(detail.folder);
  const isTrash = /(^|[./_-])trash($|[./_-])/i.test(detail.folder);
  const renderedHtml = useMemo(
    () => resolveInlineImageReferences(detail),
    [detail],
  );
  const visibleAttachments = useMemo(
    () => detail.attachments.filter((attachment) => !attachment.inline),
    [detail.attachments],
  );

  return (
    <article className="pb-premium-reader flex h-full min-h-0 flex-col">
      <div className="pb-premium-reader-toolbar">
        <div className="md:hidden">
          <button
            type="button"
            className="pb-btn pb-btn-plain"
            aria-label="Back to mailbox"
            onClick={onBack}
          >
            <ArrowLeft className="h-[19px] w-[19px]" aria-hidden="true" />
          </button>
        </div>
        <span className="pb-premium-toolbar-divider md:hidden" aria-hidden="true" />
        <ToolbarButton label="Archive" icon={Archive} onClick={() => onAction("archive")} />
        <ToolbarButton
          label={isTrash ? "Permanently delete" : "Move to Trash"}
          icon={Trash2}
          onClick={() => {
            if (!isTrash || window.confirm("Permanently delete this message? This cannot be undone.")) {
              onAction(isTrash ? "delete" : "trash");
            }
          }}
        />
        <ToolbarButton
          label={isSpam ? "Not spam" : "Mark as spam"}
          icon={isSpam ? ShieldCheck : ShieldAlert}
          onClick={() => onAction(isSpam ? "not-spam" : "spam")}
        />
        <ToolbarButton label="Mark unread" icon={Mail} onClick={() => onAction("unread")} />
        <MoveMenu
          folders={folders}
          currentFolder={detail.folder}
          onMove={onMove}
        />
        {isTrash && (
          <ToolbarButton label="Restore" icon={RotateCcw} onClick={() => onAction("restore")} />
        )}
        <span className="flex-1" />
        <a
          className="pb-btn pb-btn-plain"
          href={postbox.rawUrl(detail.folder, detail.uid)}
          target="_blank"
          rel="noopener noreferrer"
          title="Download original message"
        >
          <Download className="h-4 w-4" aria-hidden="true" />
          <span className="hidden lg:inline">Original</span>
        </a>
      </div>

      <div className="pb-premium-reader-scroll">
        {scheduledRow && (
          <ScheduledMessageBanner
            key={scheduledRow.id + scheduledRow.scheduled_at}
            row={scheduledRow}
            onReschedule={onRescheduleScheduled}
            onCancel={onCancelScheduled}
          />
        )}
        <div className="pb-premium-reader-heading">
          <h1>{detail.subject || "(no subject)"}</h1>
          {summary && (
            <button
              type="button"
              className="pb-premium-reader-star"
              aria-label={summary.flagged ? "Unstar message" : "Star message"}
              onClick={() => onAction(summary.flagged ? "unstar" : "star")}
            >
              <Star
                className="h-[21px] w-[21px]"
                fill={summary.flagged ? "currentColor" : "none"}
                aria-hidden="true"
              />
            </button>
          )}
        </div>

        <div className="pb-premium-reader-sender">
          <span className="pb-premium-reader-avatar" aria-hidden="true">
            {senderInitials(detail.from.name || detail.from.address)}
          </span>
          <div className="min-w-0 flex-1">
            <div>
              <strong>{detail.from.name || detail.from.address}</strong>
              {detail.from.address && (
                <span className="pb-premium-sender-email">&lt;{detail.from.address}&gt;</span>
              )}
            </div>
            <details>
              <summary>To {detail.to.join(", ") || "—"}</summary>
              <div className="pb-premium-message-metadata">
                <p>From: {detail.from.name || detail.from.address} &lt;{detail.from.address}&gt;</p>
                <p>To: {detail.to.join(", ") || "—"}</p>
                {detail.cc.length > 0 && <p>Cc: {detail.cc.join(", ")}</p>}
                <p>Date: {formatMessageDate(detail.date)}</p>
              </div>
            </details>
          </div>
          <time>{formatMessageDate(detail.date)}</time>
        </div>

        {detail.remote_images_blocked && !showRemote && (
          <div className="pb-premium-privacy-banner">
            <ImageOff className="h-[18px] w-[18px]" aria-hidden="true" />
            <div>
              <strong>External images are hidden</strong>
              <p>To protect your privacy, images from this sender are blocked.</p>
              <div className="pb-premium-privacy-actions">
                <button type="button" onClick={onLoadRemote}>Display images</button>
                {detail.from.address && (
                  <button type="button" onClick={onTrustRemote}>
                    Always display images from this sender
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        <div className={`pb-message-body pb-premium-message-body ${renderedHtml ? "pb-premium-html-mail" : ""}`}>
          {renderedHtml ? (
            <div dangerouslySetInnerHTML={{ __html: renderedHtml }} />
          ) : (
            <pre className="whitespace-pre-wrap">
              {detail.text || "(This message has no readable content.)"}
            </pre>
          )}
        </div>

        {visibleAttachments.length > 0 && (
          <section className="pb-premium-reader-attachments">
            <h3>
              <Paperclip className="h-4 w-4" aria-hidden="true" />
              {visibleAttachments.length} attachment{visibleAttachments.length === 1 ? "" : "s"}
            </h3>
            <div className="pb-premium-attachment-grid">
              {visibleAttachments.map((attachment) => (
                <div
                  key={attachment.part_id}
                  className="pb-premium-attachment-card"
                >
                  <span className="pb-premium-file-icon">
                    <Paperclip className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <strong>{attachment.filename}</strong>
                    <small>{attachment.content_type || "Attachment"} · {formatBytes(attachment.size)}</small>
                  </span>
                  <span className="pb-premium-attachment-actions">
                    {attachment.previewable && (
                      <a
                        href={postbox.attachmentPreviewUrl(
                          detail.folder,
                          detail.uid,
                          attachment.part_id,
                        )}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label={`Preview ${attachment.filename}`}
                        title="Preview"
                      >
                        <Eye className="h-4 w-4" aria-hidden="true" />
                      </a>
                    )}
                    <a
                      href={postbox.attachmentUrl(
                        detail.folder,
                        detail.uid,
                        attachment.part_id,
                      )}
                      download={attachment.filename}
                      aria-label={`Download ${attachment.filename}`}
                      title="Download"
                    >
                      <Download className="h-4 w-4" aria-hidden="true" />
                    </a>
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {!isSpam && !isTrash && (
          <div className="pb-premium-reply-actions">
            <button type="button" className="pb-btn pb-btn-ghost" onClick={() => onReply("reply")}>
              <CornerUpLeft className="h-4 w-4" aria-hidden="true" />
              Reply
            </button>
            <button type="button" className="pb-btn pb-btn-ghost" onClick={() => onReply("reply-all")}>
              <CornerUpRight className="h-4 w-4" aria-hidden="true" />
              Reply all
            </button>
            <button type="button" className="pb-btn pb-btn-ghost" onClick={() => onReply("forward")}>
              <Forward className="h-4 w-4" aria-hidden="true" />
              Forward
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

function ScheduledMessageBanner({
  row,
  onReschedule,
  onCancel,
}: {
  row: ScheduledRow;
  onReschedule: (row: ScheduledRow, scheduledAt: string) => Promise<void>;
  onCancel: (row: ScheduledRow) => Promise<void>;
}) {
  const [time, setTime] = useState(() => toLocalDateTimeInput(row.scheduled_at));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const pending = row.state === "pending";
  const failed = row.state === "failed";

  const save = async () => {
    const parsed = new Date(time);
    if (!time || Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
      setError("Choose a future date and time.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onReschedule(row, parsed.toISOString());
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm("Cancel this scheduled send and move the message back to Drafts?")) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onCancel(row);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="pb-scheduled-banner" aria-label="Scheduled send">
      <Clock className="h-5 w-5 shrink-0" aria-hidden="true" />
      <div className="pb-scheduled-banner-copy">
        <strong>
          {failed ? "Scheduled send needs attention" : "Scheduled to send"}
        </strong>
        <p>
          {new Date(row.scheduled_at).toLocaleString()}
          {row.recipients ? " · " + row.recipients : ""}
        </p>
        {row.last_error && <p className="pb-scheduled-error">{row.last_error}</p>}
        {error && <p className="pb-scheduled-error">{error}</p>}
      </div>
      {pending && (
        <div className="pb-scheduled-controls">
          <input
            type="datetime-local"
            aria-label="New scheduled date and time"
            value={time}
            onChange={(event) => setTime(event.target.value)}
            disabled={busy}
          />
          <button type="button" onClick={() => void save()} disabled={busy}>
            Reschedule
          </button>
        </div>
      )}
      {(pending || failed) && (
        <button
          type="button"
          className="pb-scheduled-cancel"
          onClick={() => void cancel()}
          disabled={busy}
        >
          Cancel
        </button>
      )}
    </section>
  );
}

function toLocalDateTimeInput(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function MoveMenu({
  folders,
  currentFolder,
  onMove,
  disabled = false,
}: {
  folders: Folder[];
  currentFolder: string;
  onMove: (destination: string) => void;
  disabled?: boolean;
}) {
  const destinations = folders.filter(
    (item) =>
      item.name !== currentFolder &&
      !new Set(["sent", "drafts", "scheduled"]).has(item.role),
  );
  if (destinations.length === 0) return null;

  return (
    <details className="pb-premium-move-menu">
      <summary
        className="pb-btn pb-btn-plain"
        aria-label="Move to folder"
        title="Move to folder"
        aria-disabled={disabled}
        onClick={(event) => {
          if (disabled) event.preventDefault();
        }}
      >
        <Archive className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="hidden sm:inline">Move</span>
      </summary>
      <div className="pb-premium-move-panel">
        <p>Move to folder</p>
        {destinations.map((item) => (
          <button
            key={item.name}
            type="button"
            onClick={(event) => {
              const details = event.currentTarget.closest("details");
              if (details) details.open = false;
              onMove(item.name);
            }}
          >
            {item.name}
          </button>
        ))}
      </div>
    </details>
  );
}

function senderInitials(value: string): string {
  const clean = value.replace(/[<>]/g, " ").trim();
  const parts = clean.includes("@")
    ? clean.split("@")[0].split(/[._\s-]+/)
    : clean.split(/\s+/);
  return parts.filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "M";
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
