"use client";

/**
 * The composer: new, reply, reply-all and forward.
 *
 * Drafts autosave to the real Drafts IMAP folder, debounced, and the returned
 * UID replaces the one being edited — so editing a draft does not leave a
 * trail of abandoned copies, and what is saved survives a closed browser.
 *
 * `From` is a select over identities the SERVER says are permitted. It is not
 * a free-text field, and the server refuses anything else anyway; the select
 * exists so a person sees their options rather than guessing.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Clock,
  Loader2,
  Maximize2,
  Minimize2,
  Paperclip,
  Send,
  Trash2,
  X,
} from "lucide-react";

import {
  fileToBase64,
  formatBytes,
  postbox,
  type Identity,
  type Signature,
} from "@/lib/postbox-api";
import { describePostBoxError } from "@/contexts/postbox-context";

export interface ComposeInitial {
  mode: "new" | "reply" | "reply-all" | "forward" | "draft";
  to?: string[];
  cc?: string[];
  /**
   * Present so a composer opened with Bcc already set shows the field.
   *
   * Nothing populates it today: Bcc is envelope-only — `build_message` never
   * writes a Bcc header, precisely so the copy in Sent cannot disclose the
   * people it exists to hide — so a reopened draft has no Bcc to restore. The
   * field exists because the visibility rule below reads it, and reading a
   * property that cannot exist is how a later change to drafts silently fails
   * to show a recipient.
   */
  bcc?: string[];
  subject?: string;
  text?: string;
  html?: string;
  in_reply_to?: string;
  references?: string[];
  from_address?: string;
  draft_uid?: number;
}

interface PendingAttachment {
  filename: string;
  content_type: string;
  data: string;
  size: number;
}

export function Compose({
  initial,
  identities,
  signatures,
  onClose,
  onSent,
}: {
  initial: ComposeInitial;
  identities: Identity[];
  signatures: Signature[];
  onClose: () => void;
  onSent: (message: string) => void;
}) {
  const primary =
    initial.from_address ||
    identities.find((i) => i.is_primary)?.address ||
    identities[0]?.address ||
    "";

  const [from, setFrom] = useState(primary);
  const [to, setTo] = useState((initial.to ?? []).join(", "));
  const [cc, setCc] = useState((initial.cc ?? []).join(", "));
  // From `initial` like `to` and `cc`. It was hard-coded to "" while
  // `initial.bcc` was read only to decide whether the row was VISIBLE —
  // so a Bcc that did arrive would open the field and show it empty.
  const [bcc, setBcc] = useState((initial.bcc ?? []).join(", "));
  /**
   * Whether the Cc/Bcc rows are VISIBLE — not whether they exist.
   *
   * `cc` and `bcc` live in their own state and are always in the payload,
   * so collapsing the rows hides them and keeps them. The previous control
   * could only open: once shown there was no way back, and the two rows
   * stayed for the rest of the message.
   *
   * Opens expanded when a reply or draft already carries either, because a
   * recipient nobody can see is worse than a slightly taller header.
   */
  const [showCopies, setShowCopies] = useState(
    Boolean(initial.cc?.length || initial.bcc?.length),
  );

  /**
   * Compact bottom-right window, or the large centred one.
   *
   * Deliberately a class swap on the SAME element tree rather than two
   * different renders: React keeps the component mounted, so recipients,
   * subject, body, attachments, the signature choice, the schedule time and
   * the draft UID all survive the toggle. Rendering a different subtree per
   * size would remount the form and lose every one of them.
   */
  const [expanded, setExpanded] = useState(false);
  const [subject, setSubject] = useState(initial.subject ?? "");
  const [body, setBody] = useState(initial.text ?? "");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [signatureId, setSignatureId] = useState<string>(
    signatures.find((s) =>
      initial.mode === "new" ? s.use_for_new : s.use_for_replies,
    )?.id ?? "",
  );

  // Derived, so changing the selector updates the preview with no effect
  // and no second copy of the signature in state.
  const selectedSignature =
    signatures.find((candidate) => candidate.id === signatureId) ?? null;

  const [draftUid, setDraftUid] = useState<number | null>(initial.draft_uid ?? null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [scheduleAt, setScheduleAt] = useState("");
  const [showSchedule, setShowSchedule] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = useRef(false);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const payload = useCallback(
    () => ({
      from_address: from,
      to: splitAddresses(to),
      cc: splitAddresses(cc),
      bcc: splitAddresses(bcc),
      subject,
      text: body,
      in_reply_to: initial.in_reply_to ?? "",
      references: initial.references ?? [],
      signature_id: signatureId || null,
      attachments: attachments.map(({ filename, content_type, data }) => ({
        filename,
        content_type,
        data,
      })),
      draft_uid: draftUid,
    }),
    [from, to, cc, bcc, subject, body, signatureId, attachments, draftUid, initial],
  );

  // Debounced autosave. Two seconds is long enough that typing a sentence is
  // one save rather than twenty, and short enough that a closed tab loses at
  // most a sentence.
  useEffect(() => {
    if (!dirty.current) return;
    const timer = window.setTimeout(async () => {
      try {
        const saved = await postbox.saveDraft(payload());
        setDraftUid(saved.uid || null);
        setSavedAt(saved.saved_at);
      } catch {
        // Autosave failures are silent by design: an error toast every two
        // seconds while somebody types is worse than a draft that is a little
        // behind. An explicit save surfaces the problem.
      }
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [payload]);

  const markDirty = () => {
    dirty.current = true;
  };

  const addFiles = useCallback(async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    const added: PendingAttachment[] = [];
    for (const file of Array.from(files)) {
      try {
        added.push({
          filename: file.name,
          content_type: file.type || "application/octet-stream",
          data: await fileToBase64(file),
          size: file.size,
        });
      } catch {
        setError(`${file.name} could not be attached.`);
      }
    }
    setAttachments((current) => [...current, ...added]);
    markDirty();
  }, []);

  const submit = useCallback(
    async (schedule: boolean) => {
      setError(null);
      const recipients = [
        ...splitAddresses(to),
        ...splitAddresses(cc),
        ...splitAddresses(bcc),
      ];
      if (recipients.length === 0) {
        setError("Add at least one recipient.");
        return;
      }
      if (schedule && !scheduleAt) {
        setError("Choose when to send.");
        return;
      }

      setBusy(true);
      try {
        const result = await postbox.send({
          ...payload(),
          send_at: schedule ? new Date(scheduleAt).toISOString() : null,
        });
        if (result.scheduled) {
          onSent("Message scheduled.");
        } else {
          const firstRecipient = recipients[0];
          const more = recipients.length > 1 ? ` +${recipients.length - 1} more` : "";
          onSent(`Message sent to ${firstRecipient}${more}`);
        }
        onClose();
      } catch (caught) {
        setError(describePostBoxError(caught, "Your message could not be sent."));
      } finally {
        setBusy(false);
      }
    },
    [to, cc, bcc, scheduleAt, payload, onSent, onClose],
  );

  const discard = useCallback(async () => {
    if (draftUid) {
      try {
        await postbox.deleteDraft(draftUid);
      } catch {
        // Nothing useful to say: the composer is closing either way.
      }
    }
    onClose();
  }, [draftUid, onClose]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const totalBytes = attachments.reduce((sum, a) => sum + a.size, 0);

  return (
    <div
      className={`pb-compose-backdrop fixed inset-0 z-50 flex items-stretch justify-center p-0 ${
        expanded
          ? "sm:items-center sm:justify-center sm:p-6"
          : "sm:items-end sm:justify-end sm:p-4"
      }`}
      /*
        A NON-MODAL dialog, and the two facts below are the same fact.

        Dimmed on mobile, where Compose genuinely is the screen. From `sm` up
        `.pb-compose-backdrop` turns transparent and click-through, so the
        mailbox stays visible and usable behind it — a composer is a window you
        work beside, and blacking out the mail you are replying to helps
        nobody. The panel's own shadow and border carry the elevation.

        Which is why there is no `aria-modal`. The page behind really is
        interactive, and `aria-modal="true"` tells a screen reader to hide the
        rest of the document from its user — claiming a modality we do not
        enforce would make PostBox unreachable for exactly the people who
        cannot see that it is still there.

        `role="dialog"` stays: it is a dialog, it has a name, it is dismissible.
        It is simply not a modal one.
      */
      role="dialog"
      aria-label="Compose message"
    >
      <div
        className={`pb-panel pb-compose-shell flex w-full flex-col ${
          expanded
            ? // Underscores, not spaces: Tailwind arbitrary values cannot contain
            // spaces, and `calc` is invalid without them around the operator.
            // Written as `calc(100vh-3rem)` the class is silently dropped at
            // build time and expanding resizes nothing — which is exactly what
            // happened, and what a source-string test would never have caught.
            "sm:h-[calc(100vh_-_3rem)] sm:w-[calc(100vw_-_3rem)]"
            : "sm:h-[min(42rem,90vh)] sm:w-[min(40rem,95vw)]"
        }`}
        style={{ boxShadow: "var(--pb-shadow-lg)" }}
      >
        <div
          className="flex shrink-0 items-center justify-between border-b px-3 py-2"
          style={{ borderColor: "var(--pb-border)", background: "var(--pb-surface-2)" }}
        >
          <p className="text-sm font-semibold">
            {initial.mode === "forward"
              ? "Forward"
              : initial.mode.startsWith("reply")
                ? "Reply"
                : "New message"}
          </p>
          <div className="flex items-center gap-1">
            {savedAt && <span className="text-xs pb-subtle">Draft saved</span>}
            {/*
              Hidden below `sm`, where Compose already fills the screen and
              there is nothing to expand into. Wrapped in a div because
              `hidden sm:block` on a `.pb-btn` would not apply — Tailwind v4
              puts utilities in a cascade layer and `.pb-btn` is unlayered,
              so its `display` wins. Same reason as the sidebar's close
              button.
            */}
            <div className="hidden sm:block">
              <button
                type="button"
                className="pb-btn pb-btn-plain"
                aria-label={expanded ? "Restore compose" : "Expand compose"}
                title={expanded ? "Restore down" : "Expand"}
                aria-pressed={expanded}
                onClick={() => setExpanded((current) => !current)}
              >
                {expanded ? (
                  <Minimize2 className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <Maximize2 className="h-4 w-4" aria-hidden="true" />
                )}
              </button>
            </div>
            <button
              type="button"
              className="pb-btn pb-btn-plain"
              aria-label="Close"
              title="Close"
              onClick={onClose}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* `flex flex-col` so the body textarea can claim the leftover height
            when Compose is expanded; `min-h-0` so this pane scrolls instead of
            pushing the footer actions off the panel. */}
        <div className="pb-scroll flex min-h-0 flex-1 flex-col p-3">
          <div className="space-y-2">
            <Field label="From" htmlFor="pb-from">
              <select
                id="pb-from"
                className="pb-select"
                value={from}
                onChange={(event) => {
                  setFrom(event.target.value);
                  markDirty();
                }}
              >
                {identities.map((identity) => (
                  <option key={identity.address} value={identity.address}>
                    {identity.address}
                    {identity.kind === "alias" ? " (alias)" : ""}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="To" htmlFor="pb-to">
              <div className="flex gap-2">
                <input
                  id="pb-to"
                  className="pb-input"
                  value={to}
                  onChange={(event) => {
                    setTo(event.target.value);
                    markDirty();
                  }}
                  placeholder="name@example.com, another@example.com"
                  autoComplete="off"
                />
                <button
                  type="button"
                  className="pb-btn pb-btn-plain shrink-0"
                  aria-expanded={showCopies}
                  aria-controls="pb-copies"
                  aria-label={
                    showCopies
                      ? "Hide Cc and Bcc fields"
                      : "Show Cc and Bcc fields"
                  }
                  onClick={() => setShowCopies((current) => !current)}
                >
                  Cc/Bcc
                  {showCopies ? (
                    <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                </button>
              </div>
            </Field>

            {/*
              Hidden, not discarded. `cc` and `bcc` stay in state and in the
              payload while collapsed, so a value typed and then hidden is
              still sent — and still saved into the draft.
            */}
            {showCopies && (
              <div id="pb-copies" className="space-y-2">
                <Field label="Cc" htmlFor="pb-cc">
                  <input
                    id="pb-cc"
                    className="pb-input"
                    value={cc}
                    onChange={(event) => {
                      setCc(event.target.value);
                      markDirty();
                    }}
                    autoComplete="off"
                  />
                </Field>
                <Field label="Bcc" htmlFor="pb-bcc">
                  <input
                    id="pb-bcc"
                    className="pb-input"
                    value={bcc}
                    onChange={(event) => {
                      setBcc(event.target.value);
                      markDirty();
                    }}
                    autoComplete="off"
                  />
                </Field>
              </div>
            )}

            <Field label="Subject" htmlFor="pb-subject">
              <input
                id="pb-subject"
                className="pb-input"
                value={subject}
                onChange={(event) => {
                  setSubject(event.target.value);
                  markDirty();
                }}
              />
            </Field>
          </div>

          <textarea
            id="pb-body"
            aria-label="Message"
            className="pb-textarea pb-compose-body mt-3"
            value={body}
            onChange={(event) => {
              setBody(event.target.value);
              markDirty();
            }}
          />

          {/*
            The signature, shown but NOT editable and NOT part of `body`.

            Keeping it outside the textarea is the point: if it were
            inserted into the body, editing around it would corrupt it,
            deleting it would not clear the selection, and the backend —
            which appends the signature itself — would send it twice.
            What is submitted is the body alone plus a signature id.
          */}
          {selectedSignature && (
            <div className="mt-2" aria-label="Signature preview">
              <div
                className="mb-2"
                style={{ borderTop: "1px solid var(--pb-border)" }}
              />
              <SignaturePreview signature={selectedSignature} />
            </div>
          )}

          {signatures.length > 0 && (
            <div className="mt-2 flex items-center gap-2">
              <label htmlFor="pb-signature" className="pb-label">
                Signature
              </label>
              <select
                id="pb-signature"
                className="pb-select"
                style={{ width: "auto" }}
                value={signatureId}
                onChange={(event) => {
                  setSignatureId(event.target.value);
                  markDirty();
                }}
              >
                <option value="">None</option>
                {signatures.map((signature) => (
                  <option key={signature.id} value={signature.id}>
                    {signature.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {attachments.length > 0 && (
            <ul className="mt-3 space-y-1">
              {attachments.map((attachment, index) => (
                <li
                  key={`${attachment.filename}-${index}`}
                  className="flex items-center gap-2 border px-2 py-1 text-xs"
                  style={{ borderColor: "var(--pb-border)" }}
                >
                  <Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{attachment.filename}</span>
                  <span className="pb-subtle pb-num">{formatBytes(attachment.size)}</span>
                  <button
                    type="button"
                    className="pb-btn pb-btn-plain"
                    aria-label={`Remove ${attachment.filename}`}
                    onClick={() =>
                      setAttachments((current) =>
                        current.filter((_, i) => i !== index),
                      )
                    }
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </li>
              ))}
              <li className="text-xs pb-subtle pb-num">
                {formatBytes(totalBytes)} total
              </li>
            </ul>
          )}

          {showSchedule && (
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <div>
                <label htmlFor="pb-schedule" className="pb-label">
                  Send at
                </label>
                <input
                  id="pb-schedule"
                  className="pb-input mt-1"
                  type="datetime-local"
                  value={scheduleAt}
                  onChange={(event) => setScheduleAt(event.target.value)}
                />
              </div>
              <button
                type="button"
                className="pb-btn pb-btn-primary"
                disabled={busy}
                onClick={() => void submit(true)}
              >
                Schedule
              </button>
            </div>
          )}

          {error && (
            <p className="mt-3 text-xs" role="alert" style={{ color: "var(--pb-danger)" }}>
              {error}
            </p>
          )}
        </div>

        <div
          className="flex shrink-0 flex-wrap items-center gap-2 border-t px-3 py-2"
          style={{ borderColor: "var(--pb-border)" }}
        >
          <button
            type="button"
            className="pb-btn pb-btn-primary"
            disabled={busy}
            onClick={() => void submit(false)}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Send className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            Send
          </button>

          <input
            ref={fileInput}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => void addFiles(event.target.files)}
          />
          <button
            type="button"
            className="pb-btn pb-btn-ghost"
            onClick={() => fileInput.current?.click()}
          >
            <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
            Attach
          </button>

          <button
            type="button"
            className="pb-btn pb-btn-ghost"
            aria-pressed={showSchedule}
            onClick={() => setShowSchedule((open) => !open)}
          >
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            Schedule
          </button>

          <button
            type="button"
            className="pb-btn pb-btn-plain ml-auto"
            onClick={() => void discard()}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            Discard
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The selected signature, as the recipient will see it.
 *
 * Renders the SERVER's stored value — `signature.html` came back from the
 * API already sanitised, and `image_url` is served by it. The composer never
 * decides what is safe; it only shows what was stored.
 */
/**
 * Mark an image that failed to load.
 *
 * `error` does not bubble, but it does capture — so one handler on the wrapper
 * catches every image inside markup we injected and do not otherwise control.
 * The attribute is what `.pb-sig-canvas img[data-pb-broken]` styles into a
 * labelled box; CSS alone cannot know a request 404ed.
 *
 * Only the attribute is set. Replacing the node would fight React's ownership
 * of this subtree, and the alt text is already the right thing to show.
 */
function markBrokenImage(event: React.SyntheticEvent<HTMLElement>) {
  const target = event.target as HTMLElement | null;
  if (target?.tagName === "IMG") {
    target.setAttribute("data-pb-broken", "true");
  }
}

function SignaturePreview({ signature }: { signature: Signature }) {
  if (signature.kind === "image") {
    if (!signature.has_image) return null;
    return (
      <div className="pb-sig-canvas" onErrorCapture={markBrokenImage}>
        {/* User content from our own API; next/image would try to optimise
            and re-host it. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={signature.image_url}
          alt={signature.image_alt}
          style={{ maxWidth: "100%", maxHeight: "140px" }}
        />
      </div>
    );
  }

  if (signature.kind === "html") {
    return (
      <div
        className="pb-sig-canvas text-sm"
        onErrorCapture={markBrokenImage}
        dangerouslySetInnerHTML={{ __html: signature.html }}
      />
    );
  }

  return (
    <pre
      className="pb-sig-canvas text-sm"
      style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}
    >
      {signature.text}
    </pre>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
      <label htmlFor={htmlFor} className="pb-label sm:w-16 sm:shrink-0">
        {label}
      </label>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/**
 * Split a recipient field.
 *
 * Commas and semicolons both, because people paste from other mail clients and
 * Outlook uses semicolons. Empty entries are dropped so a trailing comma is not
 * an error.
 */
function splitAddresses(value: string): string[] {
  return value
    .split(/[,;]/)
    .map((part) => part.trim())
    .filter(Boolean);
}
