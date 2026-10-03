"use client";

/**
 * Premium, read-only conversation renderer with the same safe MIME endpoints
 * as the single-message reader. Expanding a card fetches just that message's
 * sanitised body, never every body in a thread.
 *
 * The inline reply is the existing Compose lifecycle, not a second sender.
 * Its drafts, signature, attachments, scheduling and send error handling
 * therefore remain identical to the floating composer.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Archive, ArrowLeft, ChevronDown, ChevronUp, Download, Eye, Forward,
  ImageOff, Loader2, Mail, Paperclip, ShieldAlert, Star, Trash2,
  CornerUpLeft, CornerUpRight,
} from "lucide-react";
import { Compose, type ComposeInitial } from "@/components/postbox/compose";
import { resolveInlineImageReferences } from "@/lib/postbox-inline-images";
import { describePostBoxError } from "@/contexts/postbox-context";
import {
  formatBytes, formatMessageDate, postbox,
  type ConversationDetail, type ConversationMember, type Identity,
  type MessageAction, type MessageDetail, type Signature,
} from "@/lib/postbox-api";

type ReplyMode = "reply" | "reply-all" | "forward";

interface ConversationReaderProps {
  conversation: ConversationDetail;
  initialDetail: MessageDetail;
  identities: Identity[];
  signatures: Signature[];
  onBack: () => void;
  onSingle: () => void;
  onChanged: () => Promise<void>;
  onNotice: (notice: string) => void;
  onSuccess: (message: string) => void;
  onFloatingCompose: (initial: ComposeInitial) => void;
}

function memberKey(member: ConversationMember): string {
  return [member.folder, member.uid_validity, member.uid].join(":");
}

function isSameMessage(detail: MessageDetail, member: ConversationMember): boolean {
  return detail.folder === member.folder && detail.uid === member.uid &&
    detail.uid_validity === member.uid_validity;
}

/** Preserve the full body, but hide plain-text historical quote blocks by default. */
function splitPlainQuote(raw: string): { fresh: string; quoted: string } | null {
  const lines = raw.replace(/\r\n?/g, "\n").split("\n");
  const heading = lines.findIndex((line, i) =>
    i > 0 && /^On .{2,250} wrote:\s*$/i.test(line) &&
    lines.slice(i + 1, i + 3).some((later) => /^\s*>/.test(later)),
  );
  const block = lines.findIndex((line, i) =>
    i > 0 && /^\s*>/.test(line) &&
    lines.slice(i, i + 3).filter((candidate) => /^\s*>/.test(candidate)).length >= 2,
  );
  const candidates = [heading, block].filter((value) => value > 0);
  if (candidates.length === 0) return null;
  const index = Math.min(...candidates);
  const fresh = lines.slice(0, index).join("\n").trimEnd();
  if (!fresh.trim()) return null;
  return { fresh, quoted: lines.slice(index).join("\n") };
}

function initials(name: string): string {
  const bits = name.replace(/[<>]/g, " ").split(/\s+/).filter(Boolean);
  return (bits.length > 1 ? bits[0][0] + bits[1][0] : (bits[0] || "?").slice(0, 2)).toUpperCase();
}

export function ConversationReader({
  conversation, initialDetail, identities, signatures, onBack, onSingle,
  onChanged, onNotice, onSuccess, onFloatingCompose,
}: ConversationReaderProps) {
  const [inline, setInline] = useState<{
    key: string;
    initial: ComposeInitial;
  } | null>(null);
  const [preparing, setPreparing] = useState<string | null>(null);
  const [readState, setReadState] = useState<Record<string, boolean>>({});

  const startReply = useCallback(async (member: ConversationMember, mode: ReplyMode) => {
    if (inline) {
      onNotice("Save or close your current reply before starting another.");
      return;
    }
    const key = memberKey(member);
    setPreparing(key + mode);
    try {
      const context = await postbox.replyContext(member.folder, member.uid, mode, member.uid_validity);
      const initial: ComposeInitial = {
        mode, to: context.to, cc: context.cc, from_address: context.from_address,
        subject: context.subject, text: context.text,
        quoted_text: context.quoted_text, in_reply_to: context.in_reply_to,
        references: context.references,
        existing_attachments: mode === "forward" ? context.attachments : [],
      };
      if (mode === "forward") {
        onFloatingCompose(initial);
      } else {
        setInline({ key: key + mode, initial });
      }
    } catch (error) {
      onNotice(describePostBoxError(error, "That reply could not be prepared."));
    } finally {
      setPreparing(null);
    }
  }, [inline, onFloatingCompose, onNotice]);

  const memberAction = useCallback(async (
    member: ConversationMember, action: MessageAction,
  ) => {
    try {
      // A logical message may exist in Inbox and Archive. Clearing its star
      // or read state must not leave an invisible duplicate still flagged.
      const targets = action === "unstar" ? member.copies : [member];
      await Promise.all(targets.map((copy) => postbox.act(
        action, copy.folder, [copy.uid], { uid_validity: copy.uid_validity },
      )));
      if (action === "read" || action === "unread") {
        setReadState((previous) => ({
          ...previous,
          [memberKey(member)]: action === "read",
        }));
      }
      await onChanged();
    } catch (error) {
      onNotice(describePostBoxError(error, "The message could not be updated."));
    }
  }, [onChanged, onNotice]);

  const unreadCount = conversation.messages.reduce(
    (count, member) => count + (
      (readState[memberKey(member)] ?? member.seen) ? 0 : 1
    ), 0,
  );

  return (
    <article className="pb-thread-reader" aria-label="Email conversation">
      <div className="pb-premium-reader-toolbar pb-thread-toolbar">
        <button type="button" className="pb-btn pb-btn-plain" onClick={onBack}
          aria-label="Back to mailbox">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          <span>Back</span>
        </button>
        <span className="pb-thread-toolbar-divider" aria-hidden="true" />
        <span className="pb-thread-toolbar-caption">
          {conversation.message_count} messages
          {unreadCount > 0 && " · " + unreadCount + " unread"}
        </span>
        <span className="flex-1" />
        <button type="button" className="pb-btn pb-btn-ghost" onClick={onSingle}>
          Single message
        </button>
      </div>

      <div className="pb-thread-scroll pb-scroll">
        <header className="pb-thread-heading">
          <h1>{conversation.subject || "(no subject)"}</h1>
          <p>{conversation.message_count} messages in this conversation</p>
        </header>
        <ol className="pb-thread-cards" aria-label="Messages in conversation">
          {conversation.messages.map((member, index) => {
            const key = memberKey(member);
            return (
              <li key={key}>
                <ThreadMessageCard
                  member={member}
                  initialDetail={isSameMessage(initialDetail, member) ? initialDetail : null}
                  defaultOpen={index === conversation.messages.length - 1 ||
                    isSameMessage(initialDetail, member)}
                  seen={readState[key] ?? member.seen}
                  onSeen={() => {
                    setReadState((previous) => ({ ...previous, [key]: true }));
                    void onChanged();
                  }}
                  onAction={(action) => void memberAction(member, action)}
                  onReply={(mode) => void startReply(member, mode)}
                  preparing={Boolean(inline) || preparing === key + "reply" ||
                    preparing === key + "reply-all" || preparing === key + "forward"}
                  onNotice={onNotice}
                />
              </li>
            );
          })}
        </ol>
        <div className="pb-thread-inline-anchor">
          {inline ? (
            <Compose
              key={inline.key}
              initial={inline.initial}
              identities={identities}
              signatures={signatures}
              inline
              onClose={() => setInline(null)}
              onSent={(message) => {
                setInline(null);
                onSuccess(message);
                void onChanged();
              }}
            />
          ) : (
            <div className="pb-thread-reply-prompt">
              <span>Continue the conversation</span>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="pb-btn pb-btn-ghost"
                  disabled={Boolean(preparing)}
                  onClick={() => {
                    const last = conversation.messages[conversation.messages.length - 1];
                    if (last) void startReply(last, "reply");
                  }}>
                  <CornerUpLeft className="h-4 w-4" aria-hidden="true" /> Reply
                </button>
                <button type="button" className="pb-btn pb-btn-ghost"
                  disabled={Boolean(preparing)}
                  onClick={() => {
                    const last = conversation.messages[conversation.messages.length - 1];
                    if (last) void startReply(last, "reply-all");
                  }}>
                  <CornerUpRight className="h-4 w-4" aria-hidden="true" /> Reply all
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

function ThreadMessageCard({
  member, initialDetail, defaultOpen, seen, onSeen, onAction, onReply,
  preparing, onNotice,
}: {
  member: ConversationMember;
  initialDetail: MessageDetail | null;
  defaultOpen: boolean;
  seen: boolean;
  onSeen: () => void;
  onAction: (action: MessageAction) => void;
  onReply: (mode: ReplyMode) => void;
  preparing: boolean;
  onNotice: (message: string) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [detail, setDetail] = useState<MessageDetail | null>(initialDetail);
  const [loading, setLoading] = useState(false);
  const [remote, setRemote] = useState(false);
  const [working, setWorking] = useState(false);
  const marking = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!open || detail) return;
    let current = true;
    const load = async () => {
      setLoading(true);
      try {
        const result = await postbox.message(member.folder, member.uid, false, member.uid_validity);
        if (!current) return;
        if (result.uid_validity !== member.uid_validity) {
          onNotice("This folder changed. Refresh the conversation before opening this message.");
          return;
        }
        setDetail(result);
      } catch (error) {
        if (current) onNotice(describePostBoxError(error, "That message could not be opened."));
      } finally {
        if (current) setLoading(false);
      }
    };
    void load();
    return () => { current = false; };
  }, [open, detail, member.folder, member.uid, member.uid_validity, onNotice]);

  // A card is read only when the person explicitly expands it. This is not a
  // background "mark all read" when the thread's metadata is loaded.
  useEffect(() => {
    if (!open || !detail || seen || marking.current) return;
    marking.current = true;
    const mark = async () => {
      try {
        await Promise.all(member.copies.filter((copy) => !copy.seen).map((copy) =>
          postbox.act("read", copy.folder, [copy.uid], {
            uid_validity: copy.uid_validity,
          }),
        ));
        if (mounted.current) onSeen();
      } catch (error) {
        if (mounted.current) {
          onNotice(describePostBoxError(error, "Could not mark that message as read."));
        }
      } finally {
        marking.current = false;
      }
    };
    void mark();
  }, [open, detail, seen, member.copies, onSeen, onNotice]);

  const reloadImages = async (trust: boolean) => {
    if (!detail) return;
    setWorking(true);
    try {
      if (trust) {
        await postbox.trustRemoteImages(detail.folder, detail.uid, detail.uid_validity);
      }
      const result = await postbox.message(detail.folder, detail.uid, !trust, member.uid_validity);
      if (result.uid_validity !== member.uid_validity) {
        onNotice("This folder changed. Refresh the conversation before loading images.");
        return;
      }
      setDetail(result);
      setRemote(!trust);
    } catch (error) {
      onNotice(describePostBoxError(error, "Those images could not be loaded."));
    } finally {
      setWorking(false);
    }
  };

  const toggleStar = () => onAction(member.flagged ? "unstar" : "star");
  const attachments = detail?.attachments.filter((a) => !a.inline) ?? [];
  const safeHtml = detail ? resolveInlineImageReferences(detail) : "";
  const plainQuote = detail && !safeHtml ? splitPlainQuote(detail.text) : null;

  return (
    <section className="pb-thread-card" data-expanded={open} data-unread={!seen}>
      <div className="pb-thread-card-top">
        <button type="button" className="pb-thread-card-toggle"
          onClick={() => setOpen((previous) => !previous)}
          aria-expanded={open} aria-label={
            (open ? "Collapse" : "Expand") + " message from " +
            (member.from.name || member.from.address)
          }>
          <span className="pb-thread-avatar" aria-hidden="true">
            {initials(member.from.name || member.from.address)}
          </span>
          <span className="pb-thread-card-overview">
            <strong>{member.from.name || member.from.address || "Unknown sender"}</strong>
            <span className="pb-thread-card-excerpt">
              {open ? ("To: " + (member.to.join(", ") || "—")) :
                (member.subject || "(no subject)")}
            </span>
          </span>
          <time>{formatMessageDate(member.date)}</time>
          {open ? <ChevronUp className="h-4 w-4" aria-hidden="true" /> :
            <ChevronDown className="h-4 w-4" aria-hidden="true" />}
        </button>
        <button type="button" className="pb-thread-card-star"
          aria-label={member.flagged ? "Unstar message" : "Star message"}
          aria-pressed={member.flagged} onClick={toggleStar}>
          <Star className="h-4 w-4" aria-hidden="true"
            fill={member.flagged ? "currentColor" : "none"} />
        </button>
      </div>
      {open && (
        <div className="pb-thread-card-content">
          <details className="pb-thread-metadata">
            <summary>Message details</summary>
            <div>From: {member.from.name || member.from.address} &lt;{member.from.address}&gt;</div>
            <div>To: {member.to.join(", ") || "—"}</div>
            {member.cc.length > 0 && <div>Cc: {member.cc.join(", ")}</div>}
            <div>Folder: {member.folder}</div>
          </details>
          {loading && (
            <div className="pb-thread-loading" role="status">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading message…
            </div>
          )}
          {detail && <>
            {detail.remote_images_blocked && !remote && (
              <div className="pb-premium-privacy-banner pb-thread-privacy-banner">
                <ImageOff className="h-4 w-4 shrink-0" aria-hidden="true" />
                <div>
                  <strong>External images are hidden</strong>
                  <p>Display images only if you trust this sender.</p>
                  <div className="pb-premium-privacy-actions">
                    <button type="button" disabled={working}
                      onClick={() => void reloadImages(false)}>Display images</button>
                    {detail.from.address && <button type="button" disabled={working}
                      onClick={() => void reloadImages(true)}>
                      Always display images from this sender
                    </button>}
                  </div>
                </div>
              </div>
            )}
            <div className="pb-message-body pb-thread-mail-body">
              {safeHtml ? <div dangerouslySetInnerHTML={{ __html: safeHtml }} /> :
                plainQuote ? <>
                  <pre className="whitespace-pre-wrap">{plainQuote.fresh}</pre>
                  <details className="pb-thread-quoted-history">
                    <summary>Show quoted history</summary>
                    <pre className="whitespace-pre-wrap">{plainQuote.quoted}</pre>
                  </details>
                </> :
                <pre className="whitespace-pre-wrap">
                  {detail.text || "(This message has no readable content.)"}
                </pre>}
            </div>
            {attachments.length > 0 && <section className="pb-thread-attachments">
              <h3><Paperclip className="h-4 w-4" /> {attachments.length} attachment(s)</h3>
              <div className="pb-premium-attachment-grid">
                {attachments.map((item) => <div className="pb-premium-attachment-card"
                  key={item.part_id}>
                  <span className="pb-premium-file-icon"><Paperclip className="h-4 w-4" /></span>
                  <span className="min-w-0 flex-1">
                    <strong>{item.filename}</strong>
                    <small>{formatBytes(item.size)}</small>
                  </span>
                  <span className="pb-premium-attachment-actions">
                    {item.previewable && <a
                      href={postbox.attachmentPreviewUrl(detail.folder, detail.uid, item.part_id, detail.uid_validity)}
                      target="_blank" rel="noopener noreferrer" title="Preview attachment"
                      aria-label={"Preview " + item.filename}>
                      <Eye className="h-4 w-4" /></a>}
                    <a href={postbox.attachmentUrl(detail.folder, detail.uid, item.part_id, detail.uid_validity)}
                      download={item.filename} aria-label={"Download " + item.filename}>
                      <Download className="h-4 w-4" /></a>
                  </span>
                </div>)}
              </div>
            </section>}
          </>}
          <div className="pb-thread-card-actions">
            <button type="button" className="pb-btn pb-btn-ghost"
              disabled={preparing} onClick={() => onReply("reply")}>
              <CornerUpLeft className="h-4 w-4" /> Reply
            </button>
            <button type="button" className="pb-btn pb-btn-ghost"
              disabled={preparing} onClick={() => onReply("reply-all")}>
              <CornerUpRight className="h-4 w-4" /> Reply all
            </button>
            <button type="button" className="pb-btn pb-btn-ghost"
              disabled={preparing} onClick={() => onReply("forward")}>
              <Forward className="h-4 w-4" /> Forward
            </button>
            <button type="button" className="pb-btn pb-btn-plain"
              onClick={() => onAction(seen ? "unread" : "read")}>
              <Mail className="h-4 w-4" /> {seen ? "Mark unread" : "Mark read"}
            </button>
            <button type="button" className="pb-btn pb-btn-plain"
              onClick={() => onAction("archive")}>
              <Archive className="h-4 w-4" /> Archive
            </button>
            <button type="button" className="pb-btn pb-btn-plain"
              onClick={() => onAction("spam")}>
              <ShieldAlert className="h-4 w-4" /> Spam
            </button>
            <button type="button" className="pb-btn pb-btn-plain"
              onClick={() => onAction("trash")}>
              <Trash2 className="h-4 w-4" /> Trash
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
