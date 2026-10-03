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
  Save,
  Send,
  Trash2,
  X,
} from "lucide-react";

import {
  fileToBase64,
  formatBytes,
  postbox,
  type ComposeAttachmentRef,
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
  quoted_text?: string;
  in_reply_to?: string;
  references?: string[];
  from_address?: string;
  draft_uid?: number;
  signature_id?: string | null;
  signature_missing?: boolean;
  existing_attachments?: ComposeAttachmentRef[];
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
  inline = false,
}: {
  initial: ComposeInitial;
  identities: Identity[];
  signatures: Signature[];
  onClose: () => void;
  onSent: (message: string) => void;
  inline?: boolean;
}) {
  // Two composers may coexist (inline reply and sidebar Compose). Form labels
  // and ARIA references must remain unique instead of targeting the other one.
  const fieldId = (part: string) => (inline ? "pb-thread-" : "pb-") + part;

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
  // The original is separate from editable text, including after reopening a draft.
  const [includeOriginal, setIncludeOriginal] = useState(
    initial.mode === "draft" && Boolean(initial.quoted_text),
  );
  const [previewOriginal, setPreviewOriginal] = useState(false);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [existingAttachments, setExistingAttachments] = useState<ComposeAttachmentRef[]>(
    initial.existing_attachments ?? [],
  );
  const attachmentRevision = useRef(0);
  const editRevision = useRef(0);
  const [signatureId, setSignatureId] = useState<string>(
    initial.signature_id ??
      signatures.find((signature) =>
        initial.mode === "new"
          ? signature.use_for_new
          : signature.use_for_replies,
      )?.id ??
      "",
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
  const [saving, setSaving] = useState(false);
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
      quoted_text: includeOriginal ? initial.quoted_text ?? "" : "",
      in_reply_to: initial.in_reply_to ?? "",
      references: initial.references ?? [],
      signature_id: signatureId || null,
      attachments: attachments.map(({ filename, content_type, data }) => ({
        filename,
        content_type,
        data,
      })),
      existing_attachments: existingAttachments.map(
        ({ folder, uid, uid_validity, part_id }) => ({
          folder,
          uid,
          uid_validity,
          part_id,
        }),
      ),
      draft_uid: draftUid,
    }),
    [
      from,
      to,
      cc,
      bcc,
      subject,
      body,
      includeOriginal,
      signatureId,
      attachments,
      existingAttachments,
      draftUid,
      initial,
    ],
  );

  const markDirty = () => {
    editRevision.current += 1;
    dirty.current = true;
  };

  const applySavedDraft = useCallback(
    (
      saved: {
        uid: number;
        uid_validity: number;
        saved_at: string;
        attachments: ComposeAttachmentRef[];
      },
      attachmentRevisionAtStart: number,
      editRevisionAtStart: number,
    ) => {
      setDraftUid(saved.uid || null);
      setSavedAt(saved.saved_at);
      if (attachmentRevision.current === attachmentRevisionAtStart) {
        setExistingAttachments(saved.attachments ?? []);
        setAttachments([]);
      }
      if (editRevision.current === editRevisionAtStart) {
        dirty.current = false;
      }
    },
    [],
  );

  const saveDraftNow = useCallback(
    async (closeAfter = false) => {
      const hasDraftMaterial = Boolean(
        to.trim() ||
          cc.trim() ||
          bcc.trim() ||
          subject.trim() ||
          body.trim() ||
          (includeOriginal && Boolean(initial.quoted_text)) ||
          attachments.length ||
          existingAttachments.length,
      );

      if (!dirty.current && draftUid) {
        if (closeAfter) onClose();
        return true;
      }
      if (!dirty.current && !draftUid && !hasDraftMaterial) {
        if (closeAfter) onClose();
        return true;
      }

      setSaving(true);
      setError(null);
      const attachmentRevisionAtStart = attachmentRevision.current;
      const editRevisionAtStart = editRevision.current;
      try {
        const saved = await postbox.saveDraft(payload());
        applySavedDraft(
          saved,
          attachmentRevisionAtStart,
          editRevisionAtStart,
        );
        if (closeAfter) onClose();
        return true;
      } catch (caught) {
        setError(describePostBoxError(caught, "Your draft could not be saved."));
        return false;
      } finally {
        setSaving(false);
      }
    },
    [
      applySavedDraft,
      attachments.length,
      bcc,
      body,
      cc,
      includeOriginal,
      initial.quoted_text,
      draftUid,
      existingAttachments.length,
      onClose,
      payload,
      subject,
      to,
    ],
  );

  // Debounced autosave. Two seconds is long enough that typing a sentence is
  // one save rather than twenty, and short enough that a closed tab loses at
  // most a sentence.
  useEffect(() => {
    if (!dirty.current || busy || saving) return;
    const timer = window.setTimeout(async () => {
      const attachmentRevisionAtStart = attachmentRevision.current;
      const editRevisionAtStart = editRevision.current;
      setSaving(true);
      try {
        const saved = await postbox.saveDraft(payload());
        applySavedDraft(
          saved,
          attachmentRevisionAtStart,
          editRevisionAtStart,
        );
      } catch {
        // Autosave stays quiet; manual Save draft surfaces any failure.
      } finally {
        setSaving(false);
      }
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [applySavedDraft, busy, payload, saving]);



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
    if (added.length > 0) attachmentRevision.current += 1;
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
      if (schedule) {
        const scheduled = new Date(scheduleAt);
        if (
          Number.isNaN(scheduled.getTime()) ||
          scheduled.getTime() <= Date.now()
        ) {
          setError("Choose a future date and time.");
          return;
        }
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
    if (inline) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy && !saving) {
        void saveDraftNow(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, inline, onClose, saveDraftNow, saving]);

  useEffect(() => {
    // Hard refresh, tab closure and cross-origin navigation must warn if
    // the inline reply has edits not yet confirmed by the draft API.
    // Same-app link navigation is guarded by the PostBox mailbox component.
    if (!inline) return;
    const protectUnsavedDraft = (event: BeforeUnloadEvent) => {
      if (!dirty.current && !saving) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protectUnsavedDraft);
    return () => window.removeEventListener("beforeunload", protectUnsavedDraft);
  }, [inline, saving]);

  const totalBytes =
    attachments.reduce((sum, attachment) => sum + attachment.size, 0) +
    existingAttachments.reduce((sum, attachment) => sum + attachment.size, 0);

  return (
    <div
      className={inline ? "pb-thread-compose-host" : `pb-compose-backdrop fixed inset-0 z-50 flex items-stretch justify-center p-0 ${
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
      role={inline ? "region" : "dialog"}
      aria-label={inline ? "Inline reply editor" : "Compose message"}
    >
      <div
        className={inline ? "pb-panel pb-compose-shell pb-premium-compose-shell pb-thread-inline-panel flex w-full flex-col" : `pb-panel pb-compose-shell pb-premium-compose-shell flex w-full flex-col ${
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
          className="pb-compose-head flex shrink-0 items-center justify-between border-b px-3 py-2"
          style={{ borderColor: "var(--pb-border)", background: "var(--pb-surface-2)" }}
        >
          <p className="text-sm font-semibold">
            {initial.mode === "forward"
              ? "Forward"
              : initial.mode.startsWith("reply")
                ? "Reply"
                : initial.mode === "draft"
                  ? "Edit draft"
                  : "New message"}
          </p>
          <div className="flex items-center gap-1">
            {saving ? (
              <span className="pb-compose-save-state">Saving draft…</span>
            ) : savedAt ? (
              <span className="pb-compose-save-state">Draft saved</span>
            ) : null}
            {/*
              Hidden below `sm`, where Compose already fills the screen and
              there is nothing to expand into. Wrapped in a div because
              `hidden sm:block` on a `.pb-btn` would not apply — Tailwind v4
              puts utilities in a cascade layer and `.pb-btn` is unlayered,
              so its `display` wins. Same reason as the sidebar's close
              button.
            */}
            {!inline && <div className="hidden sm:block">
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
            </div>}
            <button
              type="button"
              className="pb-btn pb-btn-plain"
              aria-label="Close"
              title="Close"
              onClick={() => void saveDraftNow(true)}
              disabled={saving || busy}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* `flex flex-col` so the body textarea can claim the leftover height
            when Compose is expanded; `min-h-0` so this pane scrolls instead of
            pushing the footer actions off the panel. */}
        <div className="pb-compose-content pb-scroll flex min-h-0 flex-1 flex-col p-3">
          <div className="pb-compose-fields space-y-2">
            <Field label="From" htmlFor={fieldId("from")}>
              <select
                id={fieldId("from")}
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

            <Field label="To" htmlFor={fieldId("to")}>
              <div className="flex gap-2">
                <RecipientInput
                  id={fieldId("to")}
                  label="To"
                  value={to}
                  onChange={(value) => {
                    setTo(value);
                    markDirty();
                  }}
                  placeholder="Name or email address"
                />
                <button
                  type="button"
                  className="pb-btn pb-btn-plain shrink-0"
                  aria-expanded={showCopies}
                  aria-controls={fieldId("copies")}
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
              <div id={fieldId("copies")} className="space-y-2">
                <Field label="Cc" htmlFor={fieldId("cc")}>
                  <RecipientInput
                    id={fieldId("cc")}
                    label="Cc"
                    value={cc}
                    onChange={(value) => {
                      setCc(value);
                      markDirty();
                    }}
                  />
                </Field>
                <Field label="Bcc" htmlFor={fieldId("bcc")}>
                  <RecipientInput
                    id={fieldId("bcc")}
                    label="Bcc"
                    value={bcc}
                    onChange={(value) => {
                      setBcc(value);
                      markDirty();
                    }}
                  />
                </Field>
              </div>
            )}

            <Field label="Subject" htmlFor={fieldId("subject")}>
              <input
                id={fieldId("subject")}
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
            id={fieldId("body")}
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
          {initial.signature_missing && (
            <p className="pb-compose-warning" role="status">
              The signature previously selected for this draft no longer exists.
              Choose another signature or send without one.
            </p>
          )}

          {selectedSignature && (
            <div className="pb-compose-signature mt-2" aria-label="Signature preview">
              <div
                className="mb-2"
                style={{ borderTop: "1px solid var(--pb-border)" }}
              />
              <SignaturePreview signature={selectedSignature} />
            </div>
          )}

          {signatures.length > 0 && (
            <div className="mt-2 flex items-center gap-2">
              <label htmlFor={fieldId("signature")} className="pb-label">
                Signature
              </label>
              <select
                id={fieldId("signature")}
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

          {Boolean(initial.quoted_text) && (
            <div
              className="mt-3 shrink-0 rounded-lg border px-3 py-2 text-sm"
              style={{ borderColor: "var(--pb-border)", background: "var(--pb-surface-2)" }}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="flex cursor-pointer items-center gap-2" htmlFor={fieldId("include-original")}>
                  <input
                    id={fieldId("include-original")}
                    type="checkbox"
                    className="h-4 w-4 accent-[var(--pb-primary)]"
                    checked={includeOriginal}
                    onChange={(event) => {
                      setIncludeOriginal(event.target.checked);
                      markDirty();
                    }}
                  />
                  Include original message
                </label>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
                  aria-expanded={previewOriginal}
                  aria-controls={fieldId("original-preview")}
                  onClick={() => setPreviewOriginal((current) => !current)}
                >
                  {previewOriginal ? "Hide preview" : "Preview original"}
                  {previewOriginal ? (
                    <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                </button>
              </div>
              {previewOriginal && (
                <pre
                  id={fieldId("original-preview")}
                  className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-words border-t pt-2 text-xs"
                  style={{ borderColor: "var(--pb-border)" }}
                >
                  {initial.quoted_text?.replace(/^> ?/gm, "")}
                </pre>
              )}
            </div>
          )}

          {(existingAttachments.length > 0 || attachments.length > 0) && (
              <div className="pb-compose-attachments">
                {[...existingAttachments, ...attachments].map((attachment, index) => {
                  const isExisting = index < existingAttachments.length;
                  return (
                    <div
                      key={
                        isExisting
                          ? "server-" +
                            (attachment as ComposeAttachmentRef).folder +
                            "-" +
                            (attachment as ComposeAttachmentRef).uid +
                            "-" +
                            (attachment as ComposeAttachmentRef).part_id
                          : "upload-" + attachment.filename + "-" + index
                      }
                      className="pb-compose-file-pill"
                    >
                      <Paperclip className="h-4 w-4 shrink-0" aria-hidden="true" />
                      <span>
                        <strong>{attachment.filename}</strong>
                        <small>{formatBytes(attachment.size)}</small>
                      </span>
                      <button
                        type="button"
                        aria-label={"Remove " + attachment.filename}
                        onClick={() => {
                          attachmentRevision.current += 1;
                          if (isExisting) {
                            setExistingAttachments((current) =>
                              current.filter((_, itemIndex) => itemIndex !== index),
                            );
                          } else {
                            const pendingIndex = index - existingAttachments.length;
                            setAttachments((current) =>
                              current.filter(
                                (_, itemIndex) => itemIndex !== pendingIndex,
                              ),
                            );
                          }
                          markDirty();
                        }}
                      >
                        <X className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    </div>
                  );
                })}
                <span className="pb-compose-attachment-total">
                  {formatBytes(totalBytes)} total
                </span>
              </div>
            )}

          {showSchedule && (
            <div className="pb-compose-schedule mt-3 flex flex-wrap items-end gap-2">
              <div>
                <label htmlFor={fieldId("schedule")} className="pb-label">
                  Send at
                </label>
                <input
                  id={fieldId("schedule")}
                  className="pb-input mt-1"
                  type="datetime-local"
                  value={scheduleAt}
                  onChange={(event) => setScheduleAt(event.target.value)}
                />
              </div>
              <button
                type="button"
                className="pb-btn pb-btn-primary"
                disabled={busy || saving}
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
          className="pb-compose-footer flex shrink-0 flex-wrap items-center gap-2 border-t px-3 py-2"
          style={{ borderColor: "var(--pb-border)" }}
        >
          <button
            type="button"
            className="pb-btn pb-btn-primary"
            disabled={busy || saving}
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
            onChange={(event) => {
              void addFiles(event.target.files);
              event.currentTarget.value = "";
            }}
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
            disabled={busy || saving}
            onClick={() => setShowSchedule((open) => !open)}
          >
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            Schedule
          </button>

                      <button
              type="button"
              className="pb-btn pb-btn-ghost pb-compose-save-draft"
              disabled={saving || busy}
              onClick={() => void saveDraftNow(false)}
            >
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Save className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Save draft
            </button>


          <button
            type="button"
            className="pb-btn pb-btn-plain ml-auto"
            onClick={() => void discard()}
            disabled={saving || busy}
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

function RecipientInput({
  id,
  label,
  value,
  onChange,
  placeholder = "",
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const [suggestions, setSuggestions] = useState<
    Array<{ name: string; email: string; source: string }>
  >([]);
  const [focused, setFocused] = useState(false);

  const suffix = value.split(/[,;]/).pop()?.trim() ?? "";
  useEffect(() => {
    if (suffix.length < 2) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      postbox
        .suggest(suffix)
        .then((result) => {
          if (!cancelled) setSuggestions(result.results);
        })
        .catch(() => {
          if (!cancelled) setSuggestions([]);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [suffix]);

  const visible = focused && suffix.length >= 2 && suggestions.length > 0;

  const choose = (email: string) => {
    const parts = value.split(/[,;]/);
    parts.pop();
    const prefix = parts.map((part) => part.trim()).filter(Boolean);
    onChange([...prefix, email].join(", ") + ", ");
    setFocused(true);
  };

  return (
    <div className="pb-recipient-input-wrap">
      <input
        id={id}
        className="pb-input"
        value={value}
        aria-label={label}
        role="combobox"
        aria-autocomplete="list"
        aria-haspopup="listbox"
        aria-expanded={visible}
        aria-controls={visible ? id + "-suggestions" : undefined}
        autoComplete="off"
        placeholder={placeholder}
        onFocus={() => setFocused(true)}
        onBlur={() => window.setTimeout(() => setFocused(false), 120)}
        onChange={(event) => onChange(event.target.value)}
      />
      {visible && (
        <div
          id={id + "-suggestions"}
          className="pb-recipient-suggestions"
          role="listbox"
          aria-label={label + " suggestions"}
        >
          {suggestions.map((suggestion) => (
            <button
              key={suggestion.source + "-" + suggestion.email}
              type="button"
              role="option"
              aria-selected="false"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(suggestion.email)}
            >
              <span>
                <strong>{suggestion.name || suggestion.email}</strong>
                {suggestion.name && <small>{suggestion.email}</small>}
              </span>
              <em>{suggestion.source === "contact" ? "Contact" : "Recent"}</em>
            </button>
          ))}
        </div>
      )}
    </div>
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
