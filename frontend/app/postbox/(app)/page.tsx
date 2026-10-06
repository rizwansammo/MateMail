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
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
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
  Folder as FolderIcon,
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
  Tag,
  Trash2,
  X,
} from "lucide-react";

import { Compose, type ComposeInitial } from "@/components/postbox/compose";
import { ConversationReader } from "@/components/postbox/conversation-reader";
import { MessageHeaders } from "@/components/postbox/message-headers";
import { LinkifiedPlainText } from "@/components/postbox/linkified-plain-text";
import { resolveInlineImageReferences } from "@/lib/postbox-inline-images";
import { POSTBOX_MAILBOX_EVENT } from "@/lib/postbox-realtime";
import { useAsyncData } from "@/components/postbox/use-async";
import { describePostBoxError, usePostBox } from "@/contexts/postbox-context";
import {
  formatBytes,
  formatMessageDate,
  postbox,
  type Folder,
  type MailLabel,
  type MessageLabel,
  type ConversationDetail,
  type ConversationPage,
  type ConversationSummary,
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
  const { mailbox, permissions, preferences, updatePreferences } = usePostBox();
  const canReadMailbox = permissions.can_read;
  const canManageMailbox = permissions.can_manage;
  const canSendMailbox = permissions.can_send_as || permissions.can_send_on_behalf;
  const isTeamBox = mailbox?.kind === "team_box";

  const folder = params.get("folder") || "INBOX";
  const labelId = params.get("label");
  // Backwards-compatible ?view= links can override the saved list preference.
  // The opened reader has an independent mailbox-level choice.
  const urlListView = params.get("view");
  const listView = urlListView === "messages" || urlListView === "conversations"
    ? urlListView : preferences.list_view;
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
  const listKey = `${folder}|${labelId || ""}|${search}|${filterMode}|${searchScope}|${sortMode}|${listView}`;

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
  const [thread, setThread] = useState<ConversationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showRemote, setShowRemote] = useState(false);
  const openRequestId = useRef(0);
  const threadInlineActive = useRef(false);
  const reportInlineState = useCallback((active: boolean) => {
    threadInlineActive.current = active;
  }, []);

  useEffect(() => {
    const returnToList = () => {
      if (threadInlineActive.current) {
        setNotice("Save or close your inline reply before navigating.");
        return;
      }
      openRequestId.current += 1;
      setDetail(null);
      setThread(null);
      setShowRemote(false);
    };
    window.addEventListener("postbox:return-to-list", returnToList);
    return () =>
      window.removeEventListener("postbox:return-to-list", returnToList);
  }, []);

  useEffect(() => {
    // Next.js Link navigations do not trigger beforeunload. Intercept them
    // while a full inline composer is open so unsaved reply text is retained.
    const protectInlineDraft = (event: MouseEvent) => {
      if (!threadInlineActive.current || !(event.target instanceof Element)) return;
      const anchor = event.target.closest("a[href]") as HTMLAnchorElement | null;
      if (!anchor) return;
      const href = new URL(anchor.href, window.location.href);
      if (href.origin !== window.location.origin || !href.pathname.startsWith("/postbox")) return;
      event.preventDefault();
      event.stopPropagation();
      setNotice("Save or close your inline reply before navigating.");
    };
    document.addEventListener("click", protectInlineDraft, true);
    return () => document.removeEventListener("click", protectInlineDraft, true);
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
    () => {
      if (!canReadMailbox) {
        return Promise.resolve({
          folder,
          scope: "folder",
          sort: sortMode,
          uid_validity: 0,
          page: 1,
          page_size: preferences.messages_per_page || 25,
          total: 0,
          has_next: false,
          results: [],
        } as MessagePage);
      }
      return labelId ? postbox.labeledMessages(labelId, {
        page: pageNumber,
        q: search || undefined,
        sort: sortMode,
        unread: unreadOnly ? "true" : undefined,
        starred: filteredStarredOnly ? "true" : undefined,
      }) : postbox.messages({
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
      });
    },
    [
      canReadMailbox,
      folder,
      labelId,
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
    () => Promise.all([
      canSendMailbox
        ? postbox.identities()
        : Promise.resolve({ results: [] }),
      canSendMailbox
        ? postbox.signatures()
        : Promise.resolve({ results: [] }),
      canReadMailbox
        ? postbox.folders()
        : Promise.resolve({ results: [] }),
      canReadMailbox
        ? postbox.labels()
        : Promise.resolve({ results: [] }),
    ]),
    [canReadMailbox, canSendMailbox],
    "",
  );
  const identities = directory.data?.[0]?.results ?? [];
  const signatures = directory.data?.[1]?.results ?? [];
  const mailLabels: MailLabel[] = directory.data?.[3]?.results ?? [];
  const mailFolders = useMemo(
    () => directory.data?.[2]?.results ?? [],
    [directory.data],
  );
  const contacts = useAsyncData(
    () => canReadMailbox
      ? postbox.contacts()
      : Promise.resolve({ results: [] }),
    [canReadMailbox],
    "",
  );
  const contactNameByEmail = useMemo(() => {
    const names = new Map<string, string>();
    for (const contact of contacts.data?.results ?? []) {
      const email = contact.email.trim().toLowerCase();
      const name = contact.name.trim();
      if (email && name) names.set(email, name);
    }
    return names;
  }, [contacts.data]);
  const sentFolderNames = useMemo(
    () => new Set(
      mailFolders
        .filter((item) => item.role === "sent")
        .map((item) => item.name),
    ),
    [mailFolders],
  );

  useEffect(() => {
    const refreshLabels = () => void directory.reload();
    window.addEventListener("postbox:labels-changed", refreshLabels);
    return () => window.removeEventListener("postbox:labels-changed", refreshLabels);
    // The directory hook retains the reload callback for the active mailbox.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Inbox defaults to conversations. Searches, filtered views and other
  // folders retain the existing precise per-message interface.
  const conversationEligible =
    (folder.toUpperCase() === "INBOX" ||
      mailFolders.some((item) => item.name === folder && item.role === "inbox")) &&
    !labelId && !search && !unreadOnly && !filteredStarredOnly &&
    sortMode === "newest" && searchScope === "folder";
  const conversationMode = conversationEligible && listView === "conversations";
  const conversations = useAsyncData<ConversationPage>(
    () => canReadMailbox && conversationMode
      ? postbox.conversations({ scope: "inbox", page: pageNumber, page_size: Math.min(100, preferences.messages_per_page || 25) })
      : Promise.resolve({
          scope: "inbox" as const, page: 1, page_size: 25, total: 0,
          has_next: false, results: [],
        }),
    [canReadMailbox, conversationMode, pageNumber, preferences.messages_per_page],
    "Conversation view is unavailable. Switch to Messages.",
  );

  const scheduledData = useAsyncData(
    () => canReadMailbox
      ? postbox.scheduled()
      : Promise.resolve({ results: [] }),
    [canReadMailbox],
    "",
  );
  const scheduledRows = scheduledData.data?.results ?? [];

  useEffect(() => {
    let timer: number | null = null;
    const refreshMailboxView = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = null;
        void loadList();
        if (conversationMode) void conversations.reload();
      }, 80);
    };
    window.addEventListener(POSTBOX_MAILBOX_EVENT, refreshMailboxView);
    return () => {
      window.removeEventListener(POSTBOX_MAILBOX_EVENT, refreshMailboxView);
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [loadList, conversationMode, conversations.reload]);

  // Query-driven compose supports both the sidebar Compose link and
  // Contacts -> Send message. Keep this declarative so route navigation does
  // not need an effect that copies URL state into React state.
  const composeRequested = params.get("compose") === "new";
  const composeRecipient = params.get("to")?.trim() || "";
  const compose = canSendMailbox
    ? explicitCompose ??
    (composeRequested
      ? {
          mode: "new" as const,
          ...(composeRecipient ? { to: [composeRecipient] } : {}),
        }
      : null)
    : null;

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
      if (threadInlineActive.current) {
        setNotice("Save or close your inline reply before switching messages.");
        return;
      }
      const requestId = ++openRequestId.current;
      setDetailLoading(true);
      if (!remote) {
        setThread(null);
        }
      setShowRemote(remote);
      try {
        const data = await postbox.message(summary.folder, summary.uid, remote, summary.uid_validity);
        if (requestId !== openRequestId.current) return;
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
        if (
          canManageMailbox &&
          !summary.seen &&
          role !== "sent" &&
          !summary.sent_origin
        ) {
          await postbox.act("read", summary.folder, [summary.uid], {
            uid_validity: summary.uid_validity,
          });
          void loadList();
        }
        if (!remote &&
            !["drafts", "scheduled", "trash", "junk"].includes(role || "") &&
            !/(^|[./_-])(spam|junk|trash|scheduled)($|[./_-])/i.test(summary.folder)) {
          try {
            const grouped = await postbox.conversationForMessage(
              data.folder, data.uid, data.uid_validity,
            );
            if (requestId === openRequestId.current) setThread(grouped);
          } catch (caught) {
            // A mailbox over the header-scan cap stays fully usable through
            // its original reader. Never claim a partial thread is complete.
            if (requestId === openRequestId.current) {
              setThread(null);
              if (conversationMode) {
                setNotice(describePostBoxError(
                  caught, "Conversation view unavailable. Showing this message instead.",
                ));
              }
            }
          }
        }
      } catch (caught) {
        if (requestId === openRequestId.current) {
          setNotice(describePostBoxError(caught, "That message could not be opened."));
        }
      } finally {
        if (requestId === openRequestId.current) setDetailLoading(false);
      }
    },
    [canManageMailbox, loadList, mailFolders, conversationMode],
  );

  const applyLabel = async (labelIdToApply: string, remove: boolean, uids: number[], sourceFolder: string, uidValidity: number) => {
    if (!uidValidity || !uids.length) return;
    setBusy(true);
    try {
      await postbox.assignLabel({ label_id: labelIdToApply, folder: sourceFolder,
        uids, uid_validity: uidValidity, remove });
      setSelected(new Set());
      if (detail && uids.includes(detail.uid) && detail.folder === sourceFolder) {
        const updated = await postbox.message(detail.folder, detail.uid, false, detail.uid_validity);
        setDetail(updated);
      }
      // A thread holds its own message snapshot, separate from the list.
      // Refresh it so an applied/removed label is visible immediately.
      if (thread && detail) {
        try {
          setThread(await postbox.conversationForMessage(
            detail.folder, detail.uid, detail.uid_validity,
          ));
        } catch {
          // Label change is already saved. A stale thread must not turn that
          // successful operation into a misleading error.
          setThread(null);
        }
      }
      loadList();
      conversations.reload();
      setNotice(null);
    } catch (caught) {
      setNotice(describePostBoxError(caught, "Could not update labels."));
    } finally { setBusy(false); }
  };

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

  const refreshConversation = useCallback(async () => {
    if (!detail) return;
    try {
      const updated = await postbox.conversationForMessage(
        detail.folder, detail.uid, detail.uid_validity,
      );
      setThread(updated);
    } catch {
      // The anchor may have been moved or deleted. Returning to the mailbox
      // is safer than rendering a stale conversation after that action.
      setThread(null);
      setDetail(null);
    }
    conversations.reload();
    loadList();
  }, [detail, conversations.reload, loadList]);

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
    Boolean(labelId) || (searchScope !== "folder" && (Boolean(search) || filteredStarredOnly));
  const allSelected =
    !isCrossFolderView && rows.length > 0 && selected.size === rows.length;
  const paneRight = preferences.reading_pane === "right";
  const paneOff = preferences.reading_pane === "off";
  const folderIsSpam = /(^|[./_-])(spam|junk)($|[./_-])/i.test(folder);
  const folderIsTrash = /(^|[./_-])trash($|[./_-])/i.test(folder);
  const folderIsSent = sentFolderNames.has(folder) || folder.toUpperCase() === "SENT";
  const selectedRows = rows.filter((row) => selected.has(row.uid));
  const selectionHasSentMessage =
    folderIsSent || selectedRows.some((row) => Boolean(row.sent_origin));
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

  const changeConversationMode = (mode: "conversations" | "messages") => {
    if (threadInlineActive.current) {
      setNotice("Save or close your inline reply before changing views.");
      return;
    }
    // Do not touch reader_view: a Message list can open a full Conversation.
    setSelected(new Set());
    const next = new URLSearchParams(params.toString());
    next.delete("view"); // migrate URL-only choice to saved mailbox preference
    void updatePreferences({ list_view: mode }).then(() => {
      router.replace(next.toString() ? "/postbox?" + next.toString() : "/postbox");
    }).catch((error) => {
      setNotice(describePostBoxError(error, "Could not save your mailbox list preference."));
    });
  };

  const changeReaderView = (mode: "thread" | "single") => {
    if (threadInlineActive.current) {
      setNotice("Save or close your inline reply before changing views.");
      return;
    }
    // Opened-message choice never changes the surrounding mailbox list.
    void updatePreferences({ reader_view: mode }).catch((error) => {
      setNotice(describePostBoxError(error, "Could not save your message view preference."));
    });
  };

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

  if (!canReadMailbox) {
    return (
      <div className="flex h-full min-h-0 flex-col">
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

        <div className="flex min-h-0 flex-1 items-center justify-center p-6">
          <EmptyState
            title={isTeamBox ? "Send-only TeamBox access" : "Mailbox unavailable"}
            detail={
              isTeamBox
                ? "You can send from this TeamBox, but its Inbox, folders and message history are not available with your current permission."
                : "This mailbox is not available for reading."
            }
          />
        </div>

        {compose && (
          <Compose
            initial={compose}
            identities={identities}
            signatures={signatures}
            draftsEnabled={false}
            onClose={closeCompose}
            onSent={(message) => {
              setNotice(null);
              setSuccessNotice(message);
              setSuccessVisible(true);
            }}
          />
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!detail && <div className="pb-premium-mail-toolbar">
          {canManageMailbox && !isCrossFolderView && !conversationMode && (
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
              {!selectionHasSentMessage && (
                <>
                  <ToolbarButton label="Mark read" icon={MailOpen} busy={busy}
                    onClick={() => void act("read")} />
                  <ToolbarButton label="Mark unread" icon={Mail} busy={busy}
                    onClick={() => void act("unread")} />
                </>
              )}
              <ToolbarButton label="Star" icon={Star} busy={busy}
                onClick={() => void act("star")} />
              {!selectionHasSentMessage && (
                <ToolbarButton label="Archive" icon={Archive} busy={busy}
                  onClick={() => void act("archive")} />
              )}
              <MoveMenu
                folders={mailFolders}
                currentFolder={folder}
                sentMessage={selectionHasSentMessage}
                disabled={busy}
                onMove={(destination) => void act("move", undefined, { destination })}
              />
              <LabelMenu labels={mailLabels} disabled={busy}
                onApply={(id) => void applyLabel(id, false, Array.from(selected), folder, page?.uid_validity || 0)} />
              {!selectionHasSentMessage && (folderIsSpam ? (
                <ToolbarButton label="Not spam" icon={ShieldCheck} busy={busy}
                  onClick={() => void act("not-spam")} />
              ) : (
                <ToolbarButton label="Spam" icon={ShieldAlert} busy={busy}
                  onClick={() => void act("spam")} />
              ))}
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
              {(folderIsSent
                ? (["all", "starred"] as const)
                : (["all", "unread", "starred"] as const)
              ).map((mode) => (
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
            {conversationEligible && (
              <div className="pb-conversation-switch" role="group" aria-label="Mailbox layout">
                <button type="button" aria-pressed={conversationMode}
                  onClick={() => changeConversationMode("conversations")}>Conversations</button>
                <button type="button" aria-pressed={!conversationMode}
                  onClick={() => changeConversationMode("messages")}>Messages</button>
              </div>
            )}
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
              disabled={loading || (conversationMode && conversations.loading)}
              onClick={() => {
                loadList();
                if (conversationMode) conversations.reload();
                window.dispatchEvent(new Event("postbox:refresh-folders"));
              }}
            >
              <RefreshCw className={`h-[18px] w-[18px] ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            </button>
            <details className="pb-premium-mail-more">
              <summary className="pb-premium-icon-button" aria-label="Mailbox actions" title="Mailbox actions">
                <MoreHorizontal className="h-5 w-5" aria-hidden="true" />
              </summary>
              <div className="pb-premium-mail-menu">
                {canManageMailbox && !isCrossFolderView && !conversationMode && !folderIsSent && (
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
        </div>}

      {/* ── toolbar ────────────────────────────────────────────────────── */}
      <div
        className="pb-mail-legacy-toolbar flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2"
        style={{ borderColor: "var(--pb-border)", background: "var(--pb-bg)" }}
      >
        <input
          type="checkbox"
          aria-label="Select all messages"
          checked={allSelected}
          disabled={!canManageMailbox}
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
          onClick={() => {
            void loadList();
            window.dispatchEvent(new Event("postbox:refresh-folders"));
          }}
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
        </button>

        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {!selectionHasSentMessage && (
              <ToolbarButton label="Mark read" icon={MailOpen} busy={busy}
                onClick={() => void act("read")} />
            )}
            <ToolbarButton label="Star" icon={Star} busy={busy}
              onClick={() => void act("star")} />
            {!selectionHasSentMessage && (
              <>
                <ToolbarButton label="Archive" icon={Archive} busy={busy}
                  onClick={() => void act("archive")} />
                <ToolbarButton label="Spam" icon={ShieldAlert} busy={busy}
                  onClick={() => void act("spam")} />
              </>
            )}
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
          {conversationMode ? (
            conversations.loading ? <CentredSpinner /> :
            conversations.error ? (
              <div className="flex flex-col items-center gap-3 p-5">
                <EmptyState title="Conversation view unavailable" detail={conversations.error} />
                <button type="button" className="pb-btn pb-btn-ghost"
                  onClick={() => changeConversationMode("messages")}>
                  Switch to Messages
                </button>
              </div>
            ) : !conversations.data?.results.length ? (
              <EmptyState title="No conversations yet"
                detail="Messages in your Inbox and their replies will appear together here." />
            ) : (
              <>
                {conversations.data.results.map((conversation: ConversationSummary) => (
                  <div className="pb-thread-list-item"
                    key={conversation.id}
                    data-unread={conversation.unread_count > 0}
                    data-selected={thread?.id === conversation.id}>
                    <button type="button"
                      onClick={() => void openMessage(conversation.latest)}
                      aria-label={"Open conversation: " + (conversation.subject || "(no subject)")}>
                      <span className="pb-premium-row-avatar" aria-hidden="true">
                        {senderInitials(conversation.latest.from.name || conversation.latest.from.address)}
                      </span>
                      <span className="pb-thread-list-copy">
                        <span className="pb-thread-list-top">
                          <strong>{conversation.latest.from.name || conversation.latest.from.address}</strong>
                          <time>{formatMessageDate(conversation.latest_date)}</time>
                        </span>
                        <span className="pb-thread-list-subject">
                          {conversation.subject || "(no subject)"}
                          <MessageLabelBadges labels={conversation.latest.labels} palette={mailLabels} />
                          {conversation.unread_count > 0 && (
                            <span className="ml-2 pb-premium-unread-dot"
                              aria-label={conversation.unread_count + " unread"} />
                          )}
                        </span>
                      </span>
                      {conversation.flagged && <Star className="h-4 w-4 shrink-0"
                        style={{ color: "var(--pb-warn)" }} fill="currentColor"
                        aria-label="Contains starred messages" />}
                      <span className="pb-thread-count" aria-label={
                        conversation.message_count + " messages"
                      }>{conversation.message_count}</span>
                    </button>
                  </div>
                ))}
                {conversations.data.total > conversations.data.page_size && (
                  <div className="flex items-center justify-between gap-2 p-3 text-xs pb-subtle">
                    <span>{(pageNumber - 1) * conversations.data.page_size + 1}–{
                      Math.min(pageNumber * conversations.data.page_size, conversations.data.total)
                    } of {conversations.data.total} conversations</span>
                    <div className="flex gap-2">
                      <button type="button" className="pb-btn pb-btn-ghost"
                        disabled={pageNumber <= 1}
                        onClick={() => setPageNumber((n) => n - 1)}>Newer</button>
                      <button type="button" className="pb-btn pb-btn-ghost"
                        disabled={!conversations.data.has_next}
                        onClick={() => setPageNumber((n) => n + 1)}>Older</button>
                    </div>
                  </div>
                )}
              </>
            )
          ) : loading ? (
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
              {rows.map((row) => {
                const rowIsSent =
                  Boolean(row.sent_origin) ||
                  sentFolderNames.has(row.folder) ||
                  row.folder.toUpperCase() === "SENT";
                const recipientAddresses = row.to.length ? row.to : row.cc;
                const recipientPrefix = row.to.length ? "To:" : row.cc.length ? "Cc:" : "To:";
                const primaryRecipient = recipientAddresses[0] ?? "";
                const recipientName = primaryRecipient
                  ? contactNameByEmail.get(primaryRecipient.trim().toLowerCase()) ?? ""
                  : "";
                const recipientCount = Math.max(recipientAddresses.length - 1, 0);
                const recipientTitle = recipientAddresses.length
                  ? recipientAddresses.map((address) => {
                      const knownName = contactNameByEmail.get(address.trim().toLowerCase());
                      return knownName ? `${knownName} <${address}>` : address;
                    }).join(", ")
                  : "Undisclosed recipients";
                const avatarSource = rowIsSent
                  ? recipientName || primaryRecipient || "To"
                  : row.from.name || row.from.address;

                return (
                  <div
                    key={`${row.uid_validity}-${row.uid}`}
                    className="pb-row pb-premium-message-row"
                    data-unread={!row.seen && !rowIsSent}
                    data-selected={detail?.uid === row.uid}
                    data-sent={rowIsSent}
                  >
                    {canManageMailbox && !isCrossFolderView && (
                      <input
                        type="checkbox"
                        aria-label={
                          rowIsSent
                            ? `Select message to ${primaryRecipient || "recipient"}`
                            : `Select message from ${row.from.address}`
                        }
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
                      disabled={!canManageMailbox}
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
                        {senderInitials(avatarSource)}
                      </span>
                      <span
                        className="pb-row-from"
                        title={rowIsSent ? `${recipientPrefix} ${recipientTitle}` : row.from.address}
                      >
                        {rowIsSent ? (
                          <span className="pb-row-recipient">
                            <span className="pb-row-recipient-prefix">{recipientPrefix}</span>
                            {recipientName && (
                              <>
                                <span className="pb-row-recipient-name">{recipientName}</span>
                                <span className="pb-row-recipient-separator" aria-hidden="true">·</span>
                              </>
                            )}
                            <span className="pb-row-recipient-email">
                              {primaryRecipient || "Undisclosed recipients"}
                            </span>
                            {recipientCount > 0 && (
                              <span
                                className="pb-row-recipient-more"
                                aria-label={`${recipientCount} more recipient${recipientCount === 1 ? "" : "s"}`}
                              >
                                +{recipientCount}
                              </span>
                            )}
                          </span>
                        ) : (
                          row.from.name || row.from.address || "(unknown sender)"
                        )}
                      </span>
                      <span className="pb-premium-row-copy">
                        <span className="pb-row-subject">
                          {row.subject || "(no subject)"}
                          <MessageLabelBadges labels={row.labels} palette={mailLabels} />
                        </span>
                      </span>
                      <span className="pb-premium-row-indicators">
                        {row.has_attachments && (
                          <Paperclip className="h-3.5 w-3.5" aria-label="Has attachments" />
                        )}
                      </span>
                      <time>{formatMessageDate(row.date)}</time>
                      {!row.seen && !rowIsSent && <span className="pb-premium-unread-dot" aria-hidden="true" />}
                    </button>
                  </div>
                );
              })}

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
          ) : thread && preferences.reader_view === "thread" ? (
            <ConversationReader
              key={thread.id + ":" + detail.folder + ":" + detail.uid}
              conversation={thread}
              initialDetail={detail}
              identities={identities}
              signatures={signatures}
              labels={mailLabels}
              canManage={canManageMailbox}
              canSend={canSendMailbox}
              onApplyLabel={(member, id, remove) => {
                if (threadInlineActive.current) {
                  setNotice("Save or close your inline reply before changing labels.");
                  return;
                }
                void applyLabel(id, remove, [member.uid], member.folder, member.uid_validity);
              }}
              onBack={() => {
                openRequestId.current += 1;
                setThread(null);
                          setDetail(null);
              }}
              onSingle={() => changeReaderView("single")}
              onInlineChange={reportInlineState}
              onChanged={refreshConversation}
              onNotice={setNotice}
              onSuccess={(message) => {
                setSuccessNotice(message);
                setSuccessVisible(true);
                setNotice(null);
              }}
              onFloatingCompose={setExplicitCompose}
            />
          ) : (
            <Reader
              detail={detail}
              summary={activeSummary}
              sentMessage={folderIsSent || Boolean(activeSummary?.sent_origin)}
              showRemote={showRemote}
              onBack={() => {
                openRequestId.current += 1;
                setThread(null);
                          setDetail(null);
              }}
              onThread={thread && preferences.reader_view === "single" ? () => changeReaderView("thread") : undefined}
              onLoadRemote={() =>
                void openMessage(
                  { ...detail, seen: true } as unknown as MessageSummary,
                  true,
                )
              }
              onTrustRemote={() => void trustRemoteSender()}
              onReply={openReply}
              canManage={canManageMailbox}
              canSend={canSendMailbox}
              folders={mailFolders}
              labels={mailLabels}
              onApplyLabel={(id, remove) =>
                void applyLabel(id, remove, [detail.uid], detail.folder, detail.uid_validity)
              }
              scheduledRow={activeScheduled}
              onRescheduleScheduled={rescheduleScheduled}
              onCancelScheduled={cancelScheduled}
              onMove={(destination) =>
                void act("move", [detail.uid], { destination, uid_validity: detail.uid_validity }, detail.folder)
              }
              onAction={(action) =>
                void act(action, [detail.uid], { uid_validity: detail.uid_validity }, detail.folder)
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
          draftsEnabled={canReadMailbox}
          onClose={closeCompose}
          onSent={(message) => {
            setNotice(null);
            setSuccessNotice(message);
            setSuccessVisible(true);
            void loadList();
            if (conversationMode) conversations.reload();
            if (thread) void refreshConversation();
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
  sentMessage,
  showRemote,
  onBack,
  onThread,
  onLoadRemote,
  onTrustRemote,
  onReply,
  canManage,
  canSend,
  folders,
  labels,
  onApplyLabel,
  scheduledRow,
  onRescheduleScheduled,
  onCancelScheduled,
  onMove,
  onAction,
}: {
  detail: MessageDetail;
  summary: MessageSummary | null;
  sentMessage: boolean;
  showRemote: boolean;
  onBack: () => void;
  onThread?: () => void;
  onLoadRemote: () => void;
  onTrustRemote: () => void;
  onReply: (mode: "reply" | "reply-all" | "forward") => void;
  canManage: boolean;
  canSend: boolean;
  folders: Folder[];
  labels: MailLabel[];
  onApplyLabel: (id: string, remove: boolean) => void;
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
        <button
          type="button"
          className="pb-btn pb-btn-plain"
          aria-label="Back to mailbox"
          onClick={onBack}
        >
          <ArrowLeft className="h-[19px] w-[19px]" aria-hidden="true" />
          <span>Back</span>
        </button>
        <span className="pb-premium-toolbar-divider" aria-hidden="true" />
        {canManage && (
          <>
            {!sentMessage && (
              <ToolbarButton label="Archive" icon={Archive} onClick={() => onAction("archive")} />
            )}
            <ToolbarButton
              label={isTrash ? "Permanently delete" : "Move to Trash"}
              icon={Trash2}
              onClick={() => {
                if (!isTrash || window.confirm("Permanently delete this message? This cannot be undone.")) {
                  onAction(isTrash ? "delete" : "trash");
                }
              }}
            />
            {!sentMessage && (
              <ToolbarButton
                label={isSpam ? "Not spam" : "Mark as spam"}
                icon={isSpam ? ShieldCheck : ShieldAlert}
                onClick={() => onAction(isSpam ? "not-spam" : "spam")}
              />
            )}
            {!sentMessage && (
              <ToolbarButton label="Mark unread" icon={Mail} onClick={() => onAction("unread")} />
            )}
            <MoveMenu
              folders={folders}
              currentFolder={detail.folder}
              sentMessage={sentMessage}
              onMove={onMove}
            />
            <LabelMenu labels={labels} currentLabels={detail.labels}
              onApply={onApplyLabel} />
            {isTrash && (
              <ToolbarButton label="Restore" icon={RotateCcw} onClick={() => onAction("restore")} />
            )}
          </>
        )}
        <span className="flex-1" />
        {onThread && (
          <button type="button" className="pb-btn pb-btn-ghost" onClick={onThread}
            aria-label="Return to email thread">Email Thread</button>
        )}
        <a
          className="pb-btn pb-btn-plain"
          href={postbox.rawUrl(detail.folder, detail.uid, detail.uid_validity)}
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
              disabled={!canManage}
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
        <div className="pb-single-source-actions">
          <MessageHeaders key={detail.folder + ":" + detail.uid_validity + ":" + detail.uid}
            folder={detail.folder} uid={detail.uid} uidValidity={detail.uid_validity} />
        </div>

        {detail.remote_images_blocked && !showRemote && (
          <div className="pb-premium-privacy-banner">
            <ImageOff className="h-[18px] w-[18px]" aria-hidden="true" />
            <div>
              <strong>External images are hidden</strong>
              <p>To protect your privacy, images from this sender are blocked.</p>
              <div className="pb-premium-privacy-actions">
                <button type="button" onClick={onLoadRemote}>Display images</button>
                {canManage && detail.from.address && (
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
              <LinkifiedPlainText
                text={detail.text || "(This message has no readable content.)"}
              />
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
                          detail.uid_validity,
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
                        detail.uid_validity,
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

        {canSend && !isSpam && !isTrash && (
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

function MessageLabelBadges({ labels, palette }: { labels?: MessageLabel[]; palette?: MailLabel[] }) {
  if (!labels?.length) return null;
  return (
    <span className="pb-message-labels">
      {labels.map((item) => <span key={item.id} className="pb-message-label"
        style={{ "--pb-label-color": palette?.find((available) => available.id === item.id)?.color || item.color || "#9333ea" } as CSSProperties}>
        <Tag size={10} aria-hidden="true" /><span>{item.name}</span>
      </span>)}
    </span>
  );
}

function LabelMenu({
  labels, currentLabels = [], onApply, disabled = false,
}: {
  labels: MailLabel[];
  currentLabels?: MessageLabel[];
  onApply: (id: string, remove: boolean) => void;
  disabled?: boolean;
}) {
  const active = new Set(currentLabels.map((label) => label.id));
  return (
    <details className="pb-label-menu">
      <summary className="pb-btn pb-btn-plain" aria-label="Add label"
        aria-disabled={disabled}
        onClick={(event) => { if (disabled) event.preventDefault(); }}>
        <Tag size={16} aria-hidden="true" />
        <span className="hidden sm:inline">Add Label</span>
      </summary>
      <div className="pb-label-panel">
        <p className="pb-subtle text-xs p-2">Add or remove labels</p>
        {labels.length === 0 && <p className="pb-subtle text-xs p-2">
          Use + in the Labels sidebar to create one.
        </p>}
        {labels.map((item) => (
          <button type="button" key={item.id} onClick={(event) => {
            event.currentTarget.closest("details")!.open = false;
            onApply(item.id, active.has(item.id));
          }}>
            <Tag size={14} className="pb-colored-tag" style={{ color: item.color || "#9333ea" }} />
            {item.name}
            {active.has(item.id) && <CheckCircle2 size={14} aria-label="Applied" />}
          </button>
        ))}
      </div>
    </details>
  );
}

function MoveMenu({
  folders,
  currentFolder,
  onMove,
  sentMessage = false,
  disabled = false,
}: {
  folders: Folder[];
  currentFolder: string;
  onMove: (destination: string) => void;
  sentMessage?: boolean;
  disabled?: boolean;
}) {
  const destinations = folders.filter((item) => {
    if (item.name === currentFolder) return false;
    if (sentMessage) {
      // Outgoing mail may be filed in ordinary user folders and, once filed,
      // moved back to the real Sent folder. It never moves into Inbox,
      // Archive, Spam/Junk, Drafts or Scheduled.
      return !item.role || item.role === "sent";
    }
    return !new Set(["sent", "drafts", "scheduled"]).has(item.role);
  });
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
            {item.color && <FolderIcon size={14} style={{ color: item.color }} />}
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
